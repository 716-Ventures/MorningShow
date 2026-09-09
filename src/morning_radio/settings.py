from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from morning_radio.models import FeedConfig


class ConfigError(RuntimeError):
    pass


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMSettings(Configuration):
    base_url: HttpUrl
    model: str
    timeout_seconds: int = Field(gt=0)
    thinking: bool | None = None
    context_tokens: int = Field(default=8192, ge=4096, le=32768)


class NewsSettings(Configuration):
    request_timeout_seconds: int = Field(gt=0)
    max_html_bytes: int = Field(gt=1024)
    minimum_article_words: int = Field(ge=1)
    minimum_usable_articles: int = Field(ge=1)
    candidate_max_age_hours: int = Field(gt=0)
    max_candidates: int = Field(gt=0)
    max_articles_to_extract: int = Field(gt=0)
    concurrency: int = Field(default=8, gt=0, le=32)


class SelectionSettings(Configuration):
    major_news_importance_threshold: int = Field(ge=0, le=100)
    dossier_source_preference: int = Field(ge=1)
    maximum_selected_stories: int = Field(gt=0)


class VerificationSettings(Configuration):
    maximum_correction_cycles: int = Field(ge=0, le=5)


class AppSettings(Configuration):
    llm: LLMSettings
    news: NewsSettings
    selection: SelectionSettings
    verification: VerificationSettings


class TTSSettings(Configuration):
    engine: Literal["kokoro", "tone"]
    voice: str | None = None
    secondary_voice: str | None = None
    speed: float = Field(gt=0, le=3)
    inter_block_pause_ms: int = Field(default=220, ge=0, le=2000)
    sentence_pause_ms: int = Field(default=140, ge=0, le=1000)
    max_chunk_words: int = Field(default=55, ge=15, le=150)
    pronunciation_overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("pronunciation_overrides")
    @classmethod
    def valid_pronunciation_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned = {source.strip(): spoken.strip() for source, spoken in value.items()}
        if any(not source or not spoken for source, spoken in cleaned.items()):
            raise ValueError("pronunciation overrides may not contain empty terms")
        return cleaned


class AudioSettings(Configuration):
    bitrate_kbps: Literal[32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
    sample_rate_hz: int
    channels: int = Field(ge=1, le=2)
    loudness_target_lufs: int = Field(ge=-70, le=-5)

    @field_validator("sample_rate_hz")
    @classmethod
    def supported_sample_rate(cls, value: int) -> int:
        if value not in {44100, 48000}:
            raise ValueError("sample_rate_hz must be 44100 or 48000")
        return value


class AssetSettings(Configuration):
    opening_optional: bool
    closing_optional: bool
    bumpers_optional: bool
    beds_optional: bool


class ProductionSettings(Configuration):
    generate_audio: bool = True
    tts: TTSSettings
    audio: AudioSettings
    assets: AssetSettings


class FeedSettings(Configuration):
    feeds: list[FeedConfig]

    @field_validator("feeds")
    @classmethod
    def unique_ids(cls, feeds: list[FeedConfig]) -> list[FeedConfig]:
        ids = [feed.id for feed in feeds]
        if len(ids) != len(set(ids)):
            raise ValueError("feed ids must be unique")
        return feeds


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        raise ConfigError(f"Missing required config file: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data: object = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    try:
        return TypeAdapter(dict[str, object]).validate_python(data, strict=True)
    except ValidationError as exc:
        raise ConfigError(f"Config file must contain a string-keyed mapping: {path}") from exc


def _validated[TSettings: BaseModel](model: type[TSettings], path: Path) -> TSettings:
    try:
        return model.model_validate(load_yaml(path))
    except ValidationError as exc:
        raise ConfigError(f"Invalid config in {path}: {exc}") from exc


def load_app_settings(root: Path | None = None) -> AppSettings:
    base = root or repo_root()
    return _validated(AppSettings, base / "config" / "app.yaml")


def load_production_settings(root: Path | None = None) -> ProductionSettings:
    base = root or repo_root()
    return _validated(ProductionSettings, base / "config" / "production.yaml")


def load_feed_settings(root: Path | None = None) -> FeedSettings:
    base = root or repo_root()
    return _validated(FeedSettings, base / "config" / "feeds.yaml")
