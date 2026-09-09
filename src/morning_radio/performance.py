"""Monotonic timing and explicit process/subprocess high-water memory metrics."""

from __future__ import annotations

import platform
import resource
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from morning_radio.artifacts.io import atomic_write_json


class Measurement(BaseModel):
    stage: str
    status: str
    elapsed_ms: float = Field(ge=0)
    process_peak_rss_bytes: int = Field(ge=0)
    subprocess_peak_rss_bytes: int = Field(ge=0)


def peak_memory() -> dict[str, int]:
    multiplier = 1 if sys.platform == "darwin" else 1024
    return {
        "process_peak_rss_bytes": int(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * multiplier
        ),
        "subprocess_peak_rss_bytes": int(
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * multiplier
        ),
    }


def write_performance(path: Path, run_id: str, measurements: list[Measurement]) -> None:
    atomic_write_json(
        path,
        {
            "schema_version": 1,
            "run_id": run_id,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "memory_scope": "Process lifetime high-water marks, not summed concurrent RSS. External Ollama server memory is not included.",
            "stages": [item.model_dump() for item in measurements],
        },
    )
