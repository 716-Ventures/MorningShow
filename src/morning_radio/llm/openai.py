"""OpenAI chat adapter; no automatic retries of failed billable HTTP requests."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from morning_radio.credentials import read_credential
from morning_radio.llm.client import LLMConnectionError, LLMInvalidResponseError
from morning_radio.logging import log_model_call
from morning_radio.settings import LLMSettings

T = TypeVar("T", bound=BaseModel)


class OpenAIClient:
    def __init__(self, settings: LLMSettings, run_dir: Path):
        key = read_credential("OPENAI_API_KEY")
        if not key:
            raise LLMConnectionError("Set OPENAI_API_KEY in the project .env file.")
        self.model = settings.model
        self.run_dir = run_dir
        self._client = httpx.Client(
            base_url="https://api.openai.com/v1/",
            headers={"Authorization": f"Bearer {key}"},
            timeout=settings.timeout_seconds,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def _generate(self, system: str, prompt: str, stage: str, kind: str, structured: bool) -> str:
        started = time.monotonic()
        error = None
        try:
            payload: dict[str, object] = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_completion_tokens": 12000,
                "store": False,
            }
            if structured:
                payload["response_format"] = {"type": "json_object"}
            response = self._client.post("chat/completions", json=payload)
            if not response.is_success:
                raise LLMConnectionError(
                    f"OpenAI returned HTTP {response.status_code}. Check API credentials, "
                    "model access, billing and rate limits. No request was retried."
                )
            choice = response.json()["choices"][0]
            content = choice["message"]["content"]
            if (
                choice.get("finish_reason") != "stop"
                or not isinstance(content, str)
                or not content.strip()
            ):
                raise LLMInvalidResponseError(
                    "OpenAI returned incomplete, refused or empty output."
                )
            return content
        except httpx.HTTPError:
            error = "OpenAI network request failed; no automatic retry."
            raise LLMConnectionError(error) from None
        except (KeyError, IndexError, TypeError, ValueError):
            error = "OpenAI returned malformed response data."
            raise LLMInvalidResponseError(error) from None
        except (LLMConnectionError, LLMInvalidResponseError) as exc:
            error = str(exc)
            raise
        finally:
            log_model_call(
                self.run_dir,
                {
                    "stage": stage,
                    "model": self.model,
                    "provider": "openai",
                    "prompt_type": kind,
                    "input_character_count": len(prompt),
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "success": error is None,
                    "error": error,
                },
            )

    def generate_text(
        self, system_prompt: str, user_prompt: str, *, stage: str, prompt_type: str
    ) -> str:
        return self._generate(system_prompt, user_prompt, stage, prompt_type, False)

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        stage: str,
        prompt_type: str,
    ) -> T:
        system = (
            system_prompt
            + "\nReturn only JSON matching this schema: "
            + json.dumps(response_model.model_json_schema())
        )
        prompt = user_prompt
        for _ in range(3):
            raw = self._generate(system, prompt, stage, prompt_type, True)
            try:
                return response_model.model_validate_json(raw)
            except ValidationError:
                prompt = (
                    user_prompt
                    + "\nPrior output did not match the schema. Include every required field with the correct type."
                )
        raise LLMInvalidResponseError(
            "OpenAI output failed schema validation after three attempts."
        )
