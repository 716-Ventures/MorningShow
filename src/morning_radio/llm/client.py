from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from morning_radio.logging import log_model_call
from morning_radio.settings import LLMSettings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMConnectionError(LLMError):
    pass


class LLMInvalidResponseError(LLMError):
    pass


class LLMClient(Protocol):
    model: str

    def generate_text(
        self, system_prompt: str, user_prompt: str, *, stage: str, prompt_type: str
    ) -> str: ...

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        stage: str,
        prompt_type: str,
    ) -> T: ...


class OllamaClient:
    """One pooled HTTP client per run; the creating workflow must call close()."""

    def __init__(self, settings: LLMSettings, run_dir: Path):
        self.settings = settings
        self.model = settings.model
        self.fixture_fallback = False
        self.run_dir = run_dir
        self._client = httpx.Client(timeout=settings.timeout_seconds)

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _response_text(response: httpx.Response) -> str:
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("response"), str):
            raise LLMInvalidResponseError("Ollama response must contain a string response field.")
        return payload["response"]

    def generate_text(
        self, system_prompt: str, user_prompt: str, *, stage: str, prompt_type: str
    ) -> str:
        started = time.monotonic()
        try:
            payload = {
                "model": self.settings.model,
                "system": system_prompt,
                "prompt": user_prompt,
                "stream": False,
            }
            response = self._client.post(
                f"{str(self.settings.base_url).rstrip('/')}/api/generate",
                json=payload,
            )
            response.raise_for_status()
            text = self._response_text(response)
            if not text.strip():
                raise LLMInvalidResponseError("Ollama returned an empty response.")
            self._log(stage, prompt_type, user_prompt, started, True, 0, None)
            return text
        except httpx.HTTPError as exc:
            self._log(stage, prompt_type, user_prompt, started, False, 0, str(exc))
            raise LLMConnectionError(f"Ollama request failed: {exc}") from exc
        except (ValueError, LLMInvalidResponseError) as exc:
            self._log(stage, prompt_type, user_prompt, started, False, 0, str(exc))
            raise LLMInvalidResponseError(str(exc)) from exc

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        stage: str,
        prompt_type: str,
    ) -> T:
        prompt = user_prompt
        last_error: str | None = None
        for attempt in range(3):
            started = time.monotonic()
            try:
                payload = {
                    "model": self.settings.model,
                    "system": (
                        system_prompt
                        + "\nThe response must match this JSON Schema exactly: "
                        + json.dumps(response_model.model_json_schema(), ensure_ascii=False)
                    ),
                    "prompt": prompt,
                    "stream": False,
                    "format": response_model.model_json_schema(),
                }
                response = self._client.post(
                    f"{str(self.settings.base_url).rstrip('/')}/api/generate",
                    json=payload,
                )
                response.raise_for_status()
                raw = self._response_text(response)
                parsed = json.loads(raw)
                value = response_model.model_validate(parsed)
                self._log(stage, prompt_type, prompt, started, True, attempt, None)
                return value
            except httpx.HTTPError as exc:
                self._log(stage, prompt_type, prompt, started, False, attempt, str(exc))
                raise LLMConnectionError(f"Ollama request failed: {exc}") from exc
            except (ValidationError, TypeError, ValueError, LLMInvalidResponseError) as exc:
                last_error = str(exc)
                self._log(stage, prompt_type, prompt, started, False, attempt, last_error)
                prompt = (
                    user_prompt
                    + "\n\nYour prior response did not validate. Return valid JSON only. "
                    + f"Validation failure: {last_error}"
                )
        raise LLMInvalidResponseError(f"Model output failed validation after retries: {last_error}")

    def _log(
        self,
        stage: str,
        prompt_type: str,
        prompt: str,
        started: float,
        success: bool,
        retry_count: int,
        error: str | None,
    ) -> None:
        log_model_call(
            self.run_dir,
            {
                "stage": stage,
                "model": self.settings.model,
                "prompt_type": prompt_type,
                "input_character_count": len(prompt),
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "success": success,
                "retry_count": retry_count,
                "error": error,
            },
        )


class FakeLLM:
    model = "fake-local-fixture"
    fixture_fallback = True

    def generate_text(
        self, system_prompt: str, user_prompt: str, *, stage: str, prompt_type: str
    ) -> str:
        if stage == "writing":
            return "[HOST]\nGood morning. This is a fixture morning show.\n"
        return "{}"

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        stage: str,
        prompt_type: str,
    ) -> T:
        data = _fake_payload(stage, user_prompt)
        return response_model.model_validate(data)


def build_llm_client(settings: LLMSettings, run_dir: Path) -> LLMClient:
    if os.environ.get("MORNING_RADIO_FAKE_LLM") == "1":
        return FakeLLM()
    return OllamaClient(settings, run_dir)


def allows_fixture_fallback(llm: LLMClient) -> bool:
    return bool(getattr(llm, "fixture_fallback", False))


def _fake_payload(stage: str, user_prompt: str) -> dict[str, Any]:
    if stage == "verification":
        if os.environ.get("MORNING_RADIO_FAKE_VERIFICATION_FAIL") == "1":
            return {
                "verification": {
                    "status": "fail",
                    "issues": [
                        {
                            "severity": "high",
                            "category": "fixture",
                            "script_excerpt": "Good morning.",
                            "explanation": "Fixture verification failure requested.",
                            "supporting_source_ids": [],
                            "required_action": "Stop the run before synthesis.",
                        }
                    ],
                    "corrected_script_required": False,
                }
            }
        return {
            "verification": {"status": "pass", "issues": [], "corrected_script_required": False}
        }
    return {}
