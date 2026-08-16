from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from morning_radio.models import (
    DossierFact,
    ExtractionResult,
    Rundown,
    StoryDossier,
    VerificationResult,
)
from morning_radio.showgen.verify import verify_script


class CorrectingLLM:
    model = "test-corrector"

    def __init__(self) -> None:
        self.calls = 0

    def generate_text(self, *args, **kwargs):
        return ""

    def generate_structured(self, *args, **kwargs):
        response_model = args[2]
        self.calls += 1
        if self.calls == 1:
            return response_model.model_validate(
                {
                    "verification": {
                        "status": "fail",
                        "issues": [
                            {
                                "severity": "high",
                                "category": "unsupported_claim",
                                "script_excerpt": "bad",
                                "explanation": "bad claim",
                                "supporting_source_ids": [],
                                "required_action": "remove_or_rewrite",
                            }
                        ],
                        "corrected_script_required": True,
                    },
                    "corrected_script": "[HOST]\nCorrected script.\n",
                }
            )
        return response_model.model_validate(
            {
                "verification": {
                    "status": "pass",
                    "issues": [],
                    "corrected_script_required": False,
                },
                "corrected_script": None,
            }
        )


class PassingCorrectingLLM:
    model = "test-pass-corrector"

    def __init__(self) -> None:
        self.calls = 0

    def generate_text(self, *args, **kwargs):
        return ""

    def generate_structured(self, *args, **kwargs):
        response_model = args[2]
        self.calls += 1
        if self.calls == 1:
            return response_model.model_validate(
                {
                    "verification": {
                        "status": "pass",
                        "issues": [],
                        "corrected_script_required": False,
                    },
                    "corrected_script": "[HOST]\nCorrected after pass.\n",
                }
            )
        return response_model.model_validate(
            {
                "verification": {
                    "status": "pass",
                    "issues": [],
                    "corrected_script_required": False,
                },
                "corrected_script": None,
            }
        )


class InvalidCorrectionLLM:
    model = "test-invalid-corrector"

    def generate_text(self, *args, **kwargs):
        return ""

    def generate_structured(self, *args, **kwargs):
        response_model = args[2]
        return response_model.model_validate(
            {
                "verification": {
                    "status": "pass",
                    "issues": [],
                    "corrected_script_required": False,
                },
                "corrected_script": "Spoken copy without a host marker.\n",
            }
        )


class CapturingLLM:
    model = "test-capturing"

    def __init__(self) -> None:
        self.payload = {}

    def generate_text(self, *args, **kwargs):
        return ""

    def generate_structured(self, *args, **kwargs):
        response_model = args[2]
        self.payload = json.loads(args[1])
        return response_model.model_validate(
            {
                "verification": {
                    "status": "pass",
                    "issues": [],
                    "corrected_script_required": False,
                },
                "corrected_script": None,
            }
        )


def valid_dossier() -> StoryDossier:
    return StoryDossier(
        cluster_id="cluster-001",
        working_headline="Story",
        what_happened="Fact.",
        what_is_new_today="New.",
        why_it_matters="Matters.",
        background_needed="None.",
        facts=[DossierFact(claim="Fact.", supporting_candidate_ids=["source-001"])],
        recommended_seconds=60,
        source_ids=["source-001"],
    )


def test_verification_result_rejects_pass_with_high_issue() -> None:
    with pytest.raises(ValidationError):
        VerificationResult.model_validate(
            {
                "status": "pass",
                "issues": [
                    {
                        "severity": "high",
                        "category": "unsupported_claim",
                        "script_excerpt": "bad",
                        "explanation": "bad claim",
                        "supporting_source_ids": [],
                        "required_action": "remove",
                    }
                ],
                "corrected_script_required": False,
            }
        )


def test_verification_result_rejects_pass_requiring_correction() -> None:
    with pytest.raises(ValidationError):
        VerificationResult.model_validate(
            {"status": "pass", "issues": [], "corrected_script_required": True}
        )


def test_verification_correction_loop_writes_final(tmp_path: Path) -> None:
    dossier = valid_dossier()
    verified = verify_script(
        "[HOST]\nDraft script.\n",
        [dossier],
        tmp_path,
        CorrectingLLM(),
        maximum_correction_cycles=2,
    )
    assert verified.verification.status == "pass"
    assert verified.script == "[HOST]\nCorrected script.\n"
    assert (tmp_path / "script-final.md").read_text() == "[HOST]\nCorrected script.\n"
    assert (tmp_path / "logs" / "verification-0.json").exists()
    assert (tmp_path / "logs" / "verification-1.json").exists()


def test_pass_with_corrected_script_requires_fresh_cycle(tmp_path: Path) -> None:
    llm = PassingCorrectingLLM()
    verified = verify_script(
        "[HOST]\nDraft script.\n",
        [valid_dossier()],
        tmp_path,
        llm,
        maximum_correction_cycles=2,
    )
    assert llm.calls == 2
    assert verified.verification.status == "pass"
    assert verified.script == "[HOST]\nCorrected after pass.\n"
    assert (tmp_path / "script-final.md").read_text() == "[HOST]\nCorrected after pass.\n"


def test_invalid_corrected_script_is_rejected(tmp_path: Path) -> None:
    verified = verify_script(
        "[HOST]\nDraft script.\n",
        [valid_dossier()],
        tmp_path,
        InvalidCorrectionLLM(),
        maximum_correction_cycles=2,
    )
    assert verified.verification.status == "fail"
    assert verified.verification.issues[0].category == "script_structure"
    assert not (tmp_path / "script-final.md").exists()


def test_verifier_payload_includes_profile_rundown_and_extractions(tmp_path: Path) -> None:
    llm = CapturingLLM()
    rundown = Rundown(show_date=date(2026, 8, 15), target_seconds=600, planned_seconds=600, segments=[])
    extraction = ExtractionResult(
        candidate_id="source-001",
        url="https://example.com/story",
        final_url="https://example.com/story",
        title="Story",
        text="Source evidence.",
        word_count=2,
        extraction_status="usable",
        published_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    verify_script(
        "[HOST]\nDraft script.\n",
        [valid_dossier()],
        tmp_path,
        llm,
        maximum_correction_cycles=2,
        profile={"location": "Buffalo"},
        rundown=rundown,
        extractions=[extraction],
    )
    assert llm.payload["profile"] == {"location": "Buffalo"}
    assert llm.payload["rundown"]["target_seconds"] == 600
    assert llm.payload["extractions"][0]["candidate_id"] == "source-001"
