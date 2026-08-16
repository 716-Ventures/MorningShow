from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  requested_date TEXT NOT NULL,
  status TEXT NOT NULL,
  run_path TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS story_history (
  cluster_fingerprint TEXT PRIMARY KEY,
  canonical_title TEXT NOT NULL,
  first_seen_date TEXT NOT NULL,
  last_seen_date TEXT NOT NULL,
  last_included_date TEXT,
  include_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS feedback_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  created_at TEXT NOT NULL,
  overall TEXT NOT NULL,
  worked TEXT NOT NULL,
  change TEXT NOT NULL,
  should_not_include TEXT NOT NULL,
  missing_story TEXT NOT NULL,
  length_feedback TEXT NOT NULL,
  production_feedback TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def initialize(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (1, datetime.now().astimezone().isoformat()),
        )


def record_run(db_path: Path, run_id: str, requested_date: str, status: str, run_path: Path) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO runs(run_id, requested_date, status, run_path, started_at, completed_at)
            VALUES (?, ?, ?, ?, COALESCE((SELECT started_at FROM runs WHERE run_id = ?), ?), NULL)
            """,
            (
                run_id,
                requested_date,
                status,
                str(run_path),
                run_id,
                datetime.now().astimezone().isoformat(),
            ),
        )


def complete_run(db_path: Path, run_id: str, status: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE runs SET status = ?, completed_at = ? WHERE run_id = ?",
            (status, datetime.now().astimezone().isoformat(), run_id),
        )


def latest_completed_run(db_path: Path) -> str | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT run_id FROM runs WHERE status = 'complete' ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
    return None if row is None else str(row["run_id"])


def insert_feedback(db_path: Path, values: dict[str, str | None]) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO feedback_sessions(
              run_id, created_at, overall, worked, change, should_not_include,
              missing_story, length_feedback, production_feedback
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values.get("run_id"),
                datetime.now().astimezone().isoformat(),
                values["overall"],
                values["worked"],
                values["change"],
                values["should_not_include"],
                values["missing_story"],
                values["length_feedback"],
                values["production_feedback"],
            ),
        )


def update_story_history(
    db_path: Path, fingerprints: list[tuple[str, str]], included_date: str
) -> None:
    with connect(db_path) as conn:
        for fingerprint, title in fingerprints:
            conn.execute(
                """
                INSERT INTO story_history(
                  cluster_fingerprint, canonical_title, first_seen_date,
                  last_seen_date, last_included_date, include_count
                ) VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(cluster_fingerprint) DO UPDATE SET
                  canonical_title = excluded.canonical_title,
                  last_seen_date = excluded.last_seen_date,
                  last_included_date = excluded.last_included_date,
                  include_count = include_count + 1
                """,
                (fingerprint, title, included_date, included_date, included_date),
            )


def read_story_history(db_path: Path, fingerprints: list[str]) -> dict[str, sqlite3.Row]:
    if not fingerprints:
        return {}
    placeholders = ",".join("?" for _ in fingerprints)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT cluster_fingerprint, canonical_title, first_seen_date, last_seen_date,
                   last_included_date, include_count
            FROM story_history
            WHERE cluster_fingerprint IN ({placeholders})
            """,
            fingerprints,
        ).fetchall()
    return {str(row["cluster_fingerprint"]): row for row in rows}
