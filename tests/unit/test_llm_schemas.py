from __future__ import annotations

from morning_radio.llm.schemas import DossierResponse, RundownResponse, SameEventDecision


def test_rundown_response_repairs_model_arithmetic() -> None:
    response = RundownResponse.model_validate(
        {
            "rundown": {
                "show_date": "2026-09-04",
                "target_seconds": 600,
                "planned_seconds": 999,
                "segments": [
                    {
                        "segment_id": "story-1",
                        "type": "story",
                        "title": "Story",
                        "cluster_ids": ["cluster-001"],
                        "planned_seconds": 300,
                        "purpose": "Explain the story",
                    },
                    {
                        "segment_id": "closing",
                        "type": "closing",
                        "title": "Closing",
                        "cluster_ids": [],
                        "planned_seconds": 30,
                        "purpose": "Close the show",
                    },
                ],
            }
        }
    )

    assert response.rundown.planned_seconds == 330


def test_same_event_accepts_common_local_model_aliases() -> None:
    decision = SameEventDecision.model_validate(
        {
            "decision": "same_event",
            "explanation": "Both headlines describe the same announcement.",
        }
    )

    assert decision.same_event is True
    assert decision.reason == "Both headlines describe the same announcement."


def test_same_event_accepts_boolean_alias() -> None:
    decision = SameEventDecision.model_validate(
        {
            "are_same_event": False,
            "explanation": "The subjects overlap but the events differ.",
        }
    )

    assert decision.same_event is False


def test_dossier_response_accepts_unwrapped_dossier() -> None:
    response = DossierResponse.model_validate(
        {
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
    )

    assert response.dossier.cluster_id == "cluster-001"


def test_rundown_response_accepts_unwrapped_rundown() -> None:
    response = RundownResponse.model_validate(
        {
            "show_date": "2026-09-04",
            "target_seconds": 600,
            "planned_seconds": 60,
            "segments": [
                {
                    "segment_id": "story-1",
                    "type": "story",
                    "title": "Story",
                    "cluster_ids": ["cluster-001"],
                    "planned_seconds": 60,
                    "purpose": "Cover the story.",
                }
            ],
        }
    )

    assert response.rundown.segments[0].cluster_ids == ["cluster-001"]
