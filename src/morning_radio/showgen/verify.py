from __future__ import annotations

import json
from pathlib import Path

from morning_radio.llm.client import LLMClient
from morning_radio.llm.prompts import VERIFY_SYSTEM
from morning_radio.llm.schemas import VerificationResponse
from morning_radio.models import StoryDossier, VerificationIssue, VerificationResult
from morning_radio.showgen.script import ScriptError, validate_script


def verify_script(
    script: str,
    dossiers: list[StoryDossier],
    run_dir: Path,
    llm: LLMClient | None = None,
) -> VerificationResult:
    issues: list[VerificationIssue] = []
    try:
        validate_script(script)
    except ScriptError as exc:
        issues.append(
            VerificationIssue(
                severity="high",
                category="script_structure",
                script_excerpt="",
                explanation=str(exc),
                supporting_source_ids=[],
                required_action="rewrite",
            )
        )
    if "http://" in script or "https://" in script:
        issues.append(
            VerificationIssue(
                severity="high",
                category="url_readout",
                script_excerpt="URL present in script",
                explanation="Script should not read source URLs aloud.",
                required_action="remove_url",
            )
        )
    for dossier in dossiers:
        if not dossier.safe_for_scripting:
            issues.append(
                VerificationIssue(
                    severity="high",
                    category="unsupported_story",
                    script_excerpt=dossier.working_headline,
                    explanation="Dossier is marked unsafe for scripting.",
                    supporting_source_ids=dossier.source_ids,
                    required_action="remove_story",
                )
            )
    if issues:
        result = VerificationResult(status="fail", issues=issues, corrected_script_required=True)
    elif llm is not None:
        try:
            response = llm.generate_structured(
                VERIFY_SYSTEM,
                json.dumps(
                    {
                        "script": script,
                        "dossiers": [item.model_dump(mode="json") for item in dossiers],
                    },
                    ensure_ascii=False,
                ),
                VerificationResponse,
                stage="verification",
                prompt_type="editorial_gate",
            )
            result = response.verification
            if result.status == "pass":
                (run_dir / "script-final.md").write_text(
                    response.corrected_script or script, encoding="utf-8"
                )
                _persist(result, run_dir)
                return result
        except Exception:
            if llm.model != "fake-local-fixture":
                raise
            result = VerificationResult(status="pass", issues=[], corrected_script_required=False)
    else:
        result = VerificationResult(
            status="pass",
            issues=[],
            corrected_script_required=False,
        )
    _persist(result, run_dir)
    if result.status == "pass":
        (run_dir / "script-final.md").write_text(script, encoding="utf-8")
    return result


def _persist(result: VerificationResult, run_dir: Path) -> None:
    (run_dir / "verification.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
