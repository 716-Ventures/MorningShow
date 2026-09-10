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
    model_validator,
)

from morning_radio.models import FeedConfig


class ConfigError(RuntimeError):
    pass


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMSettings(Configuration):
    provider: Literal["ollama", "openai"] = "ollama"
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


class ElevenLabsSettings(Configuration):
    model_id: str = Field(default="eleven_multilingual_v2", min_length=1, max_length=100)
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    stability: float = Field(default=0.5, ge=0, le=1)
    similarity_boost: float = Field(default=0.75, ge=0, le=1)
    style: float = Field(default=0, ge=0, le=1)
    use_speaker_boost: bool = True


class TTSSettings(Configuration):
    engine: Literal["kokoro", "tone", "elevenlabs"]
    voice: str | None = None
    secondary_voice: str | None = None
    speed: float = Field(gt=0, le=3)
    inter_block_pause_ms: int = Field(default=220, ge=0, le=2000)
    sentence_pause_ms: int = Field(default=140, ge=0, le=1000)
    max_chunk_words: int = Field(default=55, ge=15, le=150)
    pronunciation_overrides: dict[str, str] = Field(default_factory=dict)
    elevenlabs: ElevenLabsSettings = Field(default_factory=ElevenLabsSettings)

    @model_validator(mode="after")
    def valid_provider_settings(self) -> TTSSettings:
        if self.engine == "elevenlabs":
            if not self.voice:
                raise ValueError("ElevenLabs requires tts.voice to contain a Voice ID")
            for voice in (self.voice, self.secondary_voice):
                if voice is not None and (not voice.isascii() or not voice.isalnum()):
                    raise ValueError("ElevenLabs voices must be alphanumeric Voice IDs")
            if not 0.7 <= self.speed <= 1.2:
                raise ValueError("ElevenLabs speed must be between 0.7 and 1.2")
        return self

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
    settings = _validated(AppSettings, base / "config" / "app.yaml")
    preferences = load_provider_preferences(base)
    if preferences is not None:
        settings.llm = preferences.llm
    return settings


def load_production_settings(root: Path | None = None) -> ProductionSettings:
    base = root or repo_root()
    settings = _validated(ProductionSettings, base / "config" / "production.yaml")
    preferences = load_provider_preferences(base)
    if preferences is not None:
        settings.tts = preferences.tts
        settings.generate_audio = preferences.generate_audio
    return settings


class ProviderPreferences(Configuration):
    version: Literal[1] = 1
    llm: LLMSettings
    tts: TTSSettings
    generate_audio: bool


def load_provider_preferences(root: Path) -> ProviderPreferences | None:
    path = root / "config" / "providers.local.yaml"
    return _validated(ProviderPreferences, path) if path.exists() else None


def load_feed_settings(root: Path | None = None) -> FeedSettings:
    base = root or repo_root()
    return _validated(FeedSettings, base / "config" / "feeds.yaml")
