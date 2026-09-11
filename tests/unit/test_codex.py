from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, HttpUrl
from typer.testing import CliRunner

from morning_radio.cli import app
from morning_radio.dependencies import check_llm
from morning_radio.llm import codex
from morning_radio.llm.client import LLMConnectionError, LLMInvalidResponseError, build_llm_client
from morning_radio.llm.codex_rpc import MAX_MESSAGE_BYTES, CodexRPC
from morning_radio.settings import LLMSettings, load_app_settings


class FakeServer(CodexRPC):
    def __init__(self, root: Path, timeout: float = 30):
        self.workspace = root
        self.timeout = timeout
        self.events = deque()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.account: dict[str, Any] | None = {"type": "chatgpt", "planType": "plus"}
        self.reply = '{"answer":"yes"}'
        self.status = "completed"
        self.browser_ok = True
        self.catalog: dict[str, Any] = {"data": [{"model": "test-codex"}]}

    def close(self):
        self.closed = True

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, params))
        if method == "account/read":
            return {"account": self.account}
        if method == "model/list":
            return self.catalog
        if method == "account/rateLimits/read":
            return {
                "rateLimits": {"primary": {"usedPercent": 25}, "secondary": {"usedPercent": 100}}
            }
        if method == "account/login/start":
            self.events.append({"method": "unrelated", "params": {}})
            self.events.append(
                {
                    "method": "account/login/completed",
                    "params": {"loginId": "login", "success": self.browser_ok},
                }
            )
            self.events.append({"method": "account/updated", "params": {"authMode": "chatgpt"}})
            return {
                "loginId": "login",
                "authUrl": "https://auth.openai.com/authorize?state=secret-state",
            }
        if method == "thread/start":
            return {"thread": {"id": "thread"}}
        if method == "turn/start":
            self.events.extend(
                [
                    {"method": "turn/completed", "params": {"threadId": "unrelated"}},
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread",
                            "turnId": "turn",
                            "item": {
                                "type": "agentMessage",
                                "phase": "commentary",
                                "text": "do not use",
                            },
                        },
                    },
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread",
                            "turnId": "turn",
                            "item": {
                                "type": "agentMessage",
                                "phase": "final_answer",
                                "text": self.reply,
                            },
                        },
                    },
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": "thread",
                            "turn": {"id": "turn", "status": self.status},
                        },
                    },
                ]
            )
            return {"turn": {"id": "turn"}}
        return {}

    def event(self, deadline: float) -> dict[str, Any]:
        return self.events.popleft()


@pytest.fixture
def root(tmp_path, monkeypatch):
    shutil.copytree(
        Path(__file__).resolve().parents[2] / "config",
        tmp_path / "config",
        ignore=shutil.ignore_patterns("*.local.yaml"),
    )
    monkeypatch.setattr("morning_radio.cli.repo_root", lambda: tmp_path)
    monkeypatch.setattr("morning_radio.settings.repo_root", lambda: tmp_path)
    monkeypatch.setattr(codex, "repo_root", lambda: tmp_path)
    monkeypatch.delenv("MORNING_RADIO_FAKE_LLM", raising=False)
    return tmp_path


@pytest.fixture
def server(root, monkeypatch):
    peer = FakeServer(root)
    monkeypatch.setattr(codex, "CodexRPC", lambda *args: peer)
    monkeypatch.setattr("morning_radio.llm.codex_rpc.CodexRPC", lambda *args: peer)
    return peer


def settings():
    return LLMSettings(
        provider="codex",
        model="test-codex",
        base_url=HttpUrl("http://localhost:11434"),
        timeout_seconds=30,
    )


class Answer(BaseModel):
    answer: str


def test_codex_factory_routes_text_and_structured_output(root, server):
    client = build_llm_client(settings(), root)
    assert isinstance(client, codex.CodexClient)
    assert (
        client.generate_structured(
            "system", "question", Answer, stage="test", prompt_type="test"
        ).answer
        == "yes"
    )
    server.reply = "Hello radio"
    assert (
        client.generate_text("system", "question", stage="test", prompt_type="test")
        == "Hello radio"
    )
    threads = [params for method, params in server.calls if method == "thread/start"]
    assert len(threads) == 2
    assert all(params["ephemeral"] and params["sandbox"] == "read-only" for params in threads)
    assert "JSON" in threads[0]["developerInstructions"]
    assert not server.closed
    client.close()
    assert server.closed


@pytest.mark.parametrize("account", [None, {"type": "apiKey"}])
def test_codex_never_falls_back_to_api_auth(root, server, account):
    server.account = account
    with pytest.raises(LLMConnectionError, match="codex-login"):
        codex.CodexClient(settings(), root)
    assert server.closed
    assert not any(method == "thread/start" for method, _ in server.calls)


def test_unavailable_model_closes_process(root, server):
    server.catalog = {"data": [{"model": "different"}]}
    with pytest.raises(LLMConnectionError, match="not available"):
        codex.CodexClient(settings(), root)
    assert server.closed


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_failed_turn_is_not_retried(root, server, status):
    client = codex.CodexClient(settings(), root)
    server.status = status
    with pytest.raises(LLMConnectionError, match="no request was retried"):
        client.generate_text("s", "u", stage="test", prompt_type="test")
    assert sum(method == "turn/start" for method, _ in server.calls) == 1
    assert server.closed


@pytest.mark.parametrize("reply", ["", None])
def test_missing_final_text_fails(root, server, reply):
    server.reply = reply
    client = codex.CodexClient(settings(), root)
    with pytest.raises(LLMInvalidResponseError, match="final text"):
        client.generate_text("s", "u", stage="test", prompt_type="test")
    assert server.closed


def test_schema_validation_is_local_without_retries(root, server):
    server.reply = "{}"
    client = codex.CodexClient(settings(), root)
    with pytest.raises(LLMInvalidResponseError, match="schema"):
        client.generate_structured("s", "u", Answer, stage="test", prompt_type="test")
    assert sum(method == "turn/start" for method, _ in server.calls) == 1
    client.close()


def test_login_and_status_commands_do_not_generate(root, server, monkeypatch):
    urls = []
    monkeypatch.setattr(codex.webbrowser, "open", lambda url: urls.append(url) or True)
    result = CliRunner().invoke(app, ["codex-login"])
    assert result.exit_code == 0, result.output
    assert "secret-state" not in result.output
    assert urls == ["https://auth.openai.com/authorize?state=secret-state"]
    result = CliRunner().invoke(app, ["codex-status"])
    assert result.exit_code == 0, result.output
    assert "75% remaining" in result.output
    assert "test-codex" in result.output
    assert not any(method == "thread/start" for method, _ in server.calls)


@pytest.mark.parametrize("browser", [False, True])
def test_login_failure_cancels_pending_flow(root, server, monkeypatch, browser):
    monkeypatch.setattr(codex.webbrowser, "open", lambda url: browser)
    server.browser_ok = False
    result = CliRunner().invoke(app, ["codex-login"])
    assert result.exit_code == 1
    assert any(method == "account/login/cancel" for method, _ in server.calls)
    assert server.closed


def test_status_missing_login_actionable(root, server):
    server.account = None
    result = CliRunner().invoke(app, ["codex-status"])
    assert result.exit_code == 1
    assert "codex-login" in result.output


@pytest.mark.parametrize("fails", [False, True])
def test_logout_only_targets_owned_server(root, server, monkeypatch, fails):
    if fails:

        def fail(*args):
            raise LLMConnectionError("disconnected")

        monkeypatch.setattr(server, "request", fail)
    result = CliRunner().invoke(app, ["codex-logout"])
    assert result.exit_code == int(fails)
    assert server.closed
    if not fails:
        assert server.calls == [("account/logout", {})]


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"data": []},
        {
            "data": [
                {"hidden": True, "model": "hidden"},
                None,
                {"model": "image", "inputModalities": ["image"]},
            ]
        },
    ],
)
def test_invalid_or_empty_model_catalog(server, data):
    server.catalog = data
    with pytest.raises(LLMConnectionError):
        codex.available_models(server)


def test_model_pagination_and_limit(server, monkeypatch):
    responses = iter(
        [
            {"data": [{"model": "one"}], "nextCursor": "next"},
            {"data": [{"model": "one"}, {"model": "two", "inputModalities": None}]},
        ]
    )
    monkeypatch.setattr(server, "request", lambda *args: next(responses))
    assert codex.available_models(server) == ["one", "two"]
    monkeypatch.setattr(server, "request", lambda *args: {"data": [], "nextCursor": "repeat"})
    with pytest.raises(LLMConnectionError, match="pagination"):
        codex.available_models(server)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "https://[bad",
        "http://auth.openai.com/login",
        "https://evil.example/login",
        "https://user:password@auth.openai.com/login",
    ],
)
def test_sign_in_rejects_bad_urls_before_browser(server, monkeypatch, url):
    monkeypatch.setattr(server, "request", lambda *args: {"authUrl": url, "loginId": "login"})
    monkeypatch.setattr(
        codex.webbrowser, "open", lambda *args: pytest.fail("must not open browser")
    )
    with pytest.raises(LLMConnectionError):
        codex.sign_in(server)


def test_sign_in_requires_login_id(server, monkeypatch):
    monkeypatch.setattr(
        server, "request", lambda *args: {"authUrl": "https://auth.openai.com/login"}
    )
    with pytest.raises(LLMConnectionError, match="invalid sign-in data"):
        codex.sign_in(server)


@pytest.mark.parametrize(
    "limits",
    [
        {},
        {"rateLimitsByLimitId": {"codex": {"primary": None}}},
        {"rateLimitsByLimitId": {"codex": None}},
    ],
)
def test_unknown_usage_is_not_reported_as_zero(server, monkeypatch, limits):
    original = server.request
    monkeypatch.setattr(
        server,
        "request",
        lambda method, params: (
            limits if method == "account/rateLimits/read" else original(method, params)
        ),
    )
    assert "usage limits unavailable" in codex.subscription_status(server)


def test_invalid_usage_payload_is_actionable(server, monkeypatch):
    original = server.request
    monkeypatch.setattr(
        server,
        "request",
        lambda method, params: (
            {"rateLimitsByLimitId": ["bad"]}
            if method == "account/rateLimits/read"
            else original(method, params)
        ),
    )
    with pytest.raises(LLMConnectionError, match="invalid usage"):
        codex.subscription_status(server)


def test_malformed_generation_stops_and_closes(root, server, monkeypatch):
    client = codex.CodexClient(settings(), root)
    monkeypatch.setattr(server, "request", lambda *args: {})
    with pytest.raises(LLMInvalidResponseError, match="malformed generation"):
        client.generate_text("s", "u", stage="test", prompt_type="test")
    assert server.closed


@pytest.mark.parametrize("update_first", [False, True])
def test_sign_in_waits_for_account_update(server, monkeypatch, update_first):
    server.account = None
    monkeypatch.setattr(codex.webbrowser, "open", lambda url: True)
    events = deque(
        [
            {"method": "account/login/completed", "params": {"loginId": "other", "success": True}},
            {"method": "account/updated", "params": {"authMode": None}},
        ]
    )
    completion = {
        "method": "account/login/completed",
        "params": {"loginId": "login", "success": True},
    }
    update = {"method": "account/updated", "params": {"authMode": "chatgpt"}}
    events.extend([update, completion] if update_first else [completion, update])

    def next_event(deadline):
        event = events.popleft()
        if event is update:
            server.account = {"type": "chatgpt", "planType": "plus"}
        return event

    monkeypatch.setattr(server, "event", next_event)
    codex.sign_in(server)
    assert not events
    assert ("account/read", {"refreshToken": False}) in server.calls
    assert not any(method == "account/login/cancel" for method, _ in server.calls)


def test_sign_in_missing_account_update_times_out_and_cancels(server, monkeypatch):
    monkeypatch.setattr(codex.webbrowser, "open", lambda url: True)
    events = deque(
        [{"method": "account/login/completed", "params": {"loginId": "login", "success": True}}]
    )

    def next_event(deadline):
        if events:
            return events.popleft()
        raise LLMConnectionError("Codex request timed out.")

    monkeypatch.setattr(server, "event", next_event)
    with pytest.raises(LLMConnectionError, match="timed out"):
        codex.sign_in(server)
    assert ("account/login/cancel", {"loginId": "login"}) in server.calls
    assert not any(method == "account/read" for method, _ in server.calls)


@pytest.mark.parametrize("login", [True, False])
def test_setup_offers_browser_when_signed_out(root, server, monkeypatch, login):
    server.account = None

    def open_browser(url):
        server.account = {"type": "chatgpt", "planType": "plus"}
        return True

    monkeypatch.setattr(codex.webbrowser, "open", open_browser)
    answers = "mixed\ncodex\nnone\n" + ("y\n1\ny\n" if login else "n\n")
    result = CliRunner().invoke(app, ["setup"], input=answers)
    assert result.exit_code == 0, result.output
    assert (root / "config/providers.local.yaml").exists() is login


def test_setup_codex_failure_is_actionable(root, server, monkeypatch):
    monkeypatch.setattr(
        "morning_radio.llm.codex_rpc.CodexRPC",
        lambda *args: (_ for _ in ()).throw(LLMConnectionError("CLI is missing")),
    )
    result = CliRunner().invoke(app, ["setup"], input="mixed\ncodex\nnone\n")
    assert result.exit_code == 1
    assert "CLI is missing" in result.output


def test_setup_selects_subscription_without_api_key(root, server):
    result = CliRunner().invoke(app, ["setup"], input="mixed\ncodex\nnone\n1\ny\n")
    assert result.exit_code == 0, result.output
    assert "OPENAI_API_KEY" not in result.output
    assert load_app_settings(root).llm.provider == "codex"
    assert load_app_settings(root).llm.model == "test-codex"
    assert not (root / ".env").exists()


@pytest.mark.parametrize("connected", [True, False])
def test_doctor_checks_codex_not_ollama(root, server, connected):
    if not connected:
        server.account = None
    app_settings = load_app_settings(root)
    app_settings.llm = settings()
    checks = check_llm(app_settings)
    assert checks[0].ok is connected
    assert checks[0].name == "ChatGPT via Codex"
    assert server.closed


@pytest.fixture
def spawn_peer(monkeypatch):
    original = subprocess.Popen
    children = []
    environments = []

    def configure(mode):
        monkeypatch.setattr("morning_radio.llm.codex_rpc.shutil.which", lambda name: sys.executable)

        def spawn(command, **kwargs):
            environments.append(kwargs["env"])
            assert 'forced_login_method="chatgpt"' in command
            child = original(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "fixtures/codex_server.py"),
                    mode,
                ],
                **kwargs,
            )
            children.append(child)
            return child

        monkeypatch.setattr("morning_radio.llm.codex_rpc.subprocess.Popen", spawn)
        return children, environments

    yield configure
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_real_transport_handshake_events_isolation_and_cleanup(tmp_path, monkeypatch, spawn_peer):
    children, environments = spawn_peer("normal")
    monkeypatch.setenv("OPENAI_API_KEY", "private-secret")
    monkeypatch.setenv("CODEX_HOME", "/do-not-use")
    with CodexRPC(tmp_path, 5) as peer:
        assert peer.request("account/read", {})["account"] is None
        assert peer.event(time.monotonic() + 5)["method"] == "notice"
        peer.events.clear()
        peer.send({"id": 999, "method": "echo", "params": {}})
        assert peer.event(time.monotonic() + 5)["method"] == "notice"
        assert peer.event(time.monotonic() + 5)["id"] == 999
        assert peer.request("echo", {"text": "a" * 100000})["echo"]["text"] == "a" * 100000
    assert children[0].poll() is not None
    assert "OPENAI_API_KEY" not in environments[0]
    assert environments[0]["CODEX_HOME"] == str(tmp_path / "data/codex/home")
    assert (tmp_path / "data/codex/home").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "mode",
    [
        "eof",
        "timeout",
        "malformed",
        "array",
        "large",
        "params",
        "tool",
        "flood",
        "error",
        "wrong-result",
    ],
)
def test_transport_failures_are_bounded_and_sanitized(tmp_path, spawn_peer, mode):
    children, _ = spawn_peer(mode)
    with pytest.raises(LLMConnectionError) as error:
        CodexRPC(tmp_path, 0.3)
    assert "private-secret" not in str(error.value)
    assert children[0].poll() is not None


def test_transport_request_size_and_closed_pipe(tmp_path, spawn_peer):
    spawn_peer("normal")
    peer = CodexRPC(tmp_path, 5)
    with pytest.raises(LLMConnectionError, match="size limit"):
        peer.send({"large": "x" * MAX_MESSAGE_BYTES})
    peer.close()
    with pytest.raises(LLMConnectionError, match="disconnected"):
        peer.send({"method": "test"})
    peer.close()


def test_missing_binary_and_spawn_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("morning_radio.llm.codex_rpc.shutil.which", lambda name: None)
    with pytest.raises(LLMConnectionError, match="missing"):
        CodexRPC(tmp_path)
    monkeypatch.setattr("morning_radio.llm.codex_rpc.shutil.which", lambda name: "/missing/codex")
    with pytest.raises(LLMConnectionError, match="could not start"):
        CodexRPC(tmp_path)
