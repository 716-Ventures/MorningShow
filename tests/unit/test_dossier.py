from __future__ import annotations

import pytest
from pydantic import ValidationError

from morning_radio.models import DossierFact, StoryDossier
from morning_radio.newsroom.dossier import _valid_source_ids


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
