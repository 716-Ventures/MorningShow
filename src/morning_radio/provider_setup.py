"""Interactive production preferences and conservative, offline hardware sizing."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import typer
import yaml
from pydantic import HttpUrl

from morning_radio.artifacts.io import atomic_write_text
from morning_radio.credentials import save_credentials
from morning_radio.settings import (
    ProviderPreferences,
    load_app_settings,
    load_production_settings,
    load_provider_preferences,
)


@dataclass(frozen=True)
class Hardware:
    system: str
    architecture: str
    memory_gib: float | None
    free_disk_gib: float
    cpu_count: int


def detect_hardware(root: Path) -> Hardware:
    memory = None
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                check=True,
                timeout=3,
            )
            memory = int(result.stdout.strip()) / 1024**3
        elif hasattr(os, "sysconf"):
            memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return Hardware(
        platform.system(),
        platform.machine(),
        memory,
        shutil.disk_usage(root).free / 1024**3,
        os.cpu_count() or 1,
    )


def recommend_model(hardware: Hardware) -> str | None:
    """Budget for OS, an 8K context and TTS; not a measured speed guarantee."""
    if hardware.memory_gib is None or hardware.memory_gib < 12 or hardware.free_disk_gib < 8:
        return None
    if hardware.memory_gib >= 32 and hardware.free_disk_gib >= 16:
        return "qwen3:8b"
    return "qwen3:4b"


def show_providers(root: Path) -> Hardware:
    hardware = detect_hardware(root)
    memory = f"{hardware.memory_gib:.1f} GiB" if hardware.memory_gib else "unknown RAM"
    typer.echo(
        f"Machine: {hardware.system} {hardware.architecture}, {memory}, "
        f"{hardware.cpu_count} CPUs, {hardware.free_disk_gib:.1f} GiB disk free"
    )
    model = recommend_model(hardware)
    typer.echo(
        f"Local recommendation: {model + ' + Kokoro' if model else 'insufficient or unknown resources; no confident recommendation'}"
    )
    typer.echo(
        "Sizing is conservative, not a speed guarantee. Other running apps and context size matter."
    )
    if hardware.system != "Darwin" or hardware.architecture != "arm64":
        typer.echo("GPU acceleration is not verified; CPU-only generation can be slow.")
    typer.echo(
        "Scripts: ollama (local: qwen3:4b, qwen3:8b); openai (cloud: gpt-4.1-mini, gpt-4.1)."
    )
    typer.echo("Speech: kokoro (local); elevenlabs (cloud); none (script only).")
    typer.echo(
        "Local inference has no API bill; news fetching and first model downloads still need internet."
    )
    typer.echo(
        "OpenAI requires API billing, separate from ChatGPT. Cloud receives article/profile or speech text."
    )
    typer.echo(
        "ElevenLabs needs credits and voice access. Voice Library API voices require a paid plan; a key alone does not grant access."
    )
    return hardware


def _choice(label: str, choices: tuple[str, ...], default: str) -> str:
    while True:
        value = typer.prompt(f"{label} ({', '.join(choices)})", default=default).strip().lower()
        if value in choices:
            return value
        typer.echo("Choose one of the listed options.")


def run_setup(root: Path) -> bool:
    hardware = show_providers(root)
    app = load_app_settings(root)
    production = load_production_settings(root)
    mode = _choice("Production mode", ("local", "cloud", "mixed", "keep"), "local")
    if mode != "keep":
        text_provider = "ollama" if mode == "local" else "openai"
        speech_provider = "kokoro" if mode == "local" else "elevenlabs"
        if mode == "mixed":
            text_provider = _choice("Scripts", ("ollama", "openai"), "ollama")
            speech_provider = _choice("Speech", ("kokoro", "elevenlabs", "none"), "kokoro")
        else:
            if not typer.confirm("Generate audio as well as scripts?", default=True):
                speech_provider = "none"
        if text_provider == "ollama":
            recommended = recommend_model(hardware)
            if recommended is None and not typer.confirm(
                "Local performance cannot be recommended. Continue anyway?", default=False
            ):
                return False
            model = _choice("Local model", ("qwen3:4b", "qwen3:8b"), recommended or "qwen3:4b")
            app.llm = app.llm.model_copy(
                update={
                    "provider": "ollama",
                    "model": model,
                    "base_url": HttpUrl("http://localhost:11434"),
                    "thinking": False,
                    "context_tokens": 8192,
                }
            )
        else:
            model = _choice("OpenAI model", ("gpt-4.1-mini", "gpt-4.1"), "gpt-4.1-mini")
            app.llm = app.llm.model_copy(
                update={
                    "provider": "openai",
                    "model": model,
                    "base_url": HttpUrl("https://api.openai.com/v1"),
                }
            )
        production.generate_audio = speech_provider != "none"
        if speech_provider == "kokoro":
            voice = _choice(
                "Kokoro voice",
                (
                    "af_bella",
                    "af_heart",
                    "af_nicole",
                    "am_adam",
                    "am_michael",
                    "bf_emma",
                    "bm_george",
                ),
                "af_bella",
            )
            production.tts = production.tts.model_copy(
                update={"engine": "kokoro", "voice": voice, "secondary_voice": None, "speed": 1.0}
            )
        elif speech_provider == "elevenlabs":
            voice = typer.prompt(
                "ElevenLabs Voice ID",
                default=production.tts.voice if production.tts.engine == "elevenlabs" else None,
            )
            production.tts = production.tts.model_copy(
                update={
                    "engine": "elevenlabs",
                    "voice": voice.strip(),
                    "secondary_voice": None,
                    "speed": 1.0,
                }
            )
    # Validate copied nested models before any persistent changes.
    preferences = ProviderPreferences.model_validate(
        {
            "llm": app.llm.model_dump(mode="json"),
            "tts": production.tts.model_dump(mode="json"),
            "generate_audio": production.generate_audio,
        }
    )
    typer.echo(f"Scripts: {preferences.llm.provider} / {preferences.llm.model}")
    typer.echo(
        f"Speech: {preferences.tts.engine + ' / ' + str(preferences.tts.voice) if preferences.generate_audio else 'disabled'}"
    )
    keys: dict[str, str] = {}
    required = []
    if preferences.llm.provider == "openai":
        required.append("OPENAI_API_KEY")
    if preferences.generate_audio and preferences.tts.engine == "elevenlabs":
        required.append("ELEVENLABS_API_KEY")
    for name in required:
        if typer.confirm(
            f"Add or replace {name} in .env? (No keeps existing credentials)", default=False
        ):
            key = typer.prompt(name, hide_input=True, confirmation_prompt=True).strip()
            if key:
                keys[name] = key
        if name in os.environ:
            typer.echo(f"Warning: shell variable {name} overrides the .env value, even when empty.")
    if not typer.confirm("Save these production preferences?", default=True):
        return False
    save_credentials(root, keys)
    atomic_write_text(
        root / "config" / "providers.local.yaml",
        yaml.safe_dump(preferences.model_dump(mode="json"), sort_keys=False),
    )
    typer.echo(
        "Saved config/providers.local.yaml. Editorial interests and base YAML files are unchanged."
    )
    if preferences.llm.provider == "ollama":
        typer.echo(f"Install/start Ollama, then run: ollama pull {preferences.llm.model}")
    else:
        typer.echo("Add OPENAI_API_KEY to the project .env file.")
    if preferences.generate_audio and preferences.tts.engine == "kokoro":
        typer.echo("Install the local speech runtime: uv sync --dev --extra tts --python 3.12")
    if preferences.generate_audio and preferences.tts.engine == "elevenlabs":
        typer.echo(
            "Add ELEVENLABS_API_KEY to the project .env file; ensure the voice is permitted by your plan."
        )
    typer.echo("Run ./show doctor next. Setup makes no cloud requests or model downloads.")
    return True


def ensure_setup(root: Path) -> None:
    if (
        os.environ.get("MORNING_RADIO_FIXTURE_RUN") == "1"
        or load_provider_preferences(root) is not None
    ):
        return
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Production setup required. Run ./show setup interactively before unattended generation."
        )
    if not run_setup(root):
        raise RuntimeError("Production setup cancelled; nothing was generated.")
