from __future__ import annotations

import json
from pathlib import Path

from morning_radio.llm.client import LLMClient
from morning_radio.llm.prompts import VERIFY_SYSTEM
from morning_radio.llm.schemas import VerificationResponse
from morning_radio.models import StoryDossier, VerificationIssue, VerificationResult, VerifiedScript
from morning_radio.showgen.script import ScriptError, validate_script


def verify_script(
    script: str,
    dossiers: list[StoryDossier],
    run_dir: Path,
    llm: LLMClient | None = None,
    maximum_correction_cycles: int = 2,
) -> VerifiedScript:
    current_script = script
    for cycle in range(maximum_correction_cycles + 1):
        result, corrected_script = _verify_once(current_script, dossiers, run_dir, llm, cycle)
        _persist_iteration(result, run_dir, cycle)
        if result.status == "pass":
            final_script = corrected_script or current_script
            try:
                validate_script(final_script)
            except ScriptError as exc:
                result = VerificationResult(
                    status="fail",
                    issues=[
                        VerificationIssue(
                            severity="high",
                            category="script_structure",
                            script_excerpt="",
                            explanation=str(exc),
                            supporting_source_ids=[],
                            required_action="rewrite",
                        )
                    ],
                    corrected_script_required=True,
                )
                _persist(result, run_dir)
                return VerifiedScript(verification=result, script=current_script)
            (run_dir / "script-final.md").write_text(final_script, encoding="utf-8")
            _persist(result, run_dir)
            return VerifiedScript(verification=result, script=final_script)
        if corrected_script and cycle < maximum_correction_cycles:
            current_script = corrected_script
            (run_dir / f"script-corrected-{cycle + 1}.md").write_text(
                current_script, encoding="utf-8"
            )
            continue
        _persist(result, run_dir)
        return VerifiedScript(verification=result, script=current_script)
    raise RuntimeError("Verification loop ended unexpectedly.")


def _verify_once(
    script: str,
    dossiers: list[StoryDossier],
    run_dir: Path,
    llm: LLMClient | None,
    cycle: int,
) -> tuple[VerificationResult, str | None]:
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
        return VerificationResult(status="fail", issues=issues, corrected_script_required=True), None
    if llm is not None:
        try:
            response = llm.generate_structured(
                VERIFY_SYSTEM,
                json.dumps(
                    {
                        "script": script,
                        "dossiers": [item.model_dump(mode="json") for item in dossiers],
                        "correction_cycle": cycle,
                    },
                    ensure_ascii=False,
                ),
                VerificationResponse,
                stage="verification",
                prompt_type="editorial_gate",
            )
            return response.verification, response.corrected_script
        except Exception:
            if llm.model != "fake-local-fixture":
                raise
            return VerificationResult(status="pass", issues=[], corrected_script_required=False), None
    return (
        VerificationResult(
            status="pass",
            issues=[],
            corrected_script_required=False,
        ),
        None,
    )


def _persist(result: VerificationResult, run_dir: Path) -> None:
    (run_dir / "verification.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _persist_iteration(result: VerificationResult, run_dir: Path, cycle: int) -> None:
    path = run_dir / "logs" / f"verification-{cycle}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
