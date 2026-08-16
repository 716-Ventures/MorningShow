from __future__ import annotations

import pytest

from morning_radio.showgen.script import (
    ScriptError,
    estimate_spoken_seconds,
    spoken_blocks,
    validate_script,
)


def test_valid_script_blocks() -> None:
    script = "[HOST]\nGood morning.\n\n[PAUSE: 500]\n\n[HOST]\nThat is the show.\n"
    validate_script(script)
    assert spoken_blocks(script) == [("HOST", "Good morning."), ("HOST", "That is the show.")]


def test_unknown_directive_fails() -> None:
    with pytest.raises(ScriptError):
        validate_script("[HOST]\nHello.\n\n[LASERS]\n")


def test_spoken_list_fails() -> None:
    with pytest.raises(ScriptError):
        validate_script("[HOST]\n- Bullet copy\n")


def test_invalid_pause_argument_fails() -> None:
    with pytest.raises(ScriptError):
        validate_script("[HOST]\nHello.\n\n[PAUSE: nope]\n")


def test_pause_outside_allowed_range_fails() -> None:
    with pytest.raises(ScriptError):
        validate_script("[HOST]\nHello.\n\n[PAUSE: 99999]\n")


def test_missing_bumper_name_fails() -> None:
    with pytest.raises(ScriptError):
        validate_script("[HOST]\nHello.\n\n[BUMPER: ]\n")


def test_malformed_directive_fails() -> None:
    with pytest.raises(ScriptError):
        validate_script("[HOST]\nHello.\n\n[PAUSE: 500\n")


def test_estimate_spoken_seconds_counts_words_and_pauses() -> None:
    script = "[HOST]\nOne two three four.\n\n[PAUSE: 1000]\n"
    assert estimate_spoken_seconds(script, wpm=60) == 5
