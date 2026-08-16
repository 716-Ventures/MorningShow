from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class StageStatus(str, Enum):
    CREATED = "created"
    DISCOVERING = "discovering"
    EXTRACTING = "extracting"
    CLUSTERING = "clustering"
    SCORING = "scoring"
    RESEARCHING = "researching"
    PLANNING = "planning"
    WRITING = "writing"
    VERIFYING = "verifying"
    SYNTHESIZING = "synthesizing"
    MIXING = "mixing"
    COMPLETE = "complete"
    FAILED = "failed"


LEGAL_TRANSITIONS: dict[StageStatus, set[StageStatus]] = {
    StageStatus.CREATED: {StageStatus.DISCOVERING, StageStatus.FAILED},
    StageStatus.DISCOVERING: {StageStatus.EXTRACTING, StageStatus.FAILED},
    StageStatus.EXTRACTING: {StageStatus.CLUSTERING, StageStatus.FAILED},
    StageStatus.CLUSTERING: {StageStatus.SCORING, StageStatus.FAILED},
    StageStatus.SCORING: {StageStatus.RESEARCHING, StageStatus.FAILED},
    StageStatus.RESEARCHING: {StageStatus.PLANNING, StageStatus.FAILED},
    StageStatus.PLANNING: {StageStatus.WRITING, StageStatus.FAILED},
    StageStatus.WRITING: {StageStatus.VERIFYING, StageStatus.FAILED},
    StageStatus.VERIFYING: {StageStatus.SYNTHESIZING, StageStatus.FAILED},
    StageStatus.SYNTHESIZING: {StageStatus.MIXING, StageStatus.FAILED},
    StageStatus.MIXING: {StageStatus.COMPLETE, StageStatus.FAILED},
    StageStatus.COMPLETE: set(),
    StageStatus.FAILED: set(),
}


class RunRecord(BaseModel):
    run_id: str
    requested_date: date
    target_minutes: int
    status: StageStatus
    started_at: datetime
    completed_at: datetime | None = None
    failed_stage: str | None = None
    error: str | None = None
    artifact_paths: dict[str, str] = Field(default_factory=dict)


class LocationProfile(BaseModel):
    home: str
    local_scope: str


class Interest(BaseModel):
    name: str
    priority: int = Field(ge=1, le=5)
    depth: Literal["major_only", "normal", "deep"]
    subtopics: list[str] = Field(default_factory=list)
    inclusion_notes: str = ""
    exclusion_notes: str = ""

    @field_validator("name")
    @classmethod
    def non_empty_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("interest name may not be empty")
        return cleaned


class GlobalNewsProfile(BaseModel):
    include_major_us: bool
    include_major_world: bool
    threshold: Literal["major_only", "normal"] = "major_only"


class EditorialStyle(BaseModel):
    context_level: Literal["summary", "context", "analysis"]
    assume_subject_familiarity: bool
    ongoing_story_policy: Literal["changes_only", "brief_updates", "always_context"]
    tone: str = "conversational_intelligent"
    avoid_padding: bool = True


class ShowFormat(BaseModel):
    target_minutes: int = Field(ge=5, le=90)
    minimum_minutes: int = Field(ge=5, le=90)
    maximum_minutes: int = Field(ge=5, le=90)
    allow_variable_length: bool
    headline_open: bool
    watch_list_close: bool
    host_count: Literal[1, 2]

    @field_validator("maximum_minutes")
    @classmethod
    def duration_order(cls, value: int, info) -> int:
        minimum = info.data.get("minimum_minutes")
        if minimum is not None and value < minimum:
            raise ValueError("maximum_minutes must be greater than or equal to minimum_minutes")
        return value


class VoicePreferences(BaseModel):
    primary_voice: str | None = None
    secondary_voice: str | None = None
    pace: Literal["slow", "normal", "fast"] = "normal"


class EditorialProfile(BaseModel):
    schema_version: int = 1
    created_at: datetime
    updated_at: datetime
    location: LocationProfile
    interests: list[Interest]
    global_news: GlobalNewsProfile
    negative_preferences: list[str] = Field(default_factory=list)
    editorial_style: EditorialStyle
    show_format: ShowFormat
    voice_preferences: VoicePreferences


class FeedConfig(BaseModel):
    id: str
    name: str
    url: HttpUrl
    enabled: bool
    category_hints: list[str] = Field(default_factory=list)
    geography_hints: list[str] = Field(default_factory=list)
    priority: int = Field(ge=1, le=5)


class CandidateStory(BaseModel):
    candidate_id: str
    feed_id: str
    title: str
    url: str
    published_at: datetime | None = None
    retrieved_at: datetime
    publisher: str | None = None
    feed_summary: str | None = None
    category_hints: list[str] = Field(default_factory=list)
    geography_hints: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    candidate_id: str
    url: str
    final_url: str | None = None
    http_status: int | None = None
    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    text: str = ""
    word_count: int = 0
    extraction_status: Literal[
        "usable", "too_short", "fetch_failed", "unsupported_content", "parse_failed"
    ]
    failure_reason: str | None = None


class Cluster(BaseModel):
    cluster_id: str
    canonical_title: str
    candidate_ids: list[str]
    source_count: int
    earliest_published_at: datetime | None = None
    latest_published_at: datetime | None = None
    topic_hints: list[str] = Field(default_factory=list)
    fingerprint: str


class ScoreModifier(BaseModel):
    name: str
    delta: int
    rationale: str


class StoryScore(BaseModel):
    cluster_id: str
    relevance: int = Field(ge=0, le=100)
    importance: int = Field(ge=0, le=100)
    freshness: int = Field(ge=0, le=100)
    locality: int = Field(ge=0, le=100)
    novelty: int = Field(ge=0, le=100)
    confidence: int = Field(ge=0, le=100)
    matched_interests: list[str] = Field(default_factory=list)
    negative_matches: list[str] = Field(default_factory=list)
    reason: str
    final_score: int = Field(ge=0, le=100)
    raw_final_score: int | None = None
    modifiers: list[ScoreModifier] = Field(default_factory=list)


class SelectedStory(BaseModel):
    cluster_id: str
    reason: str
    estimated_seconds: int
    score: int


class SelectionResult(BaseModel):
    selected: list[SelectedStory]
    not_selected_high_score: list[SelectedStory] = Field(default_factory=list)


class DossierFact(BaseModel):
    claim: str
    supporting_candidate_ids: list[str] = Field(min_length=1)

    @field_validator("claim")
    @classmethod
    def non_empty_claim(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("fact claim may not be empty")
        return cleaned

    @field_validator("supporting_candidate_ids")
    @classmethod
    def unique_non_empty_supporting_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("supporting candidate ids may not be empty")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("supporting candidate ids must be unique")
        return cleaned


class StoryDossier(BaseModel):
    cluster_id: str
    working_headline: str
    what_happened: str
    what_is_new_today: str
    why_it_matters: str
    background_needed: str
    facts: list[DossierFact]
    uncertainties: list[str] = Field(default_factory=list)
    source_disagreements: list[str] = Field(default_factory=list)
    do_not_claim: list[str] = Field(default_factory=list)
    recommended_seconds: int
    source_ids: list[str]
    safe_for_scripting: bool = True

    @field_validator("source_ids")
    @classmethod
    def unique_non_empty_source_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("source ids may not be empty")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("source ids must be unique")
        return cleaned

    @model_validator(mode="after")
    def safe_dossier_has_grounded_facts(self) -> StoryDossier:
        if not self.safe_for_scripting:
            return self
        if not self.source_ids:
            raise ValueError("safe dossiers must include at least one source id")
        if not self.facts:
            raise ValueError("safe dossiers must include at least one sourced fact")
        source_ids = set(self.source_ids)
        fact_ids = {
            candidate_id
            for fact in self.facts
            for candidate_id in fact.supporting_candidate_ids
        }
        unknown_ids = fact_ids - source_ids
        if unknown_ids:
            raise ValueError(
                "fact supporting candidate ids must be present in dossier source ids: "
                + ", ".join(sorted(unknown_ids))
            )
        unused_ids = source_ids - fact_ids
        if unused_ids:
            raise ValueError(
                "safe dossier source ids must support at least one fact: "
                + ", ".join(sorted(unused_ids))
            )
        return self


class RundownSegment(BaseModel):
    segment_id: str
    type: Literal["opening", "headlines", "story", "quick_hits", "local", "sports", "watch_list", "closing"]
    title: str
    cluster_ids: list[str] = Field(default_factory=list)
    planned_seconds: int
    purpose: str


class Rundown(BaseModel):
    show_date: date
    target_seconds: int
    planned_seconds: int
    segments: list[RundownSegment]


class VerificationIssue(BaseModel):
    severity: Literal["low", "medium", "high"]
    category: str
    script_excerpt: str
    explanation: str
    supporting_source_ids: list[str] = Field(default_factory=list)
    required_action: str


class VerificationResult(BaseModel):
    status: Literal["pass", "fail"]
    issues: list[VerificationIssue] = Field(default_factory=list)
    corrected_script_required: bool = False

    @model_validator(mode="after")
    def pass_requires_no_unresolved_high_issues(self) -> VerificationResult:
        if self.status == "pass":
            if self.corrected_script_required:
                raise ValueError("passing verification cannot require a corrected script")
            high_issues = [issue for issue in self.issues if issue.severity == "high"]
            if high_issues:
                raise ValueError("passing verification cannot contain high-severity issues")
        return self


class VerifiedScript(BaseModel):
    verification: VerificationResult
    script: str


class AudioMetadata(BaseModel):
    voice: str
    text_hash: str
    duration_seconds: float
    path: Path
