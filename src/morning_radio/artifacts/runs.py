from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from morning_radio import db
from morning_radio.artifacts.io import atomic_write_json as write_atomic_json
from morning_radio.logging import log_line
from morning_radio.models import LEGAL_TRANSITIONS, RunRecord, StageStatus
from morning_radio.settings import repo_root


class RunTransitionError(RuntimeError):
    pass


def atomic_write_json(path: Path, payload: dict) -> None:
    write_atomic_json(path, payload)


def generate_run_id(now: datetime | None = None) -> str:
    local_now = now or datetime.now().astimezone()
    return f"{local_now.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"


@dataclass
class RunContext:
    root: Path
    run_dir: Path
    record: RunRecord
    stage_started_at: datetime | None = None

    @property
    def run_json_path(self) -> Path:
        return self.run_dir / "run.json"

    def save(self) -> None:
        atomic_write_json(self.run_json_path, self.record.model_dump(mode="json"))

    def transition(self, status: StageStatus) -> None:
        if status not in LEGAL_TRANSITIONS[self.record.status]:
            raise RunTransitionError(f"Cannot transition {self.record.status} -> {status}")
        self._log_stage_completed()
        self.record.status = status
        self.save()
        self.stage_started_at = datetime.now().astimezone()
        log_line(self.run_dir, f"stage={status.value} status=started")

    def register_artifact(self, name: str, path: Path) -> None:
        try:
            value = str(path.relative_to(self.root))
        except ValueError:
            value = str(path)
        self.record.artifact_paths[name] = value
        self.save()

    def fail(self, stage: str, error: str) -> None:
        self._log_stage_completed(status="failed")
        self.record.status = StageStatus.FAILED
        self.record.failed_stage = stage
        self.record.error = error
        self.record.completed_at = datetime.now().astimezone()
        self.save()
        log_line(self.run_dir, f"stage={stage} status=failed error={error}")
        db.complete_run(self.root / "data" / "app.db", self.record.run_id, "failed")

    def complete(self) -> None:
        if StageStatus.COMPLETE not in LEGAL_TRANSITIONS[self.record.status]:
            raise RunTransitionError(f"Cannot complete from {self.record.status}")
        self._log_stage_completed()
        self.record.status = StageStatus.COMPLETE
        self.record.completed_at = datetime.now().astimezone()
        self.save()
        log_line(self.run_dir, "stage=complete status=complete")
        db.complete_run(self.root / "data" / "app.db", self.record.run_id, "complete")

    def _log_stage_completed(self, status: str = "completed") -> None:
        if self.record.status in {StageStatus.CREATED, StageStatus.COMPLETE, StageStatus.FAILED}:
            return
        elapsed_ms = ""
        if self.stage_started_at is not None:
            elapsed = datetime.now(UTC) - self.stage_started_at.astimezone(UTC)
            elapsed_ms = f" elapsed_ms={round(elapsed.total_seconds() * 1000)}"
        log_line(self.run_dir, f"stage={self.record.status.value} status={status}{elapsed_ms}")


def create_run(requested_date: date, target_minutes: int, root: Path | None = None) -> RunContext:
    base = root or repo_root()
    started = datetime.now().astimezone()
    run_id = generate_run_id(started)
    run_dir = base / "runs" / requested_date.isoformat() / run_id
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "extracted").mkdir(exist_ok=True)
    (run_dir / "dossiers").mkdir(exist_ok=True)
    (run_dir / "raw-audio").mkdir(exist_ok=True)
    (run_dir / "mix").mkdir(exist_ok=True)
    record = RunRecord(
        run_id=run_id,
        requested_date=requested_date,
        target_minutes=target_minutes,
        status=StageStatus.CREATED,
        started_at=started,
    )
    context = RunContext(root=base, run_dir=run_dir, record=record)
    context.save()
    db.record_run(base / "data" / "app.db", run_id, requested_date.isoformat(), "created", run_dir)
    log_line(run_dir, "stage=created status=created")
    return context
