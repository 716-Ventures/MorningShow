from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from morning_radio.decisions import ENDPOINT, JevClient, run_shadow
from morning_radio.models import CandidateStory, ExtractionResult
from morning_radio.newsroom.cluster import cluster_stories
from morning_radio.newsroom.score import score_stories
from morning_radio.profile.compiler import default_profile
from morning_radio.settings import DecisionSettings


@pytest.fixture
def newsroom(tmp_path, monkeypatch):
    monkeypatch.setattr("morning_radio.credentials.repo_root", lambda: tmp_path)
    monkeypatch.delenv("MORNING_RADIO_FIXTURE_RUN", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "private-test-key")
    candidates = [
        CandidateStory(
            candidate_id=str(index),
            feed_id="test",
            title=title,
            url=f"https://example.com/{index}",
            retrieved_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        for index, title in enumerate(
            [
                "Acme merger approved by regulators",
                "Regulators approve Acme deal",
                "Football season opens",
                "Football season opens",
            ]
        )
    ]
    extractions = [
        ExtractionResult(
            candidate_id=item.candidate_id,
            url=item.url,
            text="Private article excerpt. " * 200,
            word_count=800,
            extraction_status="usable",
        )
        for item in candidates
    ]
    clusters = cluster_stories(candidates, extractions, tmp_path)
    profile = default_profile()
    scores = score_stories(clusters, profile, tmp_path)
    return candidates, extractions, clusters, scores, profile


def respond(request):
    payload = json.loads(request.content)
    answers = {
        key: (
            {"type": "noul", "noul": 0.97}
            if question["type"] == "noul"
            else {
                "type": "score",
                "score": 2.7,
                "confidence": 0.8,
                "probabilities": {"2": 0.3, "3": 0.7},
            }
        )
        for key, question in payload["questions"].items()
    }
    return httpx.Response(
        200,
        json={
            "model": "jev-test-version",
            "answers": answers,
            "usage": {"input_tokens": 123},
        },
    )


@respx.mock
def test_shadow_compares_without_mutating_production(tmp_path, newsroom):
    before = [item.model_dump_json() for group in newsroom[:4] for item in group]
    files = {
        name: (tmp_path / name).read_bytes() for name in ["clusters.json", "scored-stories.json"]
    }
    route = respx.post(ENDPOINT).mock(side_effect=respond)
    path = run_shadow(DecisionSettings(mode="shadow"), tmp_path, *newsroom)
    assert path is not None
    report = json.loads(path.read_text())
    assert report["status"] == "complete"
    assert len(report["clustering"]) == 1
    assert report["clustering"][0]["baseline_same_event"] is False
    assert report["clustering"][0]["answers"]["same_event"]["noul"] == 0.97
    assert len(report["scoring"]) == len(newsroom[2])
    assert route.call_count == 1 + len(newsroom[2])
    request = json.loads(route.calls[-1].request.content)
    assert len(request["questions"]) == len(newsroom[-1].interests)
    assert all(i.name in json.dumps(request["questions"]) for i in newsroom[-1].interests)
    assert "private-test-key" not in path.read_text()
    assert "Private article excerpt" not in path.read_text()
    assert route.calls[0].request.headers["Authorization"] == "Bearer private-test-key"
    assert before == [item.model_dump_json() for group in newsroom[:4] for item in group]
    assert all((tmp_path / name).read_bytes() == content for name, content in files.items())


@pytest.mark.parametrize("reason", ["off", "missing_TYPESAFE_API_KEY", "fixture_run"])
@respx.mock
def test_no_network_when_disabled_or_fixture(tmp_path, newsroom, monkeypatch, reason):
    route = respx.post(ENDPOINT).mock(side_effect=respond)
    if reason == "missing_TYPESAFE_API_KEY":
        monkeypatch.delenv("TYPESAFE_API_KEY")
    if reason == "fixture_run":
        monkeypatch.setenv("MORNING_RADIO_FIXTURE_RUN", "1")
    mode = "off" if reason == "off" else "shadow"
    path = run_shadow(DecisionSettings(mode=mode), tmp_path, *newsroom)
    assert not route.called
    if path:
        assert json.loads(path.read_text())["reason"] == reason
    else:
        assert reason == "off"


@pytest.mark.parametrize("failure", [401, 429, 500, "timeout", "invalid", "ids", "type", "range"])
@respx.mock
def test_errors_are_sanitized_and_stop_requests(tmp_path, newsroom, failure):
    def fail(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private-test-key", request=request)
        if isinstance(failure, int):
            return httpx.Response(failure, text="private-test-key")
        if failure == "invalid":
            return httpx.Response(200, text="private-test-key")
        data = json.loads(respond(request).content)
        if failure == "ids":
            data["answers"] = {}
        elif failure == "type":
            data["answers"]["same_event"] = {"type": "score", "score": 1, "confidence": 1}
        else:
            data["answers"]["same_event"]["noul"] = 1.5
        return httpx.Response(200, json=data)

    route = respx.post(ENDPOINT).mock(side_effect=fail)
    path = run_shadow(DecisionSettings(mode="shadow"), tmp_path, *newsroom)
    assert path is not None
    assert json.loads(path.read_text())["status"] == "incomplete"
    assert "private-test-key" not in path.read_text()
    assert route.call_count == 1


@respx.mock
def test_sampling_limits_and_missing_baseline(tmp_path, newsroom):
    route = respx.post(ENDPOINT).mock(side_effect=respond)
    baseline = tmp_path / "logs/cluster-decisions.json"
    pairs = json.loads(baseline.read_text())
    baseline.write_text(json.dumps([pairs[0], pairs[0]]))
    path = run_shadow(DecisionSettings(mode="shadow", max_calls_per_stage=1), tmp_path, *newsroom)
    assert path is not None
    report = json.loads(path.read_text())
    assert report["clustering_truncated"] and report["scoring_truncated"]
    assert route.call_count == 2
    baseline.unlink()
    run_shadow(DecisionSettings(mode="shadow"), tmp_path, *newsroom)
    assert json.loads(path.read_text())["status"] == "incomplete"
    assert route.call_count == 2


@respx.mock
def test_client_closes_and_records_metadata():
    respx.post(ENDPOINT).mock(side_effect=respond)
    client = JevClient(DecisionSettings(), "test")
    try:
        result = client.evaluate({}, {"q": {"type": "noul", "instructions": "Is this news?"}})
        assert len(result["request_hash"]) == 64
        assert result["model"] == "jev-test-version"
        assert result["usage"]["input_tokens"] == 123
    finally:
        client.close()
    assert client.http.is_closed


@respx.mock
def test_dotenv_credentials_and_unreadable_file(tmp_path, newsroom, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    path = tmp_path / ".env"
    path.write_text("TYPESAFE_API_KEY=dotenv-test-key\n")
    route = respx.post(ENDPOINT).mock(side_effect=respond)
    run_shadow(DecisionSettings(mode="shadow"), tmp_path, *newsroom)
    assert route.calls[0].request.headers["Authorization"] == "Bearer dotenv-test-key"
    count = route.call_count
    path.write_bytes(b"\xff")
    report_path = run_shadow(DecisionSettings(mode="shadow"), tmp_path, *newsroom)
    assert report_path is not None
    assert json.loads(report_path.read_text())["error_type"] == "ConfigError"
    assert route.call_count == count
