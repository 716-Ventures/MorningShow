from __future__ import annotations

import pytest

from morning_radio.showgen.script import ScriptError, spoken_blocks, validate_script


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
