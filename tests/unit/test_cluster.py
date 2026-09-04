from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from morning_radio.llm.client import LLMInvalidResponseError
from morning_radio.models import CandidateStory, ExtractionResult
from morning_radio.newsroom.cluster import cluster_stories


class SameEventLLM:
    model = "test"

    def __init__(self, same_event: bool) -> None:
        self.same_event = same_event
        self.calls = 0

    def generate_text(self, *args, **kwargs):
        return ""

    def generate_structured(self, *args, **kwargs):
        self.calls += 1
        response_model = args[2]
        return response_model(
            same_event=self.same_event,
            canonical_title="Adjudicated title",
            reason="fixture",
        )


class FailingSameEventLLM:
    model = "test"

    def __init__(self) -> None:
        self.calls = 0

    def generate_text(self, *args, **kwargs):
        return ""

    def generate_structured(self, *args, **kwargs):
        self.calls += 1
        raise LLMInvalidResponseError("missing same_event")


def candidate(candidate_id: str, title: str, *, published_at: datetime | None = None) -> CandidateStory:
    return CandidateStory(
        candidate_id=candidate_id,
        feed_id="feed",
        title=title,
        url=f"https://example.com/{candidate_id}",
        retrieved_at=datetime.now().astimezone(),
        published_at=published_at,
        category_hints=["news"],
    )


def extraction(candidate_id: str) -> ExtractionResult:
    return ExtractionResult(
        candidate_id=candidate_id,
        url=f"https://example.com/{candidate_id}",
        text="word " * 220,
        word_count=220,
        extraction_status="usable",
    )


def test_clusters_usable_articles(tmp_path: Path) -> None:
    candidates = [
        candidate("a", "OpenAI launches new developer tool"),
        candidate("b", "Bills practice report"),
    ]
    extractions = [extraction("a"), extraction("b")]
    clusters = cluster_stories(candidates, extractions, tmp_path)
    assert len(clusters) == 2
    assert (tmp_path / "clusters.json").exists()


def test_high_confidence_same_event_different_headline_merges(tmp_path: Path) -> None:
    candidates = [
        candidate("a", "OpenAI launches coding agent"),
        candidate("b", "OpenAI coding agent launches"),
    ]
    clusters = cluster_stories(candidates, [extraction("a"), extraction("b")], tmp_path)
    assert len(clusters) == 1
    assert clusters[0].candidate_ids == ["a", "b"]


def test_same_company_different_event_stays_separate(tmp_path: Path) -> None:
    candidates = [
        candidate("a", "Apple launches new iPhone"),
        candidate("b", "Apple reports quarterly earnings"),
    ]
    clusters = cluster_stories(candidates, [extraction("a"), extraction("b")], tmp_path)
    assert len(clusters) == 2


def test_next_day_follow_up_stays_separate(tmp_path: Path) -> None:
    base = datetime(2026, 8, 14, tzinfo=UTC)
    candidates = [
        candidate("a", "Acme merger approved by regulators", published_at=base),
        candidate(
            "b",
            "Acme merger faces lawsuit after approval",
            published_at=base + timedelta(days=2),
        ),
    ]
    clusters = cluster_stories(candidates, [extraction("a"), extraction("b")], tmp_path)
    assert len(clusters) == 2


def test_syndicated_publisher_suffix_merges(tmp_path: Path) -> None:
    candidates = [
        candidate("a", "Court blocks tech merger - Reuters"),
        candidate("b", "Court blocks tech merger - AP"),
    ]
    clusters = cluster_stories(candidates, [extraction("a"), extraction("b")], tmp_path)
    assert len(clusters) == 1


def test_ambiguous_pair_uses_llm_adjudication(tmp_path: Path) -> None:
    llm = SameEventLLM(same_event=True)
    candidates = [
        candidate("a", "Acme merger approved by regulators"),
        candidate("b", "Regulators approve Acme deal"),
    ]
    clusters = cluster_stories(candidates, [extraction("a"), extraction("b")], tmp_path, llm)
    assert llm.calls == 1
    assert len(clusters) == 1


def test_ambiguous_llm_rejection_stays_separate(tmp_path: Path) -> None:
    llm = SameEventLLM(same_event=False)
    candidates = [
        candidate("a", "Acme merger approved by regulators"),
        candidate("b", "Regulators approve Acme deal"),
    ]
    clusters = cluster_stories(candidates, [extraction("a"), extraction("b")], tmp_path, llm)
    assert llm.calls == 1
    assert len(clusters) == 2


def test_ambiguous_llm_failure_stays_separate_with_diagnostic(tmp_path: Path) -> None:
    llm = FailingSameEventLLM()
    candidates = [
        candidate("a", "Acme merger approved by regulators"),
        candidate("b", "Regulators approve Acme deal"),
    ]

    clusters = cluster_stories(candidates, [extraction("a"), extraction("b")], tmp_path, llm)

    assert llm.calls == 1
    assert len(clusters) == 2
    decisions = (tmp_path / "logs" / "cluster-decisions.json").read_text(encoding="utf-8")
    assert "LLM adjudication failed" in decisions
    assert "missing same_event" in decisions
