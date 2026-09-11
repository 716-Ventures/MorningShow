from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

import httpx
from pydantic import BaseModel

from morning_radio.audio.elevenlabs import ElevenLabsTTS
from morning_radio.audio.tts import build_tts_adapter, kokoro_importable
from morning_radio.credentials import read_credential
from morning_radio.settings import AppSettings, ProductionSettings


@dataclass(frozen=True)
class DependencyCheck:
    name: str
    ok: bool
    detail: str
    action: str = ""


class DependencyPreflightError(RuntimeError):
    def __init__(self, checks: list[DependencyCheck]) -> None:
        self.checks = checks
        failures = [check for check in checks if not check.ok]
        message = "; ".join(
            f"{check.name}: {check.detail}. {check.action}".strip() for check in failures
        )
        super().__init__(message or "Dependency preflight failed.")


class ModelTag(BaseModel):
    name: str


class ModelTags(BaseModel):
    models: list[ModelTag]


def check_ffmpeg() -> list[DependencyCheck]:
    checks: list[DependencyCheck] = []
    for binary in ("ffmpeg", "ffprobe"):
        path = shutil.which(binary)
        if path is None:
            checks.append(
                DependencyCheck(
                    binary,
                    False,
                    "not found on PATH",
                    "Install FFmpeg and ensure ffmpeg/ffprobe are on PATH.",
                )
            )
            continue
        try:
            subprocess.run(
                [path, "-version"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            checks.append(DependencyCheck(binary, True, path))
        except (OSError, subprocess.SubprocessError) as exc:
            checks.append(
                DependencyCheck(binary, False, str(exc), "Reinstall or repair the FFmpeg binary.")
            )
    return checks


def check_llm(app_settings: AppSettings) -> list[DependencyCheck]:
    if app_settings.llm.provider == "codex":
        from morning_radio.llm.codex import available_models, subscription_status
        from morning_radio.llm.codex_rpc import CodexRPC
        from morning_radio.settings import repo_root

        try:
            with CodexRPC(repo_root()) as server:
                detail = subscription_status(server)
                found = app_settings.llm.model in available_models(server)
            return [
                DependencyCheck(
                    "ChatGPT via Codex",
                    found,
                    detail,
                    "Run ./show setup if the selected model is unavailable.",
                )
            ]
        except (RuntimeError, OSError) as exc:
            return [
                DependencyCheck(
                    "ChatGPT via Codex",
                    False,
                    str(exc),
                    "Run ./show codex-login, then ./show codex-status.",
                )
            ]
    if app_settings.llm.provider == "openai":
        key = read_credential("OPENAI_API_KEY")
        return [
            DependencyCheck(
                "OpenAI credential configured",
                bool(key),
                "Credential present; billing and model access are not verified."
                if key
                else "Missing key",
                "Set OPENAI_API_KEY in .env. Generation requires API billing; ChatGPT plans do not include it.",
            )
        ]
    try:
        response = httpx.get(f"{str(app_settings.llm.base_url).rstrip('/')}/api/tags", timeout=5)
        response.raise_for_status()
        names = {model.name for model in ModelTags.model_validate(response.json()).models}
        model_found = app_settings.llm.model in names
        return [
            DependencyCheck("Ollama reachable", True, str(app_settings.llm.base_url)),
            DependencyCheck(
                "Configured LLM model",
                model_found,
                app_settings.llm.model,
                f"Pull or configure model {app_settings.llm.model}.",
            ),
        ]
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return [
            DependencyCheck(
                "Ollama reachable",
                False,
                str(exc),
                "Start Ollama and confirm the configured base URL.",
            ),
            DependencyCheck(
                "Configured LLM model",
                False,
                app_settings.llm.model,
                f"Pull or configure model {app_settings.llm.model}.",
            ),
        ]


def check_tts(production: ProductionSettings) -> list[DependencyCheck]:
    if production.tts.engine == "elevenlabs":
        adapter = None
        try:
            adapter = build_tts_adapter("elevenlabs", settings=production.tts)
            voices = adapter.available_voices()
            return [DependencyCheck("ElevenLabs voice access", True, ", ".join(voices))]
        except RuntimeError as exc:
            return [
                DependencyCheck(
                    "ElevenLabs voice access",
                    False,
                    str(exc),
                    "Set ELEVENLABS_API_KEY and grant Voices Read and Text to Speech permissions.",
                )
            ]
        finally:
            if isinstance(adapter, ElevenLabsTTS):
                adapter.close()
    if production.tts.engine != "kokoro":
        return [DependencyCheck("TTS backend", True, production.tts.engine)]
    if not kokoro_importable():
        return [
            DependencyCheck(
                "TTS backend",
                False,
                "kokoro import failed",
                "Install the Kokoro optional runtime before generating audio.",
            )
        ]
    try:
        adapter = build_tts_adapter(production.tts.engine)
        voices = adapter.available_voices()
        voice = production.tts.voice or "af_heart"
        checks = [
            DependencyCheck("TTS backend", True, "kokoro import"),
            DependencyCheck(
                "Configured TTS voice",
                voice in voices,
                voice,
                f"Choose one of: {', '.join(voices)}.",
            ),
        ]
        if production.tts.secondary_voice is not None:
            secondary = production.tts.secondary_voice
            checks.append(
                DependencyCheck(
                    "Secondary TTS voice",
                    secondary in voices,
                    secondary,
                    f"Choose one of: {', '.join(voices)}.",
                )
            )
        return checks
    except RuntimeError as exc:
        return [
            DependencyCheck(
                "Configured TTS voice",
                False,
                str(exc),
                "Install the Kokoro optional runtime and configure a valid voice.",
            )
        ]


def morning_preflight(
    app_settings: AppSettings, production: ProductionSettings
) -> list[DependencyCheck]:
    checks = [*check_llm(app_settings)]
    if production.generate_audio:
        checks.extend(check_tts(production))
        checks.extend(check_ffmpeg())
    failures = [check for check in checks if not check.ok]
    if failures:
        raise DependencyPreflightError(checks)
    return checks
