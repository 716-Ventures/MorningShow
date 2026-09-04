from __future__ import annotations

import html
import json
import re
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
    Interest,
    ScoreModifier,
    StoryScore,
)

MAX_LLM_SCORING_PROMPT_CHARS = 60_000
MAX_LLM_SCORING_CLUSTERS = 18


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
    heuristic_scores = _build_heuristic_scores(
        clusters,
        profile,
        history,
        extraction_payload,
    )
    if llm is not None:
        heuristic_by_id = {score.cluster_id: score for score in heuristic_scores}
        llm_clusters = sorted(
            clusters,
            key=lambda cluster: heuristic_by_id[cluster.cluster_id].final_score,
            reverse=True,
        )[:MAX_LLM_SCORING_CLUSTERS]
        llm_cluster_ids = {cluster.cluster_id for cluster in llm_clusters}
        scoring_prompt = json.dumps(
            {
                "profile": profile.model_dump(mode="json"),
                "editorial_memory": editorial_memory,
                "clusters": [compact_cluster(item) for item in llm_clusters],
                "extraction_evidence": {
                    cluster_id: extraction_payload.get(cluster_id, [])
                    for cluster_id in llm_cluster_ids
                },
                "story_history": {
                    cluster.fingerprint: history[cluster.fingerprint]
                    for cluster in llm_clusters
                    if cluster.fingerprint in history
                },
                "required_cluster_ids": [item.cluster_id for item in llm_clusters],
            },
            ensure_ascii=False,
        )
        if len(scoring_prompt) > MAX_LLM_SCORING_PROMPT_CHARS:
            atomic_write_json(
                run_dir / "logs" / "scoring-fallback.json",
                {
                    "model": llm.model,
                    "reason": (
                        "Scoring prompt exceeded "
                        f"{MAX_LLM_SCORING_PROMPT_CHARS} characters."
                    ),
                    "input_character_count": len(scoring_prompt),
                    "fixture_fallback": allows_fixture_fallback(llm),
                },
            )
        else:
            return _score_with_llm(
                scoring_prompt,
                llm_clusters,
                profile,
                history,
                run_dir,
                llm,
                heuristic_scores,
            )
    return _persist_scores(heuristic_scores, run_dir)


def _score_with_llm(
    scoring_prompt: str,
    requested_clusters: list[Cluster],
    profile: EditorialProfile,
    history: dict[str, dict[str, str | int | None]],
    run_dir: Path,
    llm: LLMClient,
    fallback_scores: list[StoryScore],
) -> list[StoryScore]:
    try:
        response = llm.generate_structured(
            SCORING_SYSTEM,
            scoring_prompt,
            StoryScoresResponse,
            stage="scoring",
            prompt_type="story_scores",
        )
        expected = {item.cluster_id for item in requested_clusters}
        received = {item.cluster_id for item in response.scores}
        if expected <= received:
            model_scores = apply_score_modifiers(
                [score for score in response.scores if score.cluster_id in expected],
                requested_clusters,
                profile,
                history,
            )
            model_ids = {score.cluster_id for score in model_scores}
            return _persist_scores(
                [*model_scores, *(score for score in fallback_scores if score.cluster_id not in model_ids)],
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
    return _persist_scores(fallback_scores, run_dir)


def _score_with_heuristics(
    clusters: list[Cluster],
    profile: EditorialProfile,
    history: dict[str, dict[str, str | int | None]],
    run_dir: Path,
    extraction_payload: dict[str, list[dict[str, str | int | None]]],
) -> list[StoryScore]:
    return _persist_scores(
        _build_heuristic_scores(clusters, profile, history, extraction_payload),
        run_dir,
    )


def _build_heuristic_scores(
    clusters: list[Cluster],
    profile: EditorialProfile,
    history: dict[str, dict[str, str | int | None]],
    extraction_payload: dict[str, list[dict[str, str | int | None]]],
) -> list[StoryScore]:
    interest_terms = build_interest_terms(profile)
    negative_terms = [item.casefold() for item in profile.negative_preferences]
    scores: list[StoryScore] = []
    for cluster in clusters:
        title_haystack, context_haystack = cluster_haystacks(cluster, extraction_payload)
        matched_interests = matched_profile_interests(
            interest_terms,
            title_haystack,
            context_haystack,
        )
        matched = [interest.name for interest in matched_interests]
        negative = [
            name
            for name in negative_terms
            if term_matches(name, title_haystack) or term_matches(name, context_haystack)
        ]
        priority_boost = max((interest.priority for interest in matched_interests), default=0) * 10
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
    return apply_score_modifiers(scores, clusters, profile, history)


def compact_cluster(cluster: Cluster) -> dict[str, str | int | list[str] | None]:
    return {
        "cluster_id": cluster.cluster_id,
        "canonical_title": cluster.canonical_title,
        "source_count": cluster.source_count,
        "latest_published_at": cluster.latest_published_at.isoformat()
        if cluster.latest_published_at
        else None,
        "topic_hints": cluster.topic_hints,
    }


def build_interest_terms(profile: EditorialProfile) -> dict[str, Interest]:
    terms: dict[str, Interest] = {}
    for interest in profile.interests:
        for term in expand_interest_terms(interest.name, interest.subtopics):
            terms[term] = interest
    return terms


def matched_profile_interests(
    interest_terms: dict[str, Interest],
    title_haystack: str,
    context_haystack: str,
) -> list[Interest]:
    matched: list[Interest] = []
    seen: set[str] = set()
    for term, interest in interest_terms.items():
        key = interest.name.casefold()
        if key in seen:
            continue
        if term_matches(term, title_haystack) or (
            allow_context_interest_match(term) and term_matches(term, context_haystack)
        ):
            matched.append(interest)
            seen.add(key)
    return matched


def expand_interest_terms(name: str, subtopics: list[str]) -> set[str]:
    terms = {normalize_term(term) for term in [name, *subtopics] if normalize_term(term)}
    normalized_name = normalize_term(name)
    if normalized_name in {"ai", "artificial intelligence"}:
        terms.update(
            {
                "ai",
                "artificial intelligence",
                "generative ai",
                "machine learning",
                "openai",
                "anthropic",
                "llm",
                "agents",
                "chatbot",
                "data center",
                "data centre",
            }
        )
    if "apple" in normalized_name:
        terms.update(
            {
                "apple",
                "iphone",
                "ipad",
                "mac",
                "macos",
                "ios",
                "vision pro",
                "tim cook",
                "john ternus",
            }
        )
    if "bills" in normalized_name or normalized_name == "buffalo bills":
        terms.update({"buffalo bills", "bills"})
    return terms


def normalize_term(term: str) -> str:
    return " ".join(html.unescape(term).casefold().split())


def cluster_haystacks(
    cluster: Cluster,
    extraction_payload: dict[str, list[dict[str, str | int | None]]],
) -> tuple[str, str]:
    title_values: list[str] = [cluster.canonical_title]
    context_values: list[str] = []
    for evidence in extraction_payload.get(cluster.cluster_id, []):
        title = evidence.get("title")
        if isinstance(title, str):
            title_values.append(title)
        excerpt = evidence.get("excerpt")
        if isinstance(excerpt, str):
            context_values.append(excerpt)
    return normalize_term(" ".join(title_values)), normalize_term(" ".join(context_values))


def allow_context_interest_match(term: str) -> bool:
    return len(term) > 3 and term not in {"apple", "bills", "buffalo bills", "agents"}


def term_matches(term: str, haystack: str) -> bool:
    if not term:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


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
                "word_count": extraction_by_id[candidate_id].word_count,
                "excerpt": extraction_by_id[candidate_id].text[:500],
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
