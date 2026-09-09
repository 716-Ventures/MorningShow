from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from httpx import Response
from pydantic import BaseModel, HttpUrl

from morning_radio.llm.client import LLMInvalidResponseError, OllamaClient
from morning_radio.settings import LLMSettings


class TinyResponse(BaseModel):
    answer: str


@pytest.mark.parametrize("payload", [[], {"response": None}, {"response": 123}])
def test_malformed_envelope_has_typed_error(tmp_path: Path, payload) -> None:
    client = OllamaClient(
        LLMSettings(base_url=HttpUrl("http://ollama.test"), model="test", timeout_seconds=5),
        tmp_path,
    )
    try:
        with respx.mock(base_url="http://ollama.test") as router:
            router.post("/api/generate").mock(Response(200, json=payload))
            with pytest.raises(LLMInvalidResponseError):
                client.generate_text("system", "user", stage="writing", prompt_type="test")
    finally:
        client.close()


def test_structured_generation_retries_after_invalid_json(tmp_path: Path) -> None:
    settings = LLMSettings(
        base_url=HttpUrl("http://ollama.test"),
        model="fixture-model",
        timeout_seconds=30,
    )
    client = OllamaClient(settings, tmp_path)
    with respx.mock(base_url="http://ollama.test") as router:
        router.post("/api/generate").mock(
            side_effect=[
                Response(200, json={"response": "not json"}),
                Response(200, json={"response": '{"answer": "ok"}'}),
            ]
        )
        result = client.generate_structured(
            "system",
            "user",
            TinyResponse,
            stage="scoring",
            prompt_type="test",
        )
    assert result.answer == "ok"
    metrics = (tmp_path / "logs" / "model-calls.jsonl").read_text()
    assert '"success": false' in metrics
    assert '"success": true' in metrics


def test_text_generation_logs_success(tmp_path: Path) -> None:
    settings = LLMSettings(
        base_url=HttpUrl("http://ollama.test"),
        model="fixture-model",
        timeout_seconds=30,
    )
    client = OllamaClient(settings, tmp_path)
    with respx.mock(base_url="http://ollama.test") as router:
        router.post("/api/generate").mock(Response(200, json={"response": "hello"}))
        result = client.generate_text("system", "user", stage="writing", prompt_type="test")
    assert result == "hello"
    assert (tmp_path / "logs" / "model-calls.jsonl").exists()


def test_thinking_option_and_server_timing_metadata(tmp_path):
    client = OllamaClient(
        LLMSettings(
            base_url=HttpUrl("http://ollama.test"), model="test", timeout_seconds=5, thinking=False
        ),
        tmp_path,
    )
    try:
        with respx.mock as router:
            route = router.post("http://ollama.test/api/generate").mock(
                Response(
                    200,
                    json={
                        "response": "ok",
                        "load_duration": 100,
                        "eval_count": 2,
                        "eval_duration": 200,
                    },
                )
            )
            client.generate_text("system", "user", stage="test", prompt_type="test")
        assert json.loads(route.calls[0].request.content)["think"] is False
        metrics = json.loads((tmp_path / "logs/model-calls.jsonl").read_text())
        assert metrics["load_duration_ns"] == 100
        assert metrics["eval_count"] == 2
    finally:
        client.close()
