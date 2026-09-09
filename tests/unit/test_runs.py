from __future__ import annotations

import re
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from morning_radio import db
from morning_radio.artifacts.runs import RunTransitionError, create_run, generate_run_id
from morning_radio.models import StageStatus


def test_database_context_closes_and_rolls_back(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    db.initialize(path)
    with pytest.raises(RuntimeError), db.connect(path) as connection:
        connection.execute("INSERT INTO schema_migrations VALUES (2, 'test')")
        raise RuntimeError("rollback")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with db.connect(path) as connection:
        assert db.latest_migration_version(connection) == 1


def test_run_ids_match_format_and_are_unique() -> None:
    now = datetime(2026, 8, 14, 6, 15, 3, tzinfo=UTC)
    first = generate_run_id(now)
    second = generate_run_id(now)
    assert re.match(r"^20260814T061503-[0-9a-f]{6}$", first)
    assert first != second


def test_run_lifecycle_and_failure(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    db.initialize(tmp_path / "data" / "app.db")
    context = create_run(date(2026, 8, 14), 25, tmp_path)
    assert context.run_json_path.exists()
    context.transition(StageStatus.DISCOVERING)
    context.transition(StageStatus.EXTRACTING)
    log_text = (context.run_dir / "logs" / "run.log").read_text(encoding="utf-8")
    assert "stage=discovering status=completed elapsed_ms=" in log_text
    with pytest.raises(RunTransitionError):
        context.transition(StageStatus.WRITING)
    context.fail("extracting", "fixture failure")
    payload = context.run_json_path.read_text()
    assert '"status": "failed"' in payload
    assert '"failed_stage": "extracting"' in payload
    log_text = (context.run_dir / "logs" / "run.log").read_text(encoding="utf-8")
    assert "stage=extracting status=failed elapsed_ms=" in log_text


def test_db_tables_exist(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "app.db"
    db.initialize(db_path)
    with db.connect(db_path) as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"schema_migrations", "runs", "story_history", "feedback_sessions"} <= tables


def test_db_initialize_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "app.db"
    db.initialize(db_path)
    db.initialize(db_path)

    with db.connect(db_path) as conn:
        versions = conn.execute("SELECT version FROM schema_migrations").fetchall()

    assert [row["version"] for row in versions] == [1]


def test_feedback_rejects_orphan_run_id(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "app.db"
    db.initialize(db_path)

    with pytest.raises(ValueError, match="completed run"):
        db.insert_feedback(
            db_path,
            {
                "run_id": "missing",
                "overall": "same",
                "worked": "good",
                "change": "none",
                "should_not_include": "none",
                "missing_story": "none",
                "length_feedback": "right",
                "production_feedback": "good",
            },
        )


def test_completed_run_validation(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "app.db"
    db.initialize(db_path)
    run_path = tmp_path / "runs" / "run-1"
    db.record_run(db_path, "run-1", "2026-08-15", "created", run_path)

    assert not db.completed_run_exists(db_path, "run-1")

    db.complete_run(db_path, "run-1", "complete")

    assert db.completed_run_exists(db_path, "run-1")
