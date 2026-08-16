from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, HttpUrl, ValidationError, field_validator

from morning_radio.models import FeedConfig


class ConfigError(RuntimeError):
    pass


class LLMSettings(BaseModel):
    base_url: HttpUrl
    model: str
    timeout_seconds: int = Field(gt=0)


class NewsSettings(BaseModel):
    request_timeout_seconds: int = Field(gt=0)
    max_html_bytes: int = Field(gt=1024)
    minimum_article_words: int = Field(ge=1)
    minimum_usable_articles: int = Field(ge=1)
    candidate_max_age_hours: int = Field(gt=0)
    max_candidates: int = Field(gt=0)
    max_articles_to_extract: int = Field(gt=0)
    concurrency: int = Field(default=8, gt=0, le=32)


class SelectionSettings(BaseModel):
    major_news_importance_threshold: int = Field(ge=0, le=100)
    dossier_source_preference: int = Field(ge=1)
    maximum_selected_stories: int = Field(gt=0)


class VerificationSettings(BaseModel):
    maximum_correction_cycles: int = Field(ge=0, le=5)


class AppSettings(BaseModel):
    llm: LLMSettings
    news: NewsSettings
    selection: SelectionSettings
    verification: VerificationSettings


class TTSSettings(BaseModel):
    engine: str
    voice: str | None = None
    secondary_voice: str | None = None
    speed: float = Field(gt=0, le=3)


class AudioSettings(BaseModel):
    bitrate_kbps: int = Field(gt=0)
    sample_rate_hz: int
    channels: int = Field(ge=1, le=2)
    loudness_target_lufs: int

    @field_validator("sample_rate_hz")
    @classmethod
    def supported_sample_rate(cls, value: int) -> int:
        if value not in {44100, 48000}:
            raise ValueError("sample_rate_hz must be 44100 or 48000")
        return value


class AssetSettings(BaseModel):
    opening_optional: bool
    closing_optional: bool
    bumpers_optional: bool
    beds_optional: bool


class ProductionSettings(BaseModel):
    tts: TTSSettings
    audio: AudioSettings
    assets: AssetSettings


class FeedSettings(BaseModel):
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


def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Missing required config file: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config file must contain a mapping: {path}")
    return data


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
