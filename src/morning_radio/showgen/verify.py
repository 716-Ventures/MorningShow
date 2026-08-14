from __future__ import annotations

import json
from pathlib import Path

from morning_radio.models import StoryDossier, VerificationIssue, VerificationResult
from morning_radio.showgen.script import ScriptError, validate_script


def verify_script(script: str, dossiers: list[StoryDossier], run_dir: Path) -> VerificationResult:
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
    result = VerificationResult(
        status="fail" if any(issue.severity == "high" for issue in issues) else "pass",
        issues=issues,
        corrected_script_required=bool(issues),
    )
    (run_dir / "verification.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if result.status == "pass":
        (run_dir / "script-final.md").write_text(script, encoding="utf-8")
    return result
