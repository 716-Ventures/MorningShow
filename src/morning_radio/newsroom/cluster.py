from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from morning_radio.models import CandidateStory, Cluster, ExtractionResult

STOPWORDS = {"the", "a", "an", "and", "or", "to", "of", "in", "for", "on", "with", "as", "by"}


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {word for word in words if len(word) > 2 and word not in STOPWORDS}


def cluster_stories(
    candidates: list[CandidateStory], extractions: list[ExtractionResult], run_dir: Path
) -> list[Cluster]:
    usable_ids = {item.candidate_id for item in extractions if item.extraction_status == "usable"}
    by_id = {candidate.candidate_id: candidate for candidate in candidates if candidate.candidate_id in usable_ids}
    groups: dict[str, list[CandidateStory]] = defaultdict(list)
    for candidate in by_id.values():
        tokens = sorted(title_tokens(candidate.title))
        key_terms = tokens[:3] if tokens else [candidate.title.lower()[:24]]
        groups["-".join(key_terms)].append(candidate)
    clusters = []
    for index, items in enumerate(groups.values(), start=1):
        title = max(items, key=lambda item: len(item.title)).title
        published = [item.published_at for item in items if item.published_at is not None]
        hints = sorted({hint for item in items for hint in item.category_hints})
        fingerprint = hashlib.sha256(" ".join(sorted(title_tokens(title))).encode()).hexdigest()[:16]
        clusters.append(
            Cluster(
                cluster_id=f"cluster-{index:03d}",
                canonical_title=title,
                candidate_ids=[item.candidate_id for item in items],
                source_count=len(items),
                earliest_published_at=min(published) if published else None,
                latest_published_at=max(published) if published else None,
                topic_hints=hints,
                fingerprint=fingerprint,
            )
        )
    clusters.sort(
        key=lambda item: (
            item.source_count,
            item.latest_published_at or datetime.min.replace(tzinfo=UTC),
        ),
        reverse=True,
    )
    output = run_dir / "clusters.json"
    output.write_text(
        json.dumps([item.model_dump(mode="json") for item in clusters], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return clusters
