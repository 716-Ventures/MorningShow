from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from morning_radio.llm.client import LLMError
from morning_radio.models import (
    DossierFact,
    ExtractionResult,
    Rundown,
    RundownSegment,
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
                    "status": "fail",
                    "issues": [
                        {
                            "severity": "high",
                            "category": "unsupported_claim",
                            "script_excerpt": "Draft script.",
                            "explanation": "Rewrite required.",
                            "supporting_source_ids": [],
                            "required_action": "rewrite",
                        }
                    ],
                    "corrected_script_required": True,
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


class FailingVerificationLLM:
    model = "small-local-model"

    def generate_text(self, *args, **kwargs):
        return ""

    def generate_structured(self, *args, **kwargs):
        raise LLMError("timed out")


class ExplodingVerificationLLM:
    model = "small-local-model"

    def generate_text(self, *args, **kwargs):
        return ""

    def generate_structured(self, *args, **kwargs):
        raise AssertionError("verification LLM should be skipped")


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
    assert llm.calls == 1
    assert verified.verification.status == "pass"
    assert verified.script == "[HOST]\nDraft script.\n"
    assert (tmp_path / "script-final.md").read_text() == "[HOST]\nDraft script.\n"


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
    rundown = Rundown(
        show_date=date(2026, 8, 15),
        target_seconds=600,
        planned_seconds=600,
        segments=[
            RundownSegment(
                segment_id="open",
                type="opening",
                title="Opening",
                cluster_ids=[],
                planned_seconds=600,
                purpose="test",
            )
        ],
    )
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
        substantive_script(),
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
    assert llm.payload["source_evidence"][0]["candidate_id"] == "source-001"


def test_verification_model_error_blocks_publication(tmp_path: Path) -> None:
    verified = verify_script(
        "[HOST]\nDraft script.\n",
        [valid_dossier()],
        tmp_path,
        FailingVerificationLLM(),
        maximum_correction_cycles=2,
    )

    assert verified.verification.status == "fail"
    assert not (tmp_path / "script-final.md").exists()
    diagnostic = (tmp_path / "logs" / "verification-fallback.json").read_text(encoding="utf-8")
    assert "small-local-model" in diagnostic
    assert "timed out" in diagnostic


def test_oversized_verification_blocks_publication(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("morning_radio.showgen.verify.MAX_LLM_VERIFICATION_PROMPT_CHARS", 1)
    verified = verify_script(
        "[HOST]\nDraft script.\n", [valid_dossier()], tmp_path, ExplodingVerificationLLM()
    )
    assert verified.verification.status == "fail"
    assert not (tmp_path / "script-final.md").exists()


def test_verification_prompt_compacts_long_source_evidence(tmp_path: Path) -> None:
    extraction = ExtractionResult(
        candidate_id="source-001",
        url="https://example.com/story",
        final_url="https://example.com/story",
        title="Story",
        text="Long source evidence. " * 10_000,
        word_count=30_000,
        extraction_status="usable",
        published_at=datetime(2026, 8, 15, tzinfo=UTC),
    )

    llm = CapturingLLM()
    verified = verify_script(
        "[HOST]\nDraft script.\n",
        [valid_dossier()],
        tmp_path,
        llm,
        maximum_correction_cycles=2,
        extractions=[extraction],
    )

    assert verified.verification.status == "pass"
    assert len(llm.payload["source_evidence"][0]["text_excerpt"]) == 3000
    assert not (tmp_path / "logs" / "verification-fallback.json").exists()


def substantive_script() -> str:
    words = " ".join(["context"] * 60)
    return f"[HOST]\nFact. This story has {words}.\n"


def large_dossiers() -> list[StoryDossier]:
    return [
        valid_dossier().model_copy(
            update={
                "cluster_id": f"cluster-{index}",
                "working_headline": name,
                "what_happened": f"{name} released details.",
                "background_needed": "background " * 600,
                "source_ids": [f"source-{index}"],
            }
        )
        for index, name in enumerate(("Apple", "Buffalo"))
    ]


class PassageLLM(CapturingLLM):
    def __init__(self, fail_at: int | None = None, correct: bool = False) -> None:
        super().__init__()
        self.payloads: list[dict] = []
        self.fail_at = fail_at
        self.correct = correct

    def generate_structured(self, *args, **kwargs):
        self.payloads.append(json.loads(args[1]))
        assert len(args[1]) <= 12_000
        if len(self.payloads) == self.fail_at:
            if not self.correct:
                raise LLMError("timed out")
            return args[2].model_validate(
                {
                    "verification": {
                        "status": "fail",
                        "issues": [
                            {
                                "severity": "high",
                                "category": "unsupported_claim",
                                "script_excerpt": "released",
                                "explanation": "Needs correction",
                                "required_action": "rewrite",
                            }
                        ],
                        "corrected_script_required": True,
                    },
                    "corrected_script": "[HOST]\nApple released details cautiously.\n",
                }
            )
        return super().generate_structured(*args, **kwargs)


def test_large_verification_checks_every_passage_and_whole_script(tmp_path: Path) -> None:
    llm = PassageLLM()
    script = "[HOST]\nApple released details.\n\n[BUMPER: bumper]\n[HOST]\nBuffalo released details.\n[HOST]\nGood morning.\n"
    result = verify_script(script, large_dossiers(), tmp_path, llm)
    assert result.verification.status == "pass"
    assert len(llm.payloads) == 4
    assert [p["dossiers"][0]["cluster_id"] for p in llm.payloads[:2]] == ["cluster-0", "cluster-1"]
    assert llm.payloads[2]["dossiers"] == []
    assert llm.payloads[3]["script"] == script


def test_follow_up_paragraphs_keep_story_evidence(tmp_path: Path) -> None:
    dossiers = large_dossiers()
    extractions = [
        ExtractionResult(
            candidate_id=f"source-{index}",
            url=f"https://example.com/{index}",
            text=f"{name} released details. No release date was announced.",
            word_count=10,
            extraction_status="usable",
        )
        for index, name in enumerate(("Apple", "Buffalo"))
    ]
    llm = PassageLLM()
    script = (
        "[HOST]\nGood morning.\n\nApple released details.\n\n"
        "No release date was announced.\nMore information is expected.\n"
        "[PAUSE: 650]\n[HOST]\nBuffalo released details.\n\n"
        "No release date was announced.\n[BUMPER: bumper]\n[HOST]\nGoodbye.\n"
    )
    result = verify_script(script, dossiers, tmp_path, llm, extractions=extractions)
    assert result.verification.status == "pass"
    assert result.script == script
    assert [
        [source["candidate_id"] for source in payload["source_evidence"]]
        for payload in llm.payloads[:-1]
    ] == [[], ["source-0"], ["source-0"], ["source-0"], ["source-1"], ["source-1"], []]


@pytest.mark.parametrize(
    "boundary", ["[HOST]", "[HOST 2]", "[PAUSE: 650]\n[HOST]", "[BUMPER: bumper]\n[HOST]"]
)
def test_story_evidence_does_not_leak_across_boundaries(tmp_path, boundary):
    llm = PassageLLM()
    verify_script(
        f"[HOST]\nApple released details.\n{boundary}\nUnrelated factual claim.\n"
        "[HOST]\nBuffalo released details.\n",
        large_dossiers(),
        tmp_path,
        llm,
    )
    assert llm.payloads[1]["dossiers"] == []
    assert llm.payloads[1]["source_evidence"] == []


def test_passage_verification_retains_show_date(tmp_path: Path) -> None:
    from morning_radio.showgen.verify import _verify_passages

    rundown = Rundown(
        show_date=date(2026, 9, 9),
        target_seconds=300,
        planned_seconds=300,
        segments=[
            RundownSegment(
                segment_id="open",
                type="opening",
                title="Opening",
                planned_seconds=300,
                purpose="test",
            )
        ],
    )
    llm = PassageLLM()
    _verify_passages(
        "[HOST]\nApple released details.\n", large_dossiers(), [], tmp_path, llm, 0, None, rundown
    )
    assert llm.payloads[0]["show_date"] == "2026-09-09"
    assert llm.payloads[0]["show_metadata"] == {"story_count": 2}


def test_editorial_overview_does_not_repeat_large_profile(tmp_path):
    from morning_radio.showgen.verify import _verify_passages

    llm = PassageLLM()
    result, _ = _verify_passages(
        "[HOST]\nApple released details.\n",
        large_dossiers(),
        [],
        tmp_path,
        llm,
        0,
        {"irrelevant_context": "x" * 14000},
        None,
    )
    assert result.status == "pass"
    assert "profile" not in llm.payloads[-1]
    assert "rundown" not in llm.payloads[-1]
    assert llm.payloads[-1]["show_metadata"] == {"story_count": 2}


def test_one_unavailable_passage_blocks_whole_episode(tmp_path: Path) -> None:
    llm = PassageLLM(fail_at=2)
    result = verify_script(
        "[HOST]\nApple released details.\n[HOST]\nBuffalo released details.\n",
        large_dossiers(),
        tmp_path,
        llm,
    )
    assert result.verification.status == "fail"
    assert result.verification.issues[0].category == "verification_unavailable"
    assert not (tmp_path / "script-final.md").exists()


def test_passage_correction_retains_other_copy_and_cues_and_is_rechecked(tmp_path: Path) -> None:
    llm = PassageLLM(fail_at=1, correct=True)
    result = verify_script(
        "[HOST]\nApple released details.\n[BUMPER: bumper]\n[HOST]\nBuffalo released details.\n",
        large_dossiers(),
        tmp_path,
        llm,
    )
    assert result.verification.status == "pass"
    assert "Apple released details cautiously." in result.script
    assert "[BUMPER: bumper]\n[HOST]\nBuffalo released details." in result.script
    assert len(llm.payloads) == 5
    assert "cautiously" in llm.payloads[2]["script"]


@pytest.mark.parametrize(
    "correction",
    ["- Markdown list.", "[HOST]\n", "[HOST]\nApple released details.\n[MUSIC: CLOSING]\n"],
)
def test_invalid_passage_correction_never_changes_production(tmp_path, correction):
    class InvalidPassage(PassageLLM):
        def generate_structured(self, *args, **kwargs):
            response = super().generate_structured(*args, **kwargs)
            if response.corrected_script:
                response.corrected_script = correction
            return response

    result = verify_script(
        "[HOST]\nApple released details.\n[HOST]\nBuffalo released details.\n",
        large_dossiers(),
        tmp_path,
        InvalidPassage(1, True),
    )
    assert result.verification.status == "fail"
    assert result.verification.issues[0].category == "script_structure"
    assert result.verification.issues[0].script_excerpt == "Apple released details."
    assert result.verification.issues[1].category == "unsupported_claim"
    assert not (tmp_path / "script-final.md").exists()


def test_global_editorial_failure_still_blocks_after_passages_pass(tmp_path):
    class GlobalFailure(PassageLLM):
        def generate_structured(self, *args, **kwargs):
            response = super().generate_structured(*args, **kwargs)
            if len(self.payloads) == 3:
                return args[2].model_validate(
                    {
                        "verification": {
                            "status": "fail",
                            "issues": [
                                {
                                    "severity": "high",
                                    "category": "duplicate_coverage",
                                    "script_excerpt": "details",
                                    "explanation": "Repeated story",
                                    "required_action": "rewrite",
                                }
                            ],
                            "corrected_script_required": True,
                        }
                    }
                )
            return response

    result = verify_script(
        "[HOST]\nApple released details.\n[HOST]\nBuffalo released details.\n",
        large_dossiers(),
        tmp_path,
        GlobalFailure(),
    )
    assert result.verification.status == "fail"
    assert result.verification.issues[0].category == "duplicate_coverage"
    assert not (tmp_path / "script-final.md").exists()


def test_unchanged_passage_correction_does_not_clear_failure(tmp_path):
    script = "[HOST]\nApple released details cautiously.\n[HOST]\nBuffalo released details.\n"
    result = verify_script(script, large_dossiers(), tmp_path, PassageLLM(1, True))
    assert result.verification.status == "fail"
    assert result.verification.issues[0].category == "unsupported_claim"
    assert not (tmp_path / "script-final.md").exists()


def test_correction_after_limit_is_not_published(tmp_path):
    original = "[HOST]\nOriginal script.\n"
    result = verify_script(original, [], tmp_path, CorrectingLLM(), maximum_correction_cycles=0)
    assert result.script == original
    assert result.verification.issues[0].category == "correction_cycle_limit"
    assert not (tmp_path / "script-final.md").exists()
    assert result.verification.issues[1].category == "unsupported_claim"
    assert (
        tmp_path / "script-pending-verification.md"
    ).read_text() == "[HOST]\nCorrected script.\n"


def test_passage_evidence_retrieves_later_research_context():
    from morning_radio.showgen.verify import _passage_evidence

    detail = "Buffalo has ten days between games to recover before Wednesday practice."
    source = "Unrelated background. " * 170 + detail + " Further context." * 100
    evidence = _passage_evidence(source, detail)
    assert detail in evidence
    assert len(evidence) <= 3000
    assert _passage_evidence(source, None) == source[:3000]
    assert _passage_evidence(detail, detail) == detail


def test_passage_evidence_does_not_invent_or_exceed_research_context():
    from morning_radio.showgen.verify import _passage_evidence

    source = "Original background. " * 400
    evidence = _passage_evidence(source + "Secret unsupported fact.", "Secret unsupported fact.")
    assert "Secret unsupported fact." not in evidence
    assert all(
        part in source[:6000] for part in evidence.split("\n[... source text omitted ...]\n")
    )


def test_single_source_passage_receives_entire_research_excerpt():
    from morning_radio.showgen.verify import compact_source_evidence

    dossier = valid_dossier()
    source = ExtractionResult(
        candidate_id=dossier.source_ids[0],
        url="https://example.com/story",
        text="a" * 7000,
        word_count=1000,
        extraction_status="usable",
    )
    evidence = compact_source_evidence([dossier], [source], passage="Article details.")
    assert evidence[0]["text_excerpt"] == source.text[:6000]


def test_urls_fail_without_calling_model(tmp_path):
    llm = CorrectingLLM()
    result = verify_script("[HOST]\nVisit https://example.com/news today.\n", [], tmp_path, llm)
    assert "url_readout" in {issue.category for issue in result.verification.issues}
    assert llm.calls == 0
    assert not (tmp_path / "script-final.md").exists()


def test_plain_passage_correction_inherits_host_and_is_verified_again(tmp_path):
    class PlainCorrection(PassageLLM):
        def generate_structured(self, *args, **kwargs):
            response = super().generate_structured(*args, **kwargs)
            if response.corrected_script:
                response.corrected_script = response.corrected_script.replace("[HOST]\n", "")
            return response

    llm = PlainCorrection(1, True)
    result = verify_script(
        "[HOST]\nApple released details.\n[BUMPER: bumper]\n[HOST]\nBuffalo released details.\n",
        large_dossiers(),
        tmp_path,
        llm,
    )
    assert result.verification.status == "pass"
    assert "[HOST]\nApple released details cautiously." in result.script
    assert "cautiously" in llm.payloads[2]["script"]
