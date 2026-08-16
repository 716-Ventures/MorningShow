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


def test_sources_page_uses_final_script_order_and_supported_sources(tmp_path: Path) -> None:
    now = datetime.now().astimezone()
    candidates = [
        CandidateStory(
            candidate_id="used",
            feed_id="feed",
            title="Used source",
            url="https://example.com/used",
            retrieved_at=now,
            publisher="Publisher",
        ),
        CandidateStory(
            candidate_id="extra",
            feed_id="feed",
            title="Extra source",
            url="https://example.com/extra",
            retrieved_at=now,
            publisher="Publisher",
        ),
        CandidateStory(
            candidate_id="removed",
            feed_id="feed",
            title="Removed source",
            url="https://example.com/removed",
            retrieved_at=now,
            publisher="Publisher",
        ),
    ]
    clusters = [
        Cluster(
            cluster_id="cluster-used",
            canonical_title="Used Story",
            candidate_ids=["used", "extra"],
            source_count=2,
            topic_hints=[],
            fingerprint="used",
        ),
        Cluster(
            cluster_id="cluster-removed",
            canonical_title="Removed Story",
            candidate_ids=["removed"],
            source_count=1,
            topic_hints=[],
            fingerprint="removed",
        ),
    ]
    dossiers = [
        StoryDossier(
            cluster_id="cluster-used",
            working_headline="Used Story",
            what_happened="A used thing happened.",
            what_is_new_today="It is new.",
            why_it_matters="It matters.",
            background_needed="None.",
            facts=[DossierFact(claim="A used thing happened.", supporting_candidate_ids=["used"])],
            recommended_seconds=60,
            source_ids=["used"],
        ),
        StoryDossier(
            cluster_id="cluster-removed",
            working_headline="Removed Story",
            what_happened="A removed thing happened.",
            what_is_new_today="It is new.",
            why_it_matters="It matters.",
            background_needed="None.",
            facts=[DossierFact(claim="A removed thing happened.", supporting_candidate_ids=["removed"])],
            recommended_seconds=60,
            source_ids=["removed"],
        ),
    ]
    rundown = Rundown(
        show_date=date(2026, 8, 14),
        target_seconds=600,
        planned_seconds=180,
        segments=[
            RundownSegment(
                segment_id="headlines",
                type="headlines",
                title="Headlines",
                cluster_ids=["cluster-used", "cluster-removed"],
                planned_seconds=60,
                purpose="test",
            ),
            RundownSegment(
                segment_id="story-1",
                type="story",
                title="Used Story",
                cluster_ids=["cluster-used"],
                planned_seconds=60,
                purpose="test",
            ),
            RundownSegment(
                segment_id="story-2",
                type="story",
                title="Removed Story",
                cluster_ids=["cluster-removed"],
                planned_seconds=60,
                purpose="test",
            ),
        ],
    )
    final_script = "[HOST]\nHere is the shape: Used Story; Removed Story.\n\n[HOST]\nNow, Used Story. Details.\n"

    path = write_sources_page(
        rundown,
        dossiers,
        clusters,
        candidates,
        tmp_path,
        final_script=final_script,
        run_id="run-123",
    )
    html = path.read_text(encoding="utf-8")

    assert "run-123" in html
    assert "cluster-used" in html
    assert "Used source" in html
    assert "Extra source" not in html
    assert "cluster-removed" not in html
    assert "Removed source" not in html
