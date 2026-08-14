from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def log_line(run_dir: Path, message: str) -> None:
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat()
    with (log_dir / "run.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message}\n")


def log_model_call(run_dir: Path, payload: dict[str, Any]) -> None:
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    event = {"timestamp": datetime.now().astimezone().isoformat(), **payload}
    with (log_dir / "model-calls.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
