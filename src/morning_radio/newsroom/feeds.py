from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from dateutil import parser as date_parser

from morning_radio.artifacts.io import atomic_write_jsonl
from morning_radio.logging import log_line
from morning_radio.models import CandidateStory, FeedConfig
from morning_radio.settings import AppSettings, FeedSettings

TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


class DiscoveryError(RuntimeError):
    pass


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            urlencode(query, doseq=True),
            "",
        )
    )


def candidate_id(url: str) -> str:
    return hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()[:24]


def parse_feed_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, str):
            parsed = date_parser.parse(value)
        else:
            parsed = datetime(*value[:6], tzinfo=UTC)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone()


def discover_candidates(
    feed_settings: FeedSettings,
    app_settings: AppSettings,
    run_dir: Path,
    requested_at: datetime | None = None,
) -> list[CandidateStory]:
    now = requested_at or datetime.now().astimezone()
    newest_allowed = now - timedelta(hours=app_settings.news.candidate_max_age_hours)
    per_feed_candidates: dict[str, list[CandidateStory]] = {}
    feed_order = sorted(
        [item for item in feed_settings.feeds if item.enabled],
        key=lambda item: (-item.priority, item.id),
    )
    headers = {"User-Agent": "PersonalMorningRadioPOC/0.1 (+local operator)"}
    responses = asyncio.run(
        fetch_enabled_feeds(
            feed_order,
            app_settings.news.request_timeout_seconds,
            app_settings.news.concurrency,
            headers,
        )
    )
    failures = 0
    for feed, content, error in responses:
        if error is not None or content is None:
            failures += 1
            log_line(run_dir, f"stage=discover feed={feed.id} status=failed error={error}")
            continue
        try:
            parsed = feedparser.parse(content)
            if getattr(parsed, "bozo", False):
                raise ValueError(str(getattr(parsed, "bozo_exception", "malformed feed")))
        except ValueError as exc:
            failures += 1
            log_line(run_dir, f"stage=discover feed={feed.id} status=failed error={exc}")
            continue
        feed_candidates = parse_feed_candidates(feed, parsed, now, newest_allowed)
        per_feed_candidates[feed.id] = feed_candidates
        log_line(run_dir, f"stage=discover feed={feed.id} status=ok candidates={len(feed_candidates)}")

    candidates = fair_cap_candidates(feed_order, per_feed_candidates, app_settings.news.max_candidates)
    if not candidates:
        raise DiscoveryError(f"Zero candidates retrieved from enabled feeds ({failures} feed failures).")
    atomic_write_jsonl(
        run_dir / "candidates.jsonl",
        [candidate.model_dump(mode="json") for candidate in candidates],
    )
    return candidates


async def fetch_enabled_feeds(
    feeds: list[FeedConfig],
    timeout_seconds: int,
    concurrency: int,
    headers: dict[str, str],
) -> list[tuple[FeedConfig, bytes | None, str | None]]:
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        headers=headers,
        follow_redirects=True,
    ) as client:
        tasks = [fetch_feed(feed, client, semaphore) for feed in feeds]
        return await asyncio.gather(*tasks)


async def fetch_feed(
    feed: FeedConfig, client: httpx.AsyncClient, semaphore: asyncio.Semaphore
) -> tuple[FeedConfig, bytes | None, str | None]:
    async with semaphore:
        try:
            response = await client.get(str(feed.url))
            response.raise_for_status()
            return feed, response.content, None
        except (httpx.HTTPError, ValueError) as exc:
            return feed, None, str(exc)


def parse_feed_candidates(
    feed: FeedConfig, parsed, now: datetime, newest_allowed: datetime
) -> list[CandidateStory]:
    candidates: list[CandidateStory] = []
    for entry in parsed.entries:
        link = getattr(entry, "link", None)
        title = (getattr(entry, "title", "") or "").strip()
        if not link or not title:
            continue
        canonical = canonicalize_url(link)
        published = parse_feed_datetime(
            getattr(entry, "published", None) or getattr(entry, "updated", None)
        )
        if published and published < newest_allowed:
            continue
        candidates.append(
            CandidateStory(
                candidate_id=candidate_id(canonical),
                feed_id=feed.id,
                title=title,
                url=canonical,
                published_at=published,
                retrieved_at=now,
                publisher=getattr(parsed.feed, "title", None) or feed.name,
                feed_summary=getattr(entry, "summary", None),
                category_hints=feed.category_hints,
                geography_hints=feed.geography_hints,
            )
        )
    return sorted(
        candidates,
        key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )


def fair_cap_candidates(
    feeds: list[FeedConfig],
    per_feed_candidates: dict[str, list[CandidateStory]],
    max_candidates: int,
) -> list[CandidateStory]:
    selected: list[CandidateStory] = []
    seen_urls: set[str] = set()
    cursors = {feed.id: 0 for feed in feeds}
    while len(selected) < max_candidates:
        added = False
        for feed in feeds:
            feed_candidates = per_feed_candidates.get(feed.id, [])
            while cursors[feed.id] < len(feed_candidates):
                candidate = feed_candidates[cursors[feed.id]]
                cursors[feed.id] += 1
                canonical = canonicalize_url(candidate.url)
                if canonical in seen_urls:
                    continue
                seen_urls.add(canonical)
                selected.append(candidate)
                added = True
                break
            if len(selected) >= max_candidates:
                break
        if not added:
            break
    return selected
