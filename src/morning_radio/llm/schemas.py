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
                decision = normalized["decision"].casefold()
                normalized["same_event"] = "same" in decision and "different" not in decision
        normalized.setdefault("canonical_title", "")
        normalized.setdefault(
            "reason",
            str(normalized.get("explanation") or normalized.get("decision") or "local model decision"),
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
        if not isinstance(value, dict) or "rundown" in value:
            return value
        if "show_date" in value and "segments" in value:
            return {"rundown": value}
        return value


class ScriptResponse(BaseModel):
    script: str = Field(min_length=1)


class VerificationResponse(BaseModel):
    verification: VerificationResult
    corrected_script: str | None = None


class FeedbackMemoryResponse(BaseModel):
    editorial_memory_markdown: str = Field(min_length=1)
