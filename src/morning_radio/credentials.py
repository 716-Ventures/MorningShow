"""Read project-local credentials without mutating the process environment."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from dotenv import dotenv_values, set_key

from morning_radio.settings import ConfigError, repo_root


def read_credential(name: str) -> str:
    if name in os.environ:
        return os.environ[name].strip()
    path = repo_root() / ".env"
    try:
        # An explicit path avoids loading another project's secrets when CWD differs.
        # Disable interpolation so dollar signs in credentials remain literal.
        with path.open(encoding="utf-8") as stream:
            values = dotenv_values(stream=stream, interpolate=False)
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError):
        raise ConfigError(
            "Cannot read the project .env file; check its encoding and permissions."
        ) from None
    return (values.get(name) or "").strip()


def save_credentials(root: Path, values: dict[str, str]) -> None:
    """Preserve existing dotenv entries; atomically replace with owner-only permissions."""
    if not values:
        return
    if set(values) - {"OPENAI_API_KEY", "ELEVENLABS_API_KEY", "AI_GATEWAY_API_KEY"}:
        raise ValueError("Unsupported credential name")
    path = root / ".env"
    temporary = None
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        with NamedTemporaryFile("w", encoding="utf-8", dir=root, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(existing)
        for name, value in values.items():
            set_key(temporary, name, value, quote_mode="always")
        temporary.chmod(0o600)
        temporary.replace(path)
    except (OSError, UnicodeError):
        raise ConfigError(
            "Cannot save project .env credentials; check permissions and encoding."
        ) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
