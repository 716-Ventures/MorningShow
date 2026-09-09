from __future__ import annotations

import json
from pathlib import Path

import pytest

from morning_radio.evaluation import evaluate_live, evaluate_selection, load_corpus, pair_candidates
from morning_radio.newsroom.cluster import should_merge


@pytest.mark.parametrize("case", load_corpus().selection_cases, ids=lambda case: case.id)
def test_frozen_multi_interest_selection(case, tmp_path: Path):
    result = evaluate_selection(case, tmp_path)
    assert result.passed, result.model_dump()


@pytest.mark.parametrize(
    "case",
    [case for case in load_corpus().pairs if case.id != "paraphrased"],
    ids=lambda case: case.id,
)
def test_frozen_deterministic_event_pairs(case):
    actual, _, _ = should_merge(*pair_candidates(case), None)
    assert actual == case.same_event


def test_full_evaluation_exercises_actual_verification_gate(tmp_path):
    corpus = load_corpus()

    class Judge:
        model = "recorded-evaluation-decisions"

        def generate_text(self, *args, **kwargs):
            return ""

        def generate_structured(self, system_prompt, user_prompt, response_model, **kwargs):
            payload = json.loads(user_prompt)
            if kwargs["stage"] == "clustering":
                return response_model(
                    same_event="Rochester" not in payload["right"]["title"],
                    canonical_title="Test",
                    reason="Recorded decision",
                )
            text = payload.get("claim") or payload["script"].removeprefix("[HOST]\n").strip()
            case = next(case for case in corpus.claims if case.claim == text)
            if kwargs["stage"] == "evaluation":
                return response_model(supported=case.supported)
            return response_model(
                verification={
                    "status": "pass" if case.supported else "fail",
                    "issues": []
                    if case.supported
                    else [
                        {
                            "severity": "high",
                            "category": "unsupported_claim",
                            "script_excerpt": text,
                            "explanation": "Not supported by supplied evidence",
                            "required_action": "rewrite",
                        }
                    ],
                }
            )

    results = evaluate_live(Judge(), tmp_path)
    assert len(results) == 20 and all(result.passed for result in results)
    for case in corpus.claims:
        assert (tmp_path / case.id / "script-final.md").exists() == case.supported
