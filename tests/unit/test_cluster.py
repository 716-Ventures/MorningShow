from __future__ import annotations

from datetime import datetime
from pathlib import Path

from morning_radio.models import CandidateStory, ExtractionResult
from morning_radio.newsroom.cluster import cluster_stories


def test_clusters_usable_articles(tmp_path: Path) -> None:
    candidates = [
        CandidateStory(
            candidate_id="a",
            feed_id="feed",
            title="OpenAI launches new developer tool",
            url="https://example.com/a",
            retrieved_at=datetime.now().astimezone(),
            category_hints=["ai"],
        ),
        CandidateStory(
            candidate_id="b",
            feed_id="feed",
            title="Bills practice update",
            url="https://example.com/b",
            retrieved_at=datetime.now().astimezone(),
            category_hints=["sports"],
        ),
    ]
    extractions = [
        ExtractionResult(candidate_id="a", url="https://example.com/a", text="word " * 220, word_count=220, extraction_status="usable"),
        ExtractionResult(candidate_id="b", url="https://example.com/b", text="word " * 220, word_count=220, extraction_status="usable"),
    ]
    clusters = cluster_stories(candidates, extractions, tmp_path)
    assert len(clusters) == 2
    assert (tmp_path / "clusters.json").exists()
