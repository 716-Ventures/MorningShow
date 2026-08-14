from __future__ import annotations

from morning_radio.newsroom.feeds import candidate_id, canonicalize_url


def test_canonicalize_removes_tracking_and_fragment() -> None:
    url = "HTTPS://Example.com/story?utm_source=x&id=12&fbclid=abc#section"
    assert canonicalize_url(url) == "https://example.com/story?id=12"


def test_candidate_id_is_stable_for_tracking_variants() -> None:
    first = candidate_id("https://example.com/story?id=12&utm_campaign=x")
    second = candidate_id("https://example.com/story?id=12")
    assert first == second
