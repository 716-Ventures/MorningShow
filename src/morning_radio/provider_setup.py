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
from rich.console import Console
from rich.table import Table
from rich.text import Text

from morning_radio.artifacts.io import atomic_write_text
from morning_radio.credentials import save_credentials
from morning_radio.settings import (
    ProviderPreferences,
    load_app_settings,
    load_production_settings,
    load_provider_preferences,
)

console = Console(highlight=False)


def _section(title: str) -> None:
    console.print()
    console.rule(Text(title, style="bold cyan"), align="left", style="cyan")


def _details(rows: list[tuple[str, str]]) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", max_width=16)
    table.add_column(overflow="fold")
    for label, value in rows:
        table.add_row(Text(label), Text(value))
    console.print(table)


CHOICE_DESCRIPTIONS = {
    "local": "Scripts and speech on this machine",
    "cloud": "Hosted scripts and speech; subscription or API usage",
    "mixed": "Choose script and speech providers separately",
    "keep": "Keep the currently configured providers",
    "ollama": "Local script generation",
    "openai": "Cloud scripts via OpenAI API; separate API billing",
    "codex": "ChatGPT subscription via Codex; subscription limits apply",
    "kokoro": "Local speech",
    "elevenlabs": "Cloud speech; credits and voice access required",
    "none": "Script only; no audio",
    "af_bella": "Bella",
    "af_heart": "Heart",
    "af_nicole": "Nicole",
    "am_adam": "Adam",
    "am_michael": "Michael",
    "bf_emma": "Emma",
    "bm_george": "George",
}


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
    _section("Your machine")
    _details(
        [
            ("System", f"{hardware.system} / {hardware.architecture}"),
            ("Memory", memory),
            ("Processors", str(hardware.cpu_count)),
            ("Disk available", f"{hardware.free_disk_gib:.1f} GiB"),
        ]
    )
    model = recommend_model(hardware)
    console.print()
    console.print(
        f"Local recommendation: {model + ' + Kokoro' if model else 'insufficient or unknown resources; no confident recommendation'}",
        style="bold green" if model else "yellow",
        markup=False,
    )
    console.print(
        "Sizing is conservative, not a speed guarantee. Other running apps and context size matter."
    )
    if hardware.system != "Darwin" or hardware.architecture != "arm64":
        console.print(
            "GPU acceleration is not verified; CPU-only generation can be slow.", style="yellow"
        )
    _section("Supported providers")
    _details(
        [
            ("Local scripts", "Ollama / qwen3:4b, qwen3:8b"),
            ("Cloud scripts", "OpenAI / gpt-4.1-mini, gpt-4.1"),
            ("Subscription", "ChatGPT via Codex / models discovered after sign-in"),
            ("Local speech", "Kokoro"),
            ("Cloud speech", "ElevenLabs"),
            ("Without speech", "Script-only production"),
        ]
    )
    _section("Before you choose")
    _details(
        [
            ("Local", "No API bill. News and first model downloads still need internet."),
            ("OpenAI", "API billing is separate from ChatGPT."),
            (
                "Codex",
                "Uses your eligible ChatGPT plan's Codex allowance. Not unlimited; speech is separate.",
            ),
            (
                "ElevenLabs",
                "Credits and voice access required. Voice Library API voices require a paid plan; a key alone does not grant access.",
            ),
            ("Cloud privacy", "Article/profile or speech text is sent to the selected provider."),
        ]
    )
    return hardware


def _choice(label: str, choices: tuple[str, ...], default: str) -> str:
    console.print()
    console.print(label, style="bold cyan", markup=False)
    for index, choice in enumerate(choices, start=1):
        line = Text(f"  {index}. ", style="cyan")
        line.append(choice, style="bold")
        if choice == default:
            line.append(" (default)", style="green")
        description = CHOICE_DESCRIPTIONS.get(choice)
        if description:
            line.append(f" - {description}")
        console.print(line)
    while True:
        value = typer.prompt("Select number or name", default=default).strip().lower()
        if value in {str(index) for index in range(1, len(choices) + 1)}:
            return choices[int(value) - 1]
        if value in choices:
            return value
        console.print("Choose one of the listed options.", style="yellow")


def run_setup(root: Path) -> bool:
    hardware = show_providers(root)
    app = load_app_settings(root)
    production = load_production_settings(root)
    _section("1 / Production")
    mode = _choice("Production mode", ("local", "cloud", "mixed", "keep"), "local")
    if mode != "keep":
        text_provider = "ollama" if mode == "local" else "openai"
        speech_provider = "kokoro" if mode == "local" else "elevenlabs"
        if mode == "mixed":
            text_provider = _choice("Scripts", ("ollama", "openai", "codex"), "ollama")
            speech_provider = _choice("Speech", ("kokoro", "elevenlabs", "none"), "kokoro")
        else:
            if not typer.confirm("Generate audio as well as scripts?", default=True):
                speech_provider = "none"
            if mode == "cloud":
                text_provider = _choice("Scripts", ("openai", "codex"), "openai")
        if text_provider == "ollama":
            recommended = recommend_model(hardware)
            if recommended is None and not typer.confirm(
                "Local performance cannot be recommended. Continue anyway?", default=False
            ):
                console.print("Setup cancelled. No settings were changed.", style="yellow")
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
        elif text_provider == "codex":
            from morning_radio.llm.codex import available_models, require_chatgpt, sign_in
            from morning_radio.llm.codex_rpc import CodexRPC

            with CodexRPC(root) as server:
                try:
                    require_chatgpt(server)
                except RuntimeError:
                    console.print(
                        "Browser sign-in is stored separately from preferences and remains connected if setup is later cancelled.",
                        style="yellow",
                    )
                    if not typer.confirm("Sign in with ChatGPT now?", default=True):
                        return False
                    console.print("Opening your browser. Waiting up to five minutes.")
                    sign_in(server)
                models = tuple(available_models(server))
                model = _choice("Codex model", models, models[0])
            app.llm = app.llm.model_copy(update={"provider": "codex", "model": model})
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
    _section("2 / Review")
    _details(
        [
            ("Scripts", f"{preferences.llm.provider} / {preferences.llm.model}"),
            (
                "Speech",
                preferences.tts.engine + " / " + str(preferences.tts.voice)
                if preferences.generate_audio
                else "disabled",
            ),
        ]
    )
    keys: dict[str, str] = {}
    required = []
    if preferences.llm.provider == "openai":
        required.append("OPENAI_API_KEY")
    if preferences.generate_audio and preferences.tts.engine == "elevenlabs":
        required.append("ELEVENLABS_API_KEY")
    if required:
        _section("Credentials")
    for name in required:
        if typer.confirm(
            f"Add or replace {name} in .env? (No keeps existing credentials)", default=False
        ):
            key = typer.prompt(name, hide_input=True, confirmation_prompt=True).strip()
            if key:
                keys[name] = key
        if name in os.environ:
            console.print(
                f"Warning: shell variable {name} overrides the .env value, even when empty.",
                style="yellow",
                markup=False,
            )
    console.print()
    if not typer.confirm("Save these production preferences?", default=True):
        console.print("Setup cancelled. No settings were changed.", style="yellow")
        return False
    save_credentials(root, keys)
    atomic_write_text(
        root / "config" / "providers.local.yaml",
        yaml.safe_dump(preferences.model_dump(mode="json"), sort_keys=False),
    )
    _section("3 / Ready")
    console.print("Saved config/providers.local.yaml", style="bold green")
    console.print("Editorial interests and base YAML files are unchanged.")
    console.print()
    if preferences.llm.provider == "ollama":
        console.print("Install/start Ollama, then run:")
        console.print(f"  ollama pull {preferences.llm.model}", style="cyan", markup=False)
    elif preferences.llm.provider == "openai":
        console.print("OPENAI_API_KEY is read from the project .env file.")
    else:
        console.print("ChatGPT subscription selected. Check allowance with ./show codex-status.")
    if preferences.generate_audio and preferences.tts.engine == "kokoro":
        console.print("Install the local speech runtime:")
        console.print("  uv sync --dev --extra tts --python 3.12", style="cyan")
    if preferences.generate_audio and preferences.tts.engine == "elevenlabs":
        console.print(
            "ELEVENLABS_API_KEY is read from the project .env file; ensure the voice is permitted by your plan."
        )
    console.print()
    console.print("Next: ./show doctor", style="bold cyan")
    console.print("Setup makes no generation requests or model downloads.")
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
