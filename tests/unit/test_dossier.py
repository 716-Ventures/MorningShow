from __future__ import annotations

import pytest
from pydantic import ValidationError

from morning_radio.llm.client import LLMInvalidResponseError
from morning_radio.models import (
    Cluster,
    DossierFact,
    ExtractionResult,
    SelectedStory,
    SelectionResult,
    StoryDossier,
)
from morning_radio.newsroom.dossier import _valid_source_ids, build_dossiers


class FailingDossierLLM:
    model = "small-local-model"

    def generate_text(self, *args, **kwargs) -> str:
        return ""

    def generate_structured(self, *args, **kwargs):
        raise LLMInvalidResponseError("missing dossier wrapper")


def _dossier_payload(**overrides):
    payload = {
        "cluster_id": "cluster-001",
        "working_headline": "Story",
        "what_happened": "Fact.",
        "what_is_new_today": "New.",
        "why_it_matters": "Matters.",
        "background_needed": "None.",
        "facts": [{"claim": "Fact.", "supporting_candidate_ids": ["candidate-001"]}],
        "recommended_seconds": 60,
        "source_ids": ["candidate-001"],
        "safe_for_scripting": True,
    }
    payload.update(overrides)
    return payload


def test_safe_dossier_rejects_empty_fact_citations() -> None:
    with pytest.raises(ValidationError):
        StoryDossier.model_validate(
            _dossier_payload(facts=[{"claim": "Fact.", "supporting_candidate_ids": []}])
        )


def test_safe_dossier_rejects_unknown_fact_source_id() -> None:
    with pytest.raises(ValidationError):
        StoryDossier.model_validate(
            _dossier_payload(
                facts=[{"claim": "Fact.", "supporting_candidate_ids": ["missing"]}]
            )
        )


def test_safe_dossier_rejects_blank_fact() -> None:
    with pytest.raises(ValidationError):
        StoryDossier.model_validate(
            _dossier_payload(facts=[{"claim": "   ", "supporting_candidate_ids": ["candidate-001"]}])
        )


def test_safe_dossier_rejects_no_facts() -> None:
    with pytest.raises(ValidationError):
        StoryDossier.model_validate(_dossier_payload(facts=[]))


def test_safe_dossier_rejects_unused_source_id() -> None:
    with pytest.raises(ValidationError):
        StoryDossier.model_validate(
            _dossier_payload(
                facts=[{"claim": "Fact.", "supporting_candidate_ids": ["candidate-001"]}],
                source_ids=["candidate-001", "candidate-002"],
            )
        )


def test_valid_multi_source_dossier_passes() -> None:
    dossier = StoryDossier.model_validate(
        _dossier_payload(
            facts=[
                {"claim": "Fact one.", "supporting_candidate_ids": ["candidate-001"]},
                {"claim": "Fact two.", "supporting_candidate_ids": ["candidate-002"]},
            ],
            source_ids=["candidate-001", "candidate-002"],
        )
    )
    assert dossier.safe_for_scripting is True


def test_valid_source_ids_rejects_empty_safe_dossier() -> None:
    dossier = StoryDossier(
        cluster_id="cluster-001",
        working_headline="Story",
        what_happened="Insufficient source text.",
        what_is_new_today="New.",
        why_it_matters="Matters.",
        background_needed="None.",
        facts=[],
        recommended_seconds=60,
        source_ids=[],
        safe_for_scripting=False,
    )
    unsafe_marked_safe = dossier.model_copy(update={"safe_for_scripting": True})
    assert _valid_source_ids(unsafe_marked_safe, {"candidate-001"}) is False


def test_valid_source_ids_accepts_grounded_safe_dossier() -> None:
    dossier = StoryDossier(
        cluster_id="cluster-001",
        working_headline="Story",
        what_happened="Fact.",
        what_is_new_today="New.",
        why_it_matters="Matters.",
        background_needed="None.",
        facts=[DossierFact(claim="Fact.", supporting_candidate_ids=["candidate-001"])],
        recommended_seconds=60,
        source_ids=["candidate-001"],
        safe_for_scripting=True,
    )
    assert _valid_source_ids(dossier, {"candidate-001"}) is True


def test_unsafe_dossier_backfills_from_unused_high_score_story(tmp_path) -> None:
    clusters = [
        Cluster(
            cluster_id="cluster-primary",
            canonical_title="Primary",
            candidate_ids=["primary-source"],
            source_count=1,
            fingerprint="primary",
        ),
        Cluster(
            cluster_id="cluster-backfill",
            canonical_title="Backfill",
            candidate_ids=["backfill-source"],
            source_count=1,
            fingerprint="backfill",
        ),
    ]
    extractions = [
        ExtractionResult(
            candidate_id="backfill-source",
            url="https://example.com/backfill",
            title="Backfill",
            text="Backfill has a sourced fact. " * 20,
            word_count=100,
            extraction_status="usable",
        )
    ]
    selection = SelectionResult(
        selected=[
            SelectedStory(
                cluster_id="cluster-primary",
                reason="selected",
                estimated_seconds=60,
                score=90,
            )
        ],
        not_selected_high_score=[
            SelectedStory(
                cluster_id="cluster-backfill",
                reason="backfill",
                estimated_seconds=60,
                score=85,
                rejection_reason="duration_budget",
            )
        ],
    )

    dossiers = build_dossiers(selection, clusters, extractions, tmp_path)

    assert [item.cluster_id for item in dossiers] == ["cluster-backfill"]
    assert (tmp_path / "dossiers" / "backfill-history.json").exists()


def test_recoverable_dossier_model_error_uses_grounded_fallback(tmp_path) -> None:
    clusters = [
        Cluster(
            cluster_id="cluster-001",
            canonical_title="Fallback Story",
            candidate_ids=["candidate-001"],
            source_count=1,
            fingerprint="fallback",
        )
    ]
    extractions = [
        ExtractionResult(
            candidate_id="candidate-001",
            url="https://example.com/story",
            title="Fallback Story",
            text="Fallback source sentence. More source text follows.",
            word_count=8,
            extraction_status="usable",
        )
    ]
    selected = [
        SelectedStory(
            cluster_id="cluster-001",
            reason="selected",
            estimated_seconds=60,
            score=90,
        )
    ]

    dossiers = build_dossiers(selected, clusters, extractions, tmp_path, FailingDossierLLM())

    assert len(dossiers) == 1
    assert dossiers[0].facts[0].claim == "Fallback source sentence."
    diagnostic = (tmp_path / "dossiers" / "cluster-001-fallback.json").read_text(encoding="utf-8")
    assert "small-local-model" in diagnostic
    assert "missing dossier wrapper" in diagnostic
