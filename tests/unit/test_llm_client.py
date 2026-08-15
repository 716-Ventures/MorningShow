from __future__ import annotations

from pathlib import Path

import respx
from httpx import Response
from pydantic import BaseModel

from morning_radio.llm.client import OllamaClient
from morning_radio.settings import LLMSettings


class TinyResponse(BaseModel):
    answer: str


def test_structured_generation_retries_after_invalid_json(tmp_path: Path) -> None:
    settings = LLMSettings(
        base_url="http://ollama.test",
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
        base_url="http://ollama.test",
        model="fixture-model",
        timeout_seconds=30,
    )
    client = OllamaClient(settings, tmp_path)
    with respx.mock(base_url="http://ollama.test") as router:
        router.post("/api/generate").mock(Response(200, json={"response": "hello"}))
        result = client.generate_text("system", "user", stage="writing", prompt_type="test")
    assert result == "hello"
    assert (tmp_path / "logs" / "model-calls.jsonl").exists()
