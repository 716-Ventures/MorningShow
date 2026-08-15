from __future__ import annotations

from pathlib import Path

from morning_radio.models import StoryDossier
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


def test_verification_correction_loop_writes_final(tmp_path: Path) -> None:
    dossier = StoryDossier(
        cluster_id="cluster-001",
        working_headline="Story",
        what_happened="Fact.",
        what_is_new_today="New.",
        why_it_matters="Matters.",
        background_needed="None.",
        facts=[],
        recommended_seconds=60,
        source_ids=[],
    )
    result = verify_script(
        "[HOST]\nDraft script.\n",
        [dossier],
        tmp_path,
        CorrectingLLM(),
        maximum_correction_cycles=2,
    )
    assert result.status == "pass"
    assert (tmp_path / "script-final.md").read_text() == "[HOST]\nCorrected script.\n"
    assert (tmp_path / "logs" / "verification-0.json").exists()
    assert (tmp_path / "logs" / "verification-1.json").exists()
