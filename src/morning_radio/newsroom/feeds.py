from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from dateutil import parser as date_parser

from morning_radio.logging import log_line
from morning_radio.models import CandidateStory
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
    seen: set[str] = set()
    candidates: list[CandidateStory] = []
    failures = 0
    headers = {"User-Agent": "PersonalMorningRadioPOC/0.1 (+local operator)"}
    with httpx.Client(timeout=app_settings.news.request_timeout_seconds, headers=headers, follow_redirects=True) as client:
        for feed in [item for item in feed_settings.feeds if item.enabled]:
            try:
                response = client.get(str(feed.url))
                response.raise_for_status()
                parsed = feedparser.parse(response.content)
            except (httpx.HTTPError, ValueError) as exc:
                failures += 1
                log_line(run_dir, f"stage=discover feed={feed.id} status=failed error={exc}")
                continue
            count = 0
            for entry in parsed.entries:
                link = getattr(entry, "link", None)
                title = (getattr(entry, "title", "") or "").strip()
                if not link or not title:
                    continue
                canonical = canonicalize_url(link)
                if canonical in seen:
                    continue
                published = parse_feed_datetime(
                    getattr(entry, "published", None) or getattr(entry, "updated", None)
                )
                if published and published < newest_allowed:
                    continue
                seen.add(canonical)
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
                count += 1
                if len(candidates) >= app_settings.news.max_candidates:
                    break
            log_line(run_dir, f"stage=discover feed={feed.id} status=ok candidates={count}")
            if len(candidates) >= app_settings.news.max_candidates:
                break
    if not candidates:
        raise DiscoveryError(f"Zero candidates retrieved from enabled feeds ({failures} feed failures).")
    output = run_dir / "candidates.jsonl"
    with output.open("w", encoding="utf-8") as handle:
        for candidate in candidates:
            handle.write(json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False) + "\n")
    return candidates
