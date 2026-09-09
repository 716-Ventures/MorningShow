"""Read project-local credentials without mutating the process environment."""

from __future__ import annotations

import os

from dotenv import dotenv_values

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
