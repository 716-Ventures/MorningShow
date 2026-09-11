"""Bounded JSON-lines transport for an owned, isolated Codex app-server process."""

from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any

from morning_radio.llm.client import LLMConnectionError

MAX_MESSAGE_BYTES = 2_000_000


class CodexRPC:
    def __init__(self, root: Path, timeout: float = 30):
        executable = shutil.which("codex")
        if executable is None:
            raise LLMConnectionError(
                "Codex CLI is missing. Install it, then run ./show codex-login."
            )
        state = root / "data" / "codex"
        self.workspace = state / "workspace"
        home = state / "home"
        for path in (state, home, self.workspace):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.chmod(0o700)
        # Do not inherit API keys, the caller's CODEX_HOME, or its agent configuration.
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "HOME",
                "USER",
                "LOGNAME",
                "TMPDIR",
                "SYSTEMROOT",
                "LANG",
                "LC_ALL",
            }
        }
        env["CODEX_HOME"] = str(home)
        overrides = {
            "forced_login_method": '"chatgpt"',
            "cli_auth_credentials_store": '"file"',
            "web_search": '"disabled"',
            "project_doc_max_bytes": "0",
            "features.shell_tool": "false",
            "features.unified_exec": "false",
            "features.code_mode": "false",
            "features.code_mode_host": "false",
            "features.apps": "false",
            "features.hooks": "false",
            "features.multi_agent": "false",
            "features.browser_use": "false",
            "features.computer_use": "false",
            "features.image_generation": "false",
        }
        command = [executable, "app-server"]
        for key, value in overrides.items():
            command.extend(["-c", f"{key}={value}"])
        self.timeout = timeout
        self._sequence = 0
        self._buffer = bytearray()
        self.events: deque[dict[str, Any]] = deque()
        self._selector = selectors.DefaultSelector()
        try:
            self.process = subprocess.Popen(
                command,
                cwd=self.workspace,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except OSError:
            self._selector.close()
            raise LLMConnectionError(
                "Codex app-server could not start. Check the CLI installation."
            ) from None
        assert self.process.stdout is not None
        assert self.process.stdin is not None
        os.set_blocking(self.process.stdin.fileno(), False)
        self._selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "morning_radio",
                        "title": "MorningShow",
                        "version": "0.1.0",
                    },
                    "capabilities": {
                        "optOutNotificationMethods": [
                            "item/agentMessage/delta",
                            "item/reasoning/textDelta",
                            "item/reasoning/summaryTextDelta",
                        ]
                    },
                },
            )
            self.send({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> CodexRPC:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._selector.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()

    def send(self, message: dict[str, Any]) -> None:
        raw = (json.dumps(message) + "\n").encode()
        if len(raw) > MAX_MESSAGE_BYTES:
            raise LLMConnectionError("Codex request exceeded the message size limit.")
        try:
            assert self.process.stdin is not None
            deadline = time.monotonic() + self.timeout
            with selectors.DefaultSelector() as writer:
                writer.register(self.process.stdin, selectors.EVENT_WRITE)
                while raw:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not writer.select(remaining):
                        raise LLMConnectionError("Codex timed out accepting a request.")
                    raw = raw[os.write(self.process.stdin.fileno(), raw) :]
        except (OSError, ValueError):
            raise LLMConnectionError(
                "Codex app-server disconnected. No generation was retried."
            ) from None

    def receive(self, deadline: float) -> dict[str, Any]:
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._selector.select(remaining):
                raise LLMConnectionError(
                    "Codex timed out. Check sign-in and usage limits before retrying."
                )
            assert self.process.stdout is not None
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise LLMConnectionError("Codex app-server exited before completing the request.")
            self._buffer.extend(chunk)
            if len(self._buffer) > MAX_MESSAGE_BYTES:
                raise LLMConnectionError("Codex response exceeded the message size limit.")
        line, _, rest = self._buffer.partition(b"\n")
        self._buffer = bytearray(rest)
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError
            if "method" in value and not isinstance(value.get("params", {}), dict):
                raise ValueError
        except (ValueError, UnicodeError):
            raise LLMConnectionError(
                "Codex returned invalid protocol data. Update the CLI."
            ) from None
        if "method" in value and "id" in value:
            self.send(
                {
                    "id": value["id"],
                    "error": {
                        "code": -32601,
                        "message": "MorningShow does not allow tools or interactive requests during generation.",
                    },
                }
            )
            raise LLMConnectionError(
                "Codex requested an unsupported tool or approval. Generation stopped."
            )
        return value

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        request_id = self._sequence
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            message = self.receive(deadline)
            if message.get("id") == request_id:
                if "error" in message:
                    raise LLMConnectionError(
                        f"Codex rejected {method}. Check sign-in, model access and CLI compatibility."
                    )
                result = message.get("result")
                if not isinstance(result, dict):
                    raise LLMConnectionError("Codex returned an invalid RPC result.")
                return result
            if len(self.events) >= 2048:
                raise LLMConnectionError("Codex sent too many unexpected notifications.")
            self.events.append(message)

    def event(self, deadline: float) -> dict[str, Any]:
        return self.events.popleft() if self.events else self.receive(deadline)
