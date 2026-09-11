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
    shutil.copytree(
        root / "config", tmp_path / "config", ignore=shutil.ignore_patterns("*.local.yaml")
    )


def test_valid_config_loads(tmp_path: Path) -> None:
    copy_config(tmp_path)
    settings = load_app_settings(tmp_path)
    feeds = load_feed_settings(tmp_path)
    production = load_production_settings(tmp_path)
    assert settings.news.minimum_article_words > 0
    assert any(feed.enabled for feed in feeds.feeds)
    assert production.generate_audio is True
    assert production.tts.inter_block_pause_ms == 220
    assert production.tts.engine == "elevenlabs"
    assert production.tts.voice == "AkzTpEeeEWvyZf4umyCJ"
    assert production.tts.sentence_pause_ms == 0
    assert production.tts.max_chunk_words == 150
    assert production.tts.pronunciation_overrides == {}


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


@pytest.mark.parametrize("field,value", [("engine", "unknown"), ("speeed", 1.0)])
def test_tts_config_rejects_unknown_values(tmp_path, field, value):
    import json

    copy_config(tmp_path)
    production = load_production_settings(tmp_path).model_dump(mode="json")
    production["tts"][field] = value
    (tmp_path / "config/production.yaml").write_text(json.dumps(production))
    with pytest.raises(ConfigError):
        load_production_settings(tmp_path)


@pytest.mark.parametrize("content", ["[1, 2]", "1: value", "false", "invalid: ["])
def test_config_requires_valid_string_keyed_mapping(tmp_path, content):
    from morning_radio.settings import load_yaml

    path = tmp_path / "bad.yaml"
    path.write_text(content)
    with pytest.raises(ConfigError):
        load_yaml(path)
