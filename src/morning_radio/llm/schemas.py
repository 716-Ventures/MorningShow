from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from morning_radio.models import Rundown, StoryDossier, StoryScore, VerificationResult


class SameEventDecision(BaseModel):
    same_event: bool
    canonical_title: str
    reason: str

    @model_validator(mode="before")
    @classmethod
    def accept_common_local_model_keys(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if "same_event" not in normalized:
            if isinstance(normalized.get("are_same_event"), bool):
                normalized["same_event"] = normalized["are_same_event"]
            elif isinstance(normalized.get("related"), bool):
                normalized["same_event"] = normalized["related"]
            elif isinstance(normalized.get("decision"), str):
                decision = " ".join(normalized["decision"].casefold().replace("_", " ").split())
                if decision in {"same", "same event", "the same event"}:
                    normalized["same_event"] = True
                elif decision in {
                    "different",
                    "different event",
                    "different events",
                    "not same",
                    "not same event",
                    "not the same event",
                }:
                    normalized["same_event"] = False
        normalized.setdefault("canonical_title", "")
        normalized.setdefault(
            "reason",
            str(
                normalized.get("explanation")
                or normalized.get("decision")
                or "local model decision"
            ),
        )
        return normalized


class StoryScoresResponse(BaseModel):
    scores: list[StoryScore]


class DossierResponse(BaseModel):
    dossier: StoryDossier

    @model_validator(mode="before")
    @classmethod
    def accept_unwrapped_dossier(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "dossier" in value:
            return value
        if "cluster_id" in value and "facts" in value:
            return {"dossier": value}
        return value


class RundownResponse(BaseModel):
    rundown: Rundown

    @model_validator(mode="before")
    @classmethod
    def accept_unwrapped_rundown(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if "rundown" not in normalized and "show_date" in normalized and "segments" in normalized:
            normalized = {"rundown": normalized}
        rundown = normalized.get("rundown")
        if isinstance(rundown, dict) and isinstance(rundown.get("segments"), list):
            segment_seconds = [
                segment.get("planned_seconds")
                for segment in rundown["segments"]
                if isinstance(segment, dict)
            ]
            if segment_seconds and all(isinstance(seconds, int) for seconds in segment_seconds):
                total_seconds = sum(
                    seconds for seconds in segment_seconds if isinstance(seconds, int)
                )
                normalized["rundown"] = {
                    **rundown,
                    "planned_seconds": total_seconds,
                }
        return normalized


class ScriptResponse(BaseModel):
    script: str = Field(min_length=1)


class VerificationResponse(BaseModel):
    verification: VerificationResult
    corrected_script: str | None = None


class FeedbackMemoryResponse(BaseModel):
    editorial_memory_markdown: str = Field(min_length=1)
