from __future__ import annotations

from pathlib import Path

import pytest

from morning_radio.artifacts.io import atomic_write_text


def test_atomic_write_failure_preserves_destination_and_cleans_temp(
    monkeypatch, tmp_path: Path
) -> None:
    destination = tmp_path / "artifact.txt"
    destination.write_text("old", encoding="utf-8")
    original_replace = Path.replace

    def fail_replace(self: Path, target: Path) -> Path:
        if target == destination:
            raise OSError("simulated replace failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated"):
        atomic_write_text(destination, "new")

    assert destination.read_text(encoding="utf-8") == "old"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["artifact.txt"]
