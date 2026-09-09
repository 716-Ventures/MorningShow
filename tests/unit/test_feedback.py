from __future__ import annotations

from pathlib import Path

import pytest
import typer

from morning_radio import db
from morning_radio.profile import feedback
from morning_radio.profile.feedback import update_editorial_memory


class MemoryLLM:
    model = "test"

    def generate_text(self, *args, **kwargs):
        return ""

    def generate_structured(self, *args, **kwargs):
        response_model = args[2]
        return response_model(
            editorial_memory_markdown=(
                "# Editorial Memory\n\n"
                "- Prefer concise AI stories with practical developer implications.\n"
                "- Avoid celebrity lifestyle stories unless they are major business news.\n"
            )
        )


def answers() -> dict[str, str | None]:
    return {
        "run_id": "run-001",
        "overall": "better",
        "worked": "AI stories with developer impact",
        "change": "less celebrity coverage",
        "should_not_include": "celebrity lifestyle brand",
        "missing_story": "none",
        "length_feedback": "right length",
        "production_feedback": "voice was good",
    }


def test_update_editorial_memory_uses_llm_output(tmp_path: Path) -> None:
    updated = update_editorial_memory(
        "# Editorial Memory\n\nOld rule.\n", answers(), tmp_path, MemoryLLM()
    )
    assert "practical developer implications" in updated
    assert "Old rule" not in updated
    assert updated.startswith("# Editorial Memory")


def test_update_editorial_memory_falls_back_without_llm(tmp_path: Path) -> None:
    updated = update_editorial_memory("# Editorial Memory\n", answers(), tmp_path, llm=None)
    assert "Feedback for run-001" in updated
    assert updated.startswith("# Editorial Memory")


def test_record_feedback_rejects_incomplete_explicit_run_before_prompt(
    monkeypatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "data" / "app.db"
    db.initialize(db_path)
    db.record_run(db_path, "run-001", "2026-08-15", "created", tmp_path / "runs" / "run-001")
    monkeypatch.setattr(typer, "prompt", lambda *args, **kwargs: pytest.fail("prompted"))

    with pytest.raises(typer.BadParameter, match="not a completed run"):
        feedback.record_feedback("run-001", root=tmp_path)
