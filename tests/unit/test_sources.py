from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from morning_radio.artifacts.sources import write_sources_page
from morning_radio.models import (
    CandidateStory,
    Cluster,
    DossierFact,
    Rundown,
    RundownSegment,
    StoryDossier,
)


def test_sources_page_escapes_external_text(tmp_path: Path) -> None:
    candidate = CandidateStory(
        candidate_id="a",
        feed_id="feed",
        title="<script>alert(1)</script>",
        url="https://example.com/a?x=1&y=2",
        retrieved_at=datetime.now().astimezone(),
        publisher="<b>Publisher</b>",
    )
    cluster = Cluster(
        cluster_id="cluster-001",
        canonical_title="Bad HTML",
        candidate_ids=["a"],
        source_count=1,
        topic_hints=[],
        fingerprint="abc",
    )
    dossier = StoryDossier(
        cluster_id="cluster-001",
        working_headline="Bad HTML",
        what_happened="A thing happened.",
        what_is_new_today="It is new.",
        why_it_matters="It matters.",
        background_needed="None.",
        facts=[DossierFact(claim="A thing happened.", supporting_candidate_ids=["a"])],
        recommended_seconds=60,
        source_ids=["a"],
    )
    rundown = Rundown(
        show_date=date(2026, 8, 14),
        target_seconds=600,
        planned_seconds=60,
        segments=[
            RundownSegment(
                segment_id="story-1",
                type="story",
                title="Bad HTML",
                cluster_ids=["cluster-001"],
                planned_seconds=60,
                purpose="test",
            )
        ],
    )
    path = write_sources_page(rundown, [dossier], [cluster], [candidate], tmp_path)
    html = path.read_text()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "A thing happened" not in html
