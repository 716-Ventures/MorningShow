from __future__ import annotations

from pydantic import BaseModel, Field

from morning_radio.models import Rundown, StoryDossier, StoryScore, VerificationResult


class SameEventDecision(BaseModel):
    same_event: bool
    canonical_title: str
    reason: str


class StoryScoresResponse(BaseModel):
    scores: list[StoryScore]


class DossierResponse(BaseModel):
    dossier: StoryDossier


class RundownResponse(BaseModel):
    rundown: Rundown


class ScriptResponse(BaseModel):
    script: str = Field(min_length=1)


class VerificationResponse(BaseModel):
    verification: VerificationResult
    corrected_script: str | None = None


class FeedbackMemoryResponse(BaseModel):
    editorial_memory_markdown: str = Field(min_length=1)
