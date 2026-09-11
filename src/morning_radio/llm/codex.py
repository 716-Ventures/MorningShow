"""Subscription-backed text generation through Codex's managed ChatGPT login."""

from __future__ import annotations

import json
import time
import webbrowser
from contextlib import suppress
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError

from morning_radio.llm.client import LLMConnectionError, LLMInvalidResponseError
from morning_radio.llm.codex_rpc import CodexRPC
from morning_radio.logging import log_model_call
from morning_radio.settings import LLMSettings, repo_root

T = TypeVar("T", bound=BaseModel)


def require_chatgpt(server: CodexRPC) -> dict[str, Any]:
    account = server.request("account/read", {"refreshToken": False}).get("account")
    if not isinstance(account, dict) or account.get("type") != "chatgpt":
        raise LLMConnectionError(
            "ChatGPT sign-in required. Run ./show codex-login. API-key authentication is not used by this provider."
        )
    return account


def available_models(server: CodexRPC) -> list[str]:
    models: list[str] = []
    cursor = None
    for _ in range(10):
        result = server.request(
            "model/list", {"limit": 100, "includeHidden": False, "cursor": cursor}
        )
        data = result.get("data")
        if not isinstance(data, list):
            raise LLMConnectionError("Codex returned an invalid model catalog.")
        for item in data:
            if (
                isinstance(item, dict)
                and isinstance(item.get("model"), str)
                and not item.get("hidden", False)
                and "text" in (item.get("inputModalities") or ["text"])
            ):
                models.append(item["model"])
        cursor = result.get("nextCursor")
        if not cursor:
            if not models:
                raise LLMConnectionError("Codex reported no available text models.")
            return list(dict.fromkeys(models))
    raise LLMConnectionError("Codex model catalog exceeded the pagination limit.")


def sign_in(server: CodexRPC) -> None:
    result = server.request("account/login/start", {"type": "chatgpt"})
    url = result.get("authUrl", "")
    try:
        if not isinstance(url, str):
            raise ValueError
        parts = urlsplit(url)
    except ValueError:
        raise LLMConnectionError("Codex returned invalid sign-in data.") from None
    if (
        parts.scheme != "https"
        or parts.hostname not in {"auth.openai.com", "chatgpt.com"}
        or parts.username
        or parts.password
    ):
        raise LLMConnectionError(
            "Codex returned an unexpected sign-in URL; browser was not opened."
        )
    login_id = result.get("loginId")
    if not isinstance(login_id, str) or not login_id:
        raise LLMConnectionError("Codex returned invalid sign-in data.")
    try:
        if not webbrowser.open(url):
            raise LLMConnectionError(
                "Could not open the sign-in browser. Configure a default browser and retry ./show codex-login."
            )
        deadline = time.monotonic() + 300
        completed = False
        account_ready = False
        while True:
            event = server.event(deadline)
            params = event.get("params", {})
            if (
                event.get("method") == "account/login/completed"
                and params.get("loginId") == login_id
            ):
                if params.get("success") is not True:
                    raise LLMConnectionError(
                        "ChatGPT sign-in failed or was cancelled. Run ./show codex-login to retry."
                    )
                completed = True
            elif event.get("method") == "account/updated":
                account_ready = params.get("authMode") == "chatgpt"
            # Login completion can precede the server's in-memory account update.
            if completed and account_ready:
                require_chatgpt(server)
                return
    except BaseException:
        # Closing the owned server also ends its callback listener if cancellation fails.
        with suppress(LLMConnectionError):
            server.request("account/login/cancel", {"loginId": login_id})
        raise


def subscription_status(server: CodexRPC) -> str:
    account = require_chatgpt(server)
    result = server.request("account/rateLimits/read", {})
    buckets = result.get("rateLimitsByLimitId") or {"codex": result.get("rateLimits")}
    if not isinstance(buckets, dict):
        raise LLMConnectionError("Codex returned invalid usage data.")
    windows = []
    for bucket in buckets.values():
        if isinstance(bucket, dict):
            for name in ("primary", "secondary"):
                window = bucket.get(name)
                if isinstance(window, dict) and isinstance(window.get("usedPercent"), (int, float)):
                    remaining = max(0, min(100, 100 - window["usedPercent"]))
                    windows.append(f"{name}: {remaining:g}% remaining")
    return f"ChatGPT {account.get('planType') or 'account'}; " + (
        ", ".join(windows) or "usage limits unavailable"
    )


class CodexClient:
    def __init__(self, settings: LLMSettings, run_dir: Path):
        self.model = settings.model
        self.run_dir = run_dir
        self.server = CodexRPC(repo_root(), settings.timeout_seconds)
        try:
            require_chatgpt(self.server)
            if self.model not in available_models(self.server):
                raise LLMConnectionError(
                    "Configured model is not available through Codex. Run ./show setup to choose an available model."
                )
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.server.close()

    def generate_text(
        self, system_prompt: str, user_prompt: str, *, stage: str, prompt_type: str
    ) -> str:
        return self._generate(system_prompt, user_prompt, stage, prompt_type)

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        stage: str,
        prompt_type: str,
    ) -> T:
        # Keep schemas with defaults/optional fields compatible by validating locally.
        prompt = (
            system_prompt
            + "\nReturn only JSON matching this schema: "
            + json.dumps(response_model.model_json_schema())
        )
        raw = self._generate(prompt, user_prompt, stage, prompt_type)
        try:
            return response_model.model_validate_json(raw)
        except ValidationError:
            raise LLMInvalidResponseError(
                "Codex output did not match the requested schema. No subscription request was retried."
            ) from None

    def _generate(self, system: str, prompt: str, stage: str, kind: str) -> str:
        started = time.monotonic()
        error = None
        try:
            self.server.events.clear()
            result = self.server.request(
                "thread/start",
                {
                    "model": self.model,
                    "modelProvider": "openai",
                    "ephemeral": True,
                    "cwd": str(self.server.workspace),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "baseInstructions": "You are a text-only radio editorial assistant. Use only the supplied evidence. Do not call tools, browse, execute commands or access files. Return only the requested result.",
                    "developerInstructions": system,
                },
            )
            thread_id = result["thread"]["id"]
            turn = self.server.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                },
            )["turn"]["id"]
            deadline = time.monotonic() + self.server.timeout
            final = ""
            while True:
                event = self.server.event(deadline)
                params = event.get("params", {})
                if params.get("threadId") != thread_id:
                    continue
                if event.get("method") == "item/completed" and params.get("turnId") == turn:
                    item = params.get("item", {})
                    if item.get("type") == "agentMessage" and item.get("phase") in (
                        None,
                        "final_answer",
                    ):
                        final = item.get("text", "")
                if (
                    event.get("method") == "turn/completed"
                    and params.get("turn", {}).get("id") == turn
                ):
                    status = params["turn"].get("status")
                    if status != "completed":
                        raise LLMConnectionError(
                            "Codex generation failed or was interrupted. Check ./show codex-status for sign-in and usage limits; no request was retried."
                        )
                    if not isinstance(final, str) or not final.strip():
                        raise LLMInvalidResponseError(
                            "Codex completed without a final text response."
                        )
                    return final
        except (KeyError, TypeError, ValueError, AttributeError):
            error = "Codex returned malformed generation events. Update the CLI."
            self.close()
            raise LLMInvalidResponseError(error) from None
        except (LLMConnectionError, LLMInvalidResponseError) as exc:
            error = str(exc)
            self.close()
            raise
        finally:
            log_model_call(
                self.run_dir,
                {
                    "stage": stage,
                    "model": self.model,
                    "provider": "codex",
                    "prompt_type": kind,
                    "input_character_count": len(prompt),
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "success": error is None,
                    "error": error,
                },
            )
