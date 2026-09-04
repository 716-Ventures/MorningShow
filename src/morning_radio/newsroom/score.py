from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from morning_radio import db
from morning_radio.artifacts.io import atomic_write_json
from morning_radio.llm.client import LLMClient, LLMError, allows_fixture_fallback
from morning_radio.llm.prompts import SCORING_SYSTEM
from morning_radio.llm.schemas import StoryScoresResponse
from morning_radio.models import (
    Cluster,
    EditorialProfile,
    ExtractionResult,
    ScoreModifier,
    StoryScore,
)


def score_stories(
    clusters: list[Cluster],
    profile: EditorialProfile,
    run_dir: Path,
    llm: LLMClient | None = None,
    *,
    extractions: list[ExtractionResult] | None = None,
    editorial_memory_path: Path | None = None,
    db_path: Path | None = None,
) -> list[StoryScore]:
    editorial_memory = load_editorial_memory(editorial_memory_path)
    history = load_story_history(db_path, clusters)
    extraction_payload = build_extraction_payload(clusters, extractions or [])
    if llm is not None:
        try:
            response = llm.generate_structured(
                SCORING_SYSTEM,
                json.dumps(
                    {
                        "profile": profile.model_dump(mode="json"),
                        "editorial_memory": editorial_memory,
                        "clusters": [item.model_dump(mode="json") for item in clusters],
                        "extraction_evidence": extraction_payload,
                        "story_history": history,
                        "required_cluster_ids": [item.cluster_id for item in clusters],
                    },
                    ensure_ascii=False,
                ),
                StoryScoresResponse,
                stage="scoring",
                prompt_type="story_scores",
            )
            expected = {item.cluster_id for item in clusters}
            received = {item.cluster_id for item in response.scores}
            if expected <= received:
                return _persist_scores(
                    apply_score_modifiers(response.scores, clusters, profile, history),
                    run_dir,
                )
        except (LLMError, ValidationError) as exc:
            atomic_write_json(
                run_dir / "logs" / "scoring-fallback.json",
                {
                    "model": llm.model,
                    "reason": str(exc),
                    "fixture_fallback": allows_fixture_fallback(llm),
                },
            )
    interest_terms = {
        term.lower(): item
        for item in profile.interests
        for term in [item.name, *item.subtopics]
        if term
    }
    negative_terms = [item.lower() for item in profile.negative_preferences]
    scores: list[StoryScore] = []
    for cluster in clusters:
        haystack = " ".join([cluster.canonical_title, *cluster.topic_hints]).lower()
        matched = [name for name in interest_terms if name in haystack]
        negative = [name for name in negative_terms if name in haystack]
        priority_boost = max((interest_terms[name].priority for name in matched), default=0) * 10
        relevance = min(100, 20 + priority_boost + len(matched) * 10)
        importance = min(100, 35 + cluster.source_count * 12)
        if any(hint in {"world", "us", "general"} for hint in cluster.topic_hints):
            importance += 15
        locality = 80 if any("buffalo" in hint.lower() or "western" in hint.lower() for hint in cluster.topic_hints) else 20
        freshness = 75 if cluster.latest_published_at else 50
        novelty = 70
        confidence = min(95, 50 + cluster.source_count * 15)
        final = round((relevance * 0.35) + (importance * 0.3) + (freshness * 0.15) + (locality * 0.1) + (novelty * 0.1))
        final = max(0, min(100, final))
        scores.append(
            StoryScore(
                cluster_id=cluster.cluster_id,
                relevance=relevance,
                importance=max(0, min(100, importance)),
                freshness=freshness,
                locality=locality,
                novelty=novelty,
                confidence=confidence,
                matched_interests=matched,
                negative_matches=negative,
                reason=f"Matched {matched or ['general news']} with {cluster.source_count} source(s).",
                final_score=final,
            )
        )
    return _persist_scores(apply_score_modifiers(scores, clusters, profile, history), run_dir)


def load_editorial_memory(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def load_story_history(db_path: Path | None, clusters: list[Cluster]) -> dict[str, dict[str, str | int | None]]:
    if db_path is None or not db_path.exists():
        return {}
    rows = db.read_story_history(db_path, [cluster.fingerprint for cluster in clusters])
    return {
        fingerprint: {
            "canonical_title": str(row["canonical_title"]),
            "first_seen_date": str(row["first_seen_date"]),
            "last_seen_date": str(row["last_seen_date"]),
            "last_included_date": row["last_included_date"],
            "include_count": int(row["include_count"]),
        }
        for fingerprint, row in rows.items()
    }


def build_extraction_payload(
    clusters: list[Cluster], extractions: list[ExtractionResult]
) -> dict[str, list[dict[str, str | int | None]]]:
    extraction_by_id = {item.candidate_id: item for item in extractions}
    payload: dict[str, list[dict[str, str | int | None]]] = {}
    for cluster in clusters:
        payload[cluster.cluster_id] = [
            {
                "candidate_id": candidate_id,
                "title": extraction_by_id[candidate_id].title,
                "url": extraction_by_id[candidate_id].url,
                "word_count": extraction_by_id[candidate_id].word_count,
                "excerpt": extraction_by_id[candidate_id].text[:800],
            }
            for candidate_id in cluster.candidate_ids
            if candidate_id in extraction_by_id
        ]
    return payload


def apply_score_modifiers(
    scores: list[StoryScore],
    clusters: list[Cluster],
    profile: EditorialProfile,
    history: dict[str, dict[str, str | int | None]],
) -> list[StoryScore]:
    cluster_by_id = {cluster.cluster_id: cluster for cluster in clusters}
    negative_terms = [item.lower() for item in profile.negative_preferences]
    adjusted: list[StoryScore] = []
    for score in scores:
        cluster = cluster_by_id.get(score.cluster_id)
        raw = score.final_score
        final = raw
        modifiers: list[ScoreModifier] = []
        if cluster is not None:
            haystack = " ".join([cluster.canonical_title, *cluster.topic_hints]).lower()
            matched_negative = sorted(term for term in negative_terms if term and term in haystack)
            if matched_negative:
                modifiers.append(
                    ScoreModifier(
                        name="negative_preference",
                        delta=-35,
                        rationale="Matched negative preference(s): " + ", ".join(matched_negative),
                    )
                )
            history_entry = history.get(cluster.fingerprint)
            if history_entry and int(history_entry.get("include_count") or 0) > 0:
                modifiers.append(
                    ScoreModifier(
                        name="repeat_story",
                        delta=-25,
                        rationale=(
                            "Story fingerprint was previously included on "
                            f"{history_entry.get('last_included_date')}."
                        ),
                    )
                )
        for modifier in modifiers:
            final += modifier.delta
        final = max(0, min(100, final))
        novelty = min(score.novelty, 25) if any(item.name == "repeat_story" for item in modifiers) else score.novelty
        adjusted.append(
            score.model_copy(
                update={
                    "raw_final_score": raw,
                    "final_score": final,
                    "novelty": novelty,
                    "modifiers": modifiers,
                }
            )
        )
    adjusted.sort(key=lambda item: item.final_score, reverse=True)
    return adjusted


def _persist_scores(scores: list[StoryScore], run_dir: Path) -> list[StoryScore]:
    scores.sort(key=lambda item: item.final_score, reverse=True)
    atomic_write_json(
        run_dir / "scored-stories.json",
        [item.model_dump(mode="json") for item in scores],
    )
    return scores
