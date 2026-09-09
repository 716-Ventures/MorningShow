from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime
from pathlib import Path

import feedparser
import pytest
import respx
from httpx import Response
from pydantic import HttpUrl

from morning_radio.models import CandidateStory, FeedConfig
from morning_radio.newsroom import feeds as feed_module
from morning_radio.newsroom.feeds import (
    DiscoveryError,
    candidate_id,
    canonicalize_url,
    discover_candidates,
    fair_cap_candidates,
    fetch_enabled_feeds,
    parse_feed_candidates,
)
from morning_radio.settings import (
    AppSettings,
    FeedSettings,
    LLMSettings,
    NewsSettings,
    SelectionSettings,
    VerificationSettings,
)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )


def test_canonicalize_removes_tracking_and_fragment() -> None:
    url = "HTTPS://Example.com/story?utm_source=x&id=12&fbclid=abc#section"
    assert canonicalize_url(url) == "https://example.com/story?id=12"


def test_candidate_id_is_stable_for_tracking_variants() -> None:
    first = candidate_id("https://example.com/story?id=12&utm_campaign=x")
    second = candidate_id("https://example.com/story?id=12")
    assert first == second


def feed(
    feed_id: str,
    *,
    priority: int = 3,
    interest_hints: list[str] | None = None,
) -> FeedConfig:
    return FeedConfig(
        id=feed_id,
        name=feed_id,
        url=HttpUrl(f"https://{feed_id}.example.com/rss"),
        enabled=True,
        priority=priority,
        interest_hints=interest_hints or [],
    )


def app_settings(*, max_candidates: int = 20, concurrency: int = 2) -> AppSettings:
    return AppSettings(
        llm=LLMSettings(base_url=HttpUrl("http://ollama.test"), model="test", timeout_seconds=5),
        news=NewsSettings(
            request_timeout_seconds=5,
            max_html_bytes=10240,
            minimum_article_words=1,
            minimum_usable_articles=1,
            candidate_max_age_hours=72,
            max_candidates=max_candidates,
            max_articles_to_extract=10,
            concurrency=concurrency,
        ),
        selection=SelectionSettings(
            major_news_importance_threshold=80,
            dossier_source_preference=2,
            maximum_selected_stories=3,
        ),
        verification=VerificationSettings(maximum_correction_cycles=2),
    )


def rss(items: list[tuple[str, str]]) -> str:
    entries = "\n".join(
        f"<item><title>{title}</title><link>https://news.example.com/{slug}</link>"
        f"<pubDate>Sat, 15 Aug 2026 12:00:00 GMT</pubDate></item>"
        for slug, title in items
    )
    return f"<?xml version='1.0'?><rss version='2.0'><channel><title>Feed</title>{entries}</channel></rss>"


def test_feed_interest_hints_are_preserved_on_candidates() -> None:
    configured_feed = feed("bills", interest_hints=["Bills Football"])
    parsed = feedparser.parse(rss([("story", "Roster move announced")]).encode())
    candidates = parse_feed_candidates(
        configured_feed,
        parsed,
        datetime(2026, 8, 15, tzinfo=UTC),
        datetime(2026, 8, 14, tzinfo=UTC),
    )

    assert candidates[0].interest_hints == ["Bills Football"]


def story(feed_id: str, slug: str) -> CandidateStory:
    return CandidateStory(
        candidate_id=slug,
        feed_id=feed_id,
        title=slug,
        url=f"https://news.example.com/{slug}",
        retrieved_at=datetime(2026, 8, 15, tzinfo=UTC),
    )


def test_fetch_enabled_feeds_respects_concurrency(monkeypatch) -> None:
    active = 0
    max_active = 0

    async def fake_fetch(feed_config, client, semaphore):
        nonlocal active, max_active
        async with semaphore:
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return feed_config, b"<rss />", None

    monkeypatch.setattr(feed_module, "fetch_feed", fake_fetch)
    asyncio.run(fetch_enabled_feeds([feed("a"), feed("b"), feed("c")], 5, 2, {}))
    assert max_active == 2


def test_fair_cap_interleaves_feeds_by_priority() -> None:
    high = feed("high", priority=5)
    low = feed("low", priority=1)
    selected = fair_cap_candidates(
        [high, low],
        {
            "high": [story("high", "high-1"), story("high", "high-2")],
            "low": [story("low", "low-1"), story("low", "low-2")],
        },
        max_candidates=3,
    )
    assert [(item.feed_id, item.title) for item in selected] == [
        ("high", "high-1"),
        ("low", "low-1"),
        ("high", "high-2"),
    ]


def test_discover_candidates_fetches_all_feeds_before_capping(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://first.example.com/rss").mock(
            Response(200, text=rss([("a1", "A one"), ("a2", "A two")]))
        )
        router.get("https://second.example.com/rss").mock(
            Response(200, text=rss([("b1", "B one"), ("b2", "B two")]))
        )
        candidates = discover_candidates(
            FeedSettings(feeds=[feed("first", priority=5), feed("second", priority=1)]),
            app_settings(max_candidates=3),
            tmp_path,
            requested_at=datetime(2026, 8, 15, tzinfo=UTC),
        )
    assert [item.title for item in candidates] == ["A one", "B one", "A two"]


def test_discover_candidates_treats_malformed_feed_as_failure(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://bad.example.com/rss").mock(
            Response(200, text="<html>not a feed</html>")
        )
        with pytest.raises(DiscoveryError):
            discover_candidates(
                FeedSettings(feeds=[feed("bad")]),
                app_settings(max_candidates=3),
                tmp_path,
                requested_at=datetime(2026, 8, 15, tzinfo=UTC),
            )
