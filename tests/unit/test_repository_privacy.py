from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from morning_radio.models import EditorialProfile
from morning_radio.profile.compiler import ProfileError, load_profile, save_profile

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_state_and_dotenv_variants_are_ignored(tmp_path: Path) -> None:
    shutil.copyfile(ROOT / ".gitignore", tmp_path / ".gitignore")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=10)
    private_paths = [
        ".env",
        ".env.local",
        ".env.production",
        ".env.backup",
        "data/profile.json",
        "data/profile.md",
        "data/editorial-memory.md",
        "data/editorial-memory.md.bak",
        "data/app.db",
        "data/app.db-wal",
        "data/app.db-shm",
        "data/codex/home/auth.json",
        "runs/example/script-final.md",
        "config/providers.local.yaml",
    ]
    public_paths = [".env.example", "examples/profile.json", "examples/profile.md"]
    result = subprocess.run(
        ["git", "-c", "core.excludesFile=/dev/null", "check-ignore", "--stdin"],
        cwd=tmp_path,
        input="\n".join(private_paths + public_paths) + "\n",
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert set(result.stdout.splitlines()) == set(private_paths)


def test_new_checkout_requires_profile_and_can_save_example(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="show configure"):
        load_profile(tmp_path)
    example = EditorialProfile.model_validate_json((ROOT / "examples/profile.json").read_text())
    save_profile(example, tmp_path)
    assert load_profile(tmp_path) == example
    assert (tmp_path / "data/profile.md").is_file()
    assert (
        (tmp_path / "data/editorial-memory.md").read_text().endswith("No durable feedback yet.\n")
    )
