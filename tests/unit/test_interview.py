import pytest

from morning_radio.models import ShowFormat
from morning_radio.profile import interview
from morning_radio.profile.compiler import default_profile


def test_targeted_edit_does_not_restart_full_interview(monkeypatch):
    original = default_profile()
    edited = original.model_copy(
        update={"location": original.location.model_copy(update={"home": "Boston"})}
    )
    collected = []
    saved = []
    choices = iter(["edit", "yes"])
    monkeypatch.setattr(
        interview, "_collect_profile", lambda *args: collected.append(True) or original
    )
    monkeypatch.setattr(interview, "_targeted_edit", lambda value: edited)
    monkeypatch.setattr(interview, "_choice", lambda *args: next(choices))
    monkeypatch.setattr(interview, "save_profile", saved.append)
    assert interview.run_interview(original) == edited
    assert len(collected) == 1
    assert saved == [edited]


def test_cancel_does_not_save(monkeypatch):
    original = default_profile()
    saved = []
    monkeypatch.setattr(interview, "_collect_profile", lambda *args: original)
    monkeypatch.setattr(interview, "_choice", lambda *args: "cancel")
    monkeypatch.setattr(interview, "save_profile", saved.append)
    assert interview.run_interview(original) is None
    assert not saved


def test_setup_defaults_preserve_existing_preferences(monkeypatch):
    original = default_profile()
    original.global_news.include_major_us = False
    original.global_news.include_major_world = False
    original.editorial_style.assume_subject_familiarity = False
    monkeypatch.setattr(interview, "_ask", lambda prompt, default=None: default or "done")
    monkeypatch.setattr(interview, "_confirm", lambda prompt, default=True: default)
    result = interview._collect_profile(original, True)
    assert result.global_news == original.global_news
    assert result.editorial_style == original.editorial_style
    assert result.negative_preferences == original.negative_preferences
    assert result.interests == original.interests
    assert result.show_format == original.show_format


@pytest.mark.parametrize(
    "category", ["location", "interests", "news", "exclusions", "style", "format", "voice"]
)
def test_targeted_edit_preserves_defaults(monkeypatch, category):
    original = default_profile()
    monkeypatch.setattr(
        interview,
        "_ask",
        lambda prompt, default=None: (
            category
            if prompt.startswith("Edit category")
            else default
            if default is not None
            else "done"
        ),
    )
    monkeypatch.setattr(interview, "_confirm", lambda prompt, default=True: default)
    result = interview._targeted_edit(original)
    assert result.model_dump(exclude={"updated_at"}) == original.model_dump(exclude={"updated_at"})


def test_variable_format_upper_boundary_is_valid(monkeypatch):
    def integer(prompt, minimum, maximum, default):
        assert minimum <= default <= maximum
        return maximum if prompt == "Minimum minutes" else default

    monkeypatch.setattr(interview, "_int", integer)
    monkeypatch.setattr(interview, "_confirm", lambda *args: True)
    result = ShowFormat.model_validate(interview._collect_format(default_profile()))
    assert result.minimum_minutes < result.maximum_minutes


def test_numeric_prompt_retries_invalid_input(monkeypatch):
    answers = iter(["bad", "100", "10"])
    monkeypatch.setattr(interview, "_ask", lambda *args: next(answers))
    assert interview._int("Minutes", 5, 90, 20) == 10


def test_interest_entry_retries_empty_and_duplicate_names(monkeypatch):
    responses = iter(
        [
            "",
            "AI",
            "5",
            "deep",
            "tools",
            "done",
            "include tools",
            "avoid hype",
            "ai",
            "Apple",
            "3",
            "normal",
            "",
            "",
            "done",
        ]
    )
    monkeypatch.setattr(interview, "_ask", lambda *args: next(responses))
    interests = interview._collect_interests(default_profile())
    assert [item["name"] for item in interests] == ["AI", "Apple"]
    assert interests[0]["subtopics"] == ["tools"]


def test_exclusions_and_fixed_format(monkeypatch):
    values = iter(["", "celebrity", "done"])
    monkeypatch.setattr(interview, "_ask", lambda *args: next(values))
    assert interview._collect_exclusions([]) == ["celebrity"]
    monkeypatch.setattr(interview, "_confirm", lambda *args: False)
    monkeypatch.setattr(interview, "_int", lambda prompt, minimum, maximum, default: default)
    result = ShowFormat.model_validate(interview._collect_format(default_profile()))
    assert result.minimum_minutes == result.maximum_minutes == result.target_minutes


def test_prompt_wrappers_use_defaults_and_retry_choices(monkeypatch):
    monkeypatch.setattr(interview.typer, "prompt", lambda *args, **kwargs: "  ")
    assert interview._ask("Home", "Buffalo") == "Buffalo"
    monkeypatch.setattr(interview.typer, "confirm", lambda *args, **kwargs: kwargs["default"])
    assert not interview._confirm("News", False)
    answers = iter(["not a choice", "changes only"])
    monkeypatch.setattr(interview, "_ask", lambda *args: next(answers))
    assert interview._choice("Policy", {"changes_only"}, "changes_only") == "changes_only"
