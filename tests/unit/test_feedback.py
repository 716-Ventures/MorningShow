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


@pytest.mark.parametrize("owned", [True, False])
@pytest.mark.parametrize("fails", [True, False])
def test_feedback_client_ownership_and_failure(monkeypatch, tmp_path, owned, fails):
    from morning_radio.llm.client import LLMError, OllamaClient
    from morning_radio.settings import load_app_settings

    class Client(OllamaClient):
        def __init__(self):
            self.model = "test"
            self.closed = False

        def close(self):
            self.closed = True

        def generate_structured(self, *args, **kwargs):
            if fails:
                raise LLMError("failed")
            return args[2](editorial_memory_markdown="Keep sources clear.")

    client = Client()
    monkeypatch.setattr(feedback, "load_app_settings", lambda root: load_app_settings())
    monkeypatch.setattr(feedback, "build_llm_client", lambda *args: client)
    if fails:
        with pytest.raises(LLMError):
            update_editorial_memory("old", answers(), tmp_path, None if owned else client)
    else:
        assert "Keep sources clear" in update_editorial_memory(
            "old", answers(), tmp_path, None if owned else client
        )
    assert client.closed == owned


def test_feedback_saves_history_and_memory_backup(monkeypatch, tmp_path):
    from morning_radio.profile.compiler import memory_path

    path = tmp_path / "data/app.db"
    db.initialize(path)
    db.record_run(path, "run-001", "2026-09-09", "complete", tmp_path / "run")
    old = "# Editorial Memory\n\nOld rule.\n"
    memory_path(tmp_path).write_text(old)
    monkeypatch.setattr(typer, "prompt", lambda *args: "test answer")
    feedback.record_feedback(root=tmp_path, llm=MemoryLLM())
    assert memory_path(tmp_path).with_suffix(".md.bak").read_text() == old
    assert "practical developer" in memory_path(tmp_path).read_text()
