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
