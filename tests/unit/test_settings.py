from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from morning_radio.settings import (
    ConfigError,
    load_app_settings,
    load_feed_settings,
    load_production_settings,
)


def copy_config(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    shutil.copytree(root / "config", tmp_path / "config")


def test_valid_config_loads(tmp_path: Path) -> None:
    copy_config(tmp_path)
    settings = load_app_settings(tmp_path)
    feeds = load_feed_settings(tmp_path)
    production = load_production_settings(tmp_path)
    assert settings.news.minimum_article_words > 0
    assert any(feed.enabled for feed in feeds.feeds)
    assert production.generate_audio is False
    assert production.tts.inter_block_pause_ms == 220
    assert production.tts.sentence_pause_ms == 140
    assert production.tts.max_chunk_words == 55
    assert production.tts.pronunciation_overrides["OpenAI"] == "Open A I"


def test_invalid_numeric_boundary_fails(tmp_path: Path) -> None:
    copy_config(tmp_path)
    path = tmp_path / "config" / "app.yaml"
    path.write_text(
        path.read_text().replace("minimum_article_words: 200", "minimum_article_words: 0")
    )
    with pytest.raises(ConfigError):
        load_app_settings(tmp_path)


def test_missing_config_fails(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_app_settings(tmp_path)
