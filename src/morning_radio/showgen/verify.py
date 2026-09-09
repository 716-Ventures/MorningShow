from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from morning_radio.artifacts.io import atomic_write_json, atomic_write_text
from morning_radio.llm.client import LLMClient, LLMError, allows_fixture_fallback
from morning_radio.llm.prompts import VERIFY_SYSTEM
from morning_radio.llm.schemas import VerificationResponse
from morning_radio.models import (
    ExtractionResult,
    Rundown,
    StoryDossier,
    VerificationIssue,
    VerificationResult,
    VerifiedScript,
)
from morning_radio.showgen.script import ScriptError, validate_script, validate_script_quality

MAX_LLM_VERIFICATION_PROMPT_CHARS = 80_000


def verify_script(
    script: str,
    dossiers: list[StoryDossier],
    run_dir: Path,
    llm: LLMClient | None = None,
    maximum_correction_cycles: int = 2,
    *,
    profile: Any | None = None,
    rundown: Rundown | None = None,
    extractions: list[ExtractionResult] | None = None,
) -> VerifiedScript:
    current_script = script
    for cycle in range(maximum_correction_cycles + 1):
        result, corrected_script = _verify_once(
            current_script,
            dossiers,
            run_dir,
            llm,
            cycle,
            profile=profile,
            rundown=rundown,
            extractions=extractions,
        )
        _persist_iteration(result, run_dir, cycle)
        if result.status == "pass":
            corrected_script = None
        if corrected_script and corrected_script != current_script:
            try:
                validate_script(corrected_script)
            except ScriptError as exc:
                result = _script_structure_failure(str(exc))
                _persist(result, run_dir)
                return VerifiedScript(verification=result, script=current_script)
            if cycle < maximum_correction_cycles:
                current_script = corrected_script
                atomic_write_text(run_dir / f"script-corrected-{cycle + 1}.md", current_script)
                continue
            result = VerificationResult(
                status="fail",
                issues=[
                    VerificationIssue(
                        severity="high",
                        category="correction_cycle_limit",
                        script_excerpt="",
                        explanation="Verifier returned a corrected script after all correction cycles.",
                        supporting_source_ids=[],
                        required_action="increase_cycles_or_rewrite",
                    )
                ],
                corrected_script_required=True,
            )
            _persist(result, run_dir)
            return VerifiedScript(verification=result, script=current_script)
        if result.status == "pass":
            final_script = current_script
            try:
                validate_script(final_script)
            except ScriptError as exc:
                result = _script_structure_failure(str(exc))
                _persist(result, run_dir)
                return VerifiedScript(verification=result, script=current_script)
            atomic_write_text(run_dir / "script-final.md", final_script)
            _persist(result, run_dir)
            return VerifiedScript(verification=result, script=final_script)
        _persist(result, run_dir)
        return VerifiedScript(verification=result, script=current_script)
    raise RuntimeError("Verification loop ended unexpectedly.")


def _verify_once(
    script: str,
    dossiers: list[StoryDossier],
    run_dir: Path,
    llm: LLMClient | None,
    cycle: int,
    *,
    profile: Any | None,
    rundown: Rundown | None,
    extractions: list[ExtractionResult] | None,
) -> tuple[VerificationResult, str | None]:
    issues: list[VerificationIssue] = []
    try:
        validate_script(script)
        if rundown is not None:
            validate_script_quality(script, rundown, dossiers)
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
        return VerificationResult(
            status="fail", issues=issues, corrected_script_required=True
        ), None
    if llm is not None:
        verification_prompt = json.dumps(
            {
                "script": script,
                "dossiers": [item.model_dump(mode="json") for item in dossiers],
                "profile": _dump_optional_model(profile),
                "rundown": _dump_optional_model(rundown),
                "source_evidence": compact_source_evidence(dossiers, extractions or []),
                "correction_cycle": cycle,
            },
            ensure_ascii=False,
        )
        fixture_fallback = allows_fixture_fallback(llm)
        oversized_prompt = len(verification_prompt) > MAX_LLM_VERIFICATION_PROMPT_CHARS
        if not fixture_fallback and oversized_prompt:
            atomic_write_json(
                run_dir / "logs" / "verification-fallback.json",
                {
                    "model": llm.model,
                    "reason": "Verification prompt exceeded local model guardrail.",
                    "input_character_count": len(verification_prompt),
                    "fixture_fallback": fixture_fallback,
                },
            )
            return _verification_unavailable(
                "Verification prompt exceeded the model size limit."
            ), None
        try:
            response = llm.generate_structured(
                VERIFY_SYSTEM,
                verification_prompt,
                VerificationResponse,
                stage="verification",
                prompt_type="editorial_gate",
            )
            return response.verification, response.corrected_script
        except (LLMError, ValidationError) as exc:
            atomic_write_json(
                run_dir / "logs" / "verification-fallback.json",
                {
                    "model": llm.model,
                    "reason": str(exc),
                    "fixture_fallback": allows_fixture_fallback(llm),
                },
            )
            return _verification_unavailable(f"Verification model failed: {exc}"), None
    return (
        VerificationResult(
            status="pass",
            issues=[],
            corrected_script_required=False,
        ),
        None,
    )


def _script_structure_failure(explanation: str) -> VerificationResult:
    return VerificationResult(
        status="fail",
        issues=[
            VerificationIssue(
                severity="high",
                category="script_structure",
                script_excerpt="",
                explanation=explanation,
                supporting_source_ids=[],
                required_action="rewrite",
            )
        ],
        corrected_script_required=True,
    )


def _verification_unavailable(explanation: str) -> VerificationResult:
    """A failed check is not evidence that the script is safe to publish."""
    return VerificationResult(
        status="fail",
        issues=[
            VerificationIssue(
                severity="high",
                category="verification_unavailable",
                script_excerpt="",
                explanation=explanation,
                required_action="restore_verifier_and_retry",
            )
        ],
        corrected_script_required=False,
    )


def _dump_optional_model(value: Any | None) -> Any | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def compact_source_evidence(
    dossiers: list[StoryDossier],
    extractions: list[ExtractionResult],
) -> list[dict[str, Any]]:
    relevant_ids = {source_id for dossier in dossiers for source_id in dossier.source_ids}
    return [
        {
            "candidate_id": extraction.candidate_id,
            "title": extraction.title,
            "published_at": extraction.published_at.isoformat()
            if extraction.published_at
            else None,
            "text_excerpt": extraction.text[:3000],
        }
        for extraction in extractions
        if extraction.candidate_id in relevant_ids
    ][:12]


def _persist(result: VerificationResult, run_dir: Path) -> None:
    atomic_write_json(run_dir / "verification.json", result.model_dump(mode="json"))


def _persist_iteration(result: VerificationResult, run_dir: Path, cycle: int) -> None:
    path = run_dir / "logs" / f"verification-{cycle}.json"
    atomic_write_json(path, result.model_dump(mode="json"))
