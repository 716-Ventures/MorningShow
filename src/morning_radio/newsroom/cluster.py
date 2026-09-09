from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from morning_radio.artifacts.io import atomic_write_json
from morning_radio.llm.client import LLMClient, LLMError
from morning_radio.llm.schemas import SameEventDecision
from morning_radio.models import CandidateStory, Cluster, ExtractionResult

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "for",
    "on",
    "with",
    "as",
    "by",
    "from",
    "after",
    "over",
    "new",
    "today",
    "update",
}

PUBLISHER_SUFFIX = re.compile(
    r"\s+[-|]\s+(ap|associated press|reuters|bbc|cnn|npr|axios|the verge).*$", re.IGNORECASE
)
HIGH_CONFIDENCE_SIMILARITY = 0.72
AMBIGUOUS_SIMILARITY = 0.30
TIME_BLOCK_HOURS = 36


def title_tokens(title: str) -> set[str]:
    normalized = normalize_title(title)
    words = re.findall(r"[a-z0-9]+", normalized)
    return {_stem_token(word) for word in words if len(word) > 2 and word not in STOPWORDS}


def cluster_stories(
    candidates: list[CandidateStory],
    extractions: list[ExtractionResult],
    run_dir: Path,
    llm: LLMClient | None = None,
) -> list[Cluster]:
    usable_ids = {item.candidate_id for item in extractions if item.extraction_status == "usable"}
    by_id = {
        candidate.candidate_id: candidate
        for candidate in candidates
        if candidate.candidate_id in usable_ids
    }
    ordered_candidates = sorted(by_id.values(), key=lambda item: item.candidate_id)
    union_find = UnionFind([item.candidate_id for item in ordered_candidates])
    decisions: list[dict[str, str | bool | float]] = []

    for left_index, left in enumerate(ordered_candidates):
        for right in ordered_candidates[left_index + 1 :]:
            decision, similarity, error = should_merge(left, right, llm)
            decision_log: dict[str, str | bool | float] = {
                "left": left.candidate_id,
                "right": right.candidate_id,
                "same_event": decision,
                "similarity": round(similarity, 3),
            }
            if error is not None:
                decision_log["error"] = error
            decisions.append(decision_log)
            if decision:
                union_find.union(left.candidate_id, right.candidate_id)

    grouped: dict[str, list[CandidateStory]] = {}
    for candidate in ordered_candidates:
        grouped.setdefault(union_find.find(candidate.candidate_id), []).append(candidate)

    sorted_groups = sorted(
        grouped.values(),
        key=lambda items: (
            len(items),
            max(
                (item.published_at for item in items if item.published_at is not None),
                default=datetime.min.replace(tzinfo=UTC),
            ),
            canonical_title(items),
        ),
        reverse=True,
    )

    clusters = []
    for index, items in enumerate(sorted_groups, start=1):
        title = max(items, key=lambda item: len(item.title)).title
        published = [item.published_at for item in items if item.published_at is not None]
        hints = sorted({hint for item in items for hint in item.category_hints})
        fingerprint = hashlib.sha256(canonical_fingerprint_text(items).encode()).hexdigest()[:16]
        clusters.append(
            Cluster(
                cluster_id=f"cluster-{index:03d}",
                canonical_title=title,
                candidate_ids=sorted(item.candidate_id for item in items),
                source_count=len(items),
                earliest_published_at=min(published) if published else None,
                latest_published_at=max(published) if published else None,
                topic_hints=hints,
                fingerprint=fingerprint,
            )
        )
    atomic_write_json(
        run_dir / "clusters.json", [item.model_dump(mode="json") for item in clusters]
    )
    decisions_path = run_dir / "logs" / "cluster-decisions.json"
    atomic_write_json(decisions_path, decisions)
    return clusters


def normalize_title(title: str) -> str:
    title = PUBLISHER_SUFFIX.sub("", title)
    return re.sub(r"\s+", " ", title.lower()).strip()


def _stem_token(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("es") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and len(token) > 4:
        return token[:-1]
    return token


def token_similarity(left: CandidateStory, right: CandidateStory) -> float:
    left_tokens = title_tokens(left.title)
    right_tokens = title_tokens(right.title)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def should_merge(
    left: CandidateStory, right: CandidateStory, llm: LLMClient | None
) -> tuple[bool, float, str | None]:
    left_title = normalize_title(left.title)
    right_title = normalize_title(right.title)
    similarity = token_similarity(left, right)
    shared_tokens = title_tokens(left.title) & title_tokens(right.title)
    if left_title == right_title:
        return True, 1.0, None
    if similarity >= HIGH_CONFIDENCE_SIMILARITY and within_time_block(left, right):
        return True, similarity, None
    if (
        similarity < AMBIGUOUS_SIMILARITY
        or len(shared_tokens) < 2
        or not within_time_block(left, right)
    ):
        return False, similarity, None
    if llm is None:
        return False, similarity, None
    try:
        decision = llm.generate_structured(
            "Decide whether two news headlines describe the same real-world event.",
            json.dumps(
                {
                    "left": left.model_dump(mode="json"),
                    "right": right.model_dump(mode="json"),
                    "similarity": similarity,
                },
                ensure_ascii=False,
            ),
            SameEventDecision,
            stage="clustering",
            prompt_type="same_event",
        )
    except LLMError as exc:
        return False, similarity, f"LLM adjudication failed: {exc}"
    return decision.same_event, similarity, None


def within_time_block(left: CandidateStory, right: CandidateStory) -> bool:
    if left.published_at is None or right.published_at is None:
        return True
    delta = abs((left.published_at - right.published_at).total_seconds())
    return delta <= TIME_BLOCK_HOURS * 3600


def canonical_title(items: list[CandidateStory]) -> str:
    return max(items, key=lambda item: len(title_tokens(item.title))).title


def canonical_fingerprint_text(items: list[CandidateStory]) -> str:
    tokens = sorted({token for item in items for token in title_tokens(item.title)})
    return " ".join(tokens)


class UnionFind:
    def __init__(self, ids: list[str]) -> None:
        self.parent = {item_id: item_id for item_id in ids}

    def find(self, item_id: str) -> str:
        parent = self.parent[item_id]
        if parent != item_id:
            self.parent[item_id] = self.find(parent)
        return self.parent[item_id]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        winner, loser = sorted([left_root, right_root])
        self.parent[loser] = winner
