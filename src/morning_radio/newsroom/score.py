from __future__ import annotations

import json
from pathlib import Path

from morning_radio.llm.client import LLMClient
from morning_radio.llm.prompts import SCORING_SYSTEM
from morning_radio.llm.schemas import StoryScoresResponse
from morning_radio.models import Cluster, EditorialProfile, StoryScore


def score_stories(
    clusters: list[Cluster],
    profile: EditorialProfile,
    run_dir: Path,
    llm: LLMClient | None = None,
) -> list[StoryScore]:
    if llm is not None:
        try:
            response = llm.generate_structured(
                SCORING_SYSTEM,
                json.dumps(
                    {
                        "profile": profile.model_dump(mode="json"),
                        "clusters": [item.model_dump(mode="json") for item in clusters],
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
                return _persist_scores(response.scores, run_dir)
        except Exception:
            if llm.model != "fake-local-fixture":
                raise
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
        if negative:
            final = max(0, final - 35)
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
    scores.sort(key=lambda item: item.final_score, reverse=True)
    return _persist_scores(scores, run_dir)


def _persist_scores(scores: list[StoryScore], run_dir: Path) -> list[StoryScore]:
    scores.sort(key=lambda item: item.final_score, reverse=True)
    (run_dir / "scored-stories.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in scores], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return scores
