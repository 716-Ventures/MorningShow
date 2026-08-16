from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx
import trafilatura

from morning_radio.models import CandidateStory, ExtractionResult
from morning_radio.newsroom.fetch import UnsafeUrlError, assert_safe_public_url
from morning_radio.settings import AppSettings


class ExtractionStageError(RuntimeError):
    pass


MAX_REDIRECTS = 5


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_articles(
    candidates: list[CandidateStory], settings: AppSettings, run_dir: Path
) -> list[ExtractionResult]:
    ranked = sorted(
        candidates,
        key=lambda item: (len(item.category_hints), item.published_at is not None),
        reverse=True,
    )[: settings.news.max_articles_to_extract]
    results: list[ExtractionResult] = []
    output_dir = run_dir / "extracted"
    output_dir.mkdir(exist_ok=True)
    headers = {"User-Agent": "PersonalMorningRadioPOC/0.1 (+local operator)"}
    results = asyncio.run(extract_ranked_articles(ranked, settings, headers))
    for result in results:
        (output_dir / f"{result.candidate_id}.json").write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    usable = [item for item in results if item.extraction_status == "usable"]
    if len(usable) < settings.news.minimum_usable_articles:
        raise ExtractionStageError(
            f"Only {len(usable)} usable articles; need {settings.news.minimum_usable_articles}."
        )
    return results


async def extract_ranked_articles(
    ranked: list[CandidateStory], settings: AppSettings, headers: dict[str, str]
) -> list[ExtractionResult]:
    semaphore = asyncio.Semaphore(settings.news.concurrency)
    async with httpx.AsyncClient(
        timeout=settings.news.request_timeout_seconds,
        follow_redirects=False,
        headers=headers,
    ) as client:
        tasks = [_extract_one_async(candidate, settings, client, semaphore) for candidate in ranked]
        return list(await asyncio.gather(*tasks))


async def _extract_one_async(
    candidate: CandidateStory,
    settings: AppSettings,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> ExtractionResult:
    async with semaphore:
        try:
            response = await _get_checked_response_async(candidate.url, client)
            return _extraction_from_response(candidate, settings, response)
        except (httpx.HTTPError, UnsafeUrlError) as exc:
            return ExtractionResult(
                candidate_id=candidate.candidate_id,
                url=candidate.url,
                extraction_status="fetch_failed",
                failure_reason=str(exc),
            )


def _extract_one(
    candidate: CandidateStory, settings: AppSettings, client: httpx.Client
) -> ExtractionResult:
    try:
        response = _get_checked_response(candidate.url, client)
        return _extraction_from_response(candidate, settings, response)
    except (httpx.HTTPError, UnsafeUrlError) as exc:
        return ExtractionResult(
            candidate_id=candidate.candidate_id,
            url=candidate.url,
            extraction_status="fetch_failed",
            failure_reason=str(exc),
        )


def _extraction_from_response(
    candidate: CandidateStory, settings: AppSettings, response: httpx.Response
) -> ExtractionResult:
    final_url = str(response.url)
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type.lower():
        return ExtractionResult(
            candidate_id=candidate.candidate_id,
            url=candidate.url,
            final_url=final_url,
            http_status=response.status_code,
            extraction_status="unsupported_content",
            failure_reason=f"content-type={content_type}",
        )
    chunks = response.content
    if len(chunks) > settings.news.max_html_bytes:
        return ExtractionResult(
            candidate_id=candidate.candidate_id,
            url=candidate.url,
            final_url=final_url,
            http_status=response.status_code,
            extraction_status="fetch_failed",
            failure_reason="HTML body exceeded configured size limit",
        )
    if response.status_code >= 400:
        return ExtractionResult(
            candidate_id=candidate.candidate_id,
            url=candidate.url,
            final_url=final_url,
            http_status=response.status_code,
            extraction_status="fetch_failed",
            failure_reason=f"HTTP {response.status_code}",
        )
    extracted = trafilatura.extract(bytes(chunks), include_comments=False, include_tables=False)
    if not extracted:
        return ExtractionResult(
            candidate_id=candidate.candidate_id,
            url=candidate.url,
            final_url=final_url,
            http_status=response.status_code,
            extraction_status="parse_failed",
            failure_reason="Trafilatura returned no article text",
        )
    text = normalize_text(extracted)
    word_count = len(text.split())
    if word_count < settings.news.minimum_article_words:
        status = "too_short"
        reason = f"{word_count} words"
    else:
        status = "usable"
        reason = None
    return ExtractionResult(
        candidate_id=candidate.candidate_id,
        url=candidate.url,
        final_url=final_url,
        http_status=response.status_code,
        title=candidate.title,
        published_at=candidate.published_at,
        text=text,
        word_count=word_count,
        extraction_status=status,
        failure_reason=reason,
    )


def _get_checked_response(url: str, client: httpx.Client) -> httpx.Response:
    current_url = url
    visited: set[str] = set()
    for _ in range(MAX_REDIRECTS + 1):
        assert_safe_public_url(current_url)
        if current_url in visited:
            raise UnsafeUrlError(f"Redirect loop detected: {current_url}")
        visited.add(current_url)
        response = client.get(current_url)
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("location")
        response.close()
        if not location:
            raise UnsafeUrlError(f"Redirect response missing Location header: {current_url}")
        current_url = urljoin(current_url, location)
        assert_safe_public_url(current_url)
    raise UnsafeUrlError(f"Redirect limit exceeded after {MAX_REDIRECTS} redirects")


async def _get_checked_response_async(url: str, client: httpx.AsyncClient) -> httpx.Response:
    current_url = url
    visited: set[str] = set()
    for _ in range(MAX_REDIRECTS + 1):
        assert_safe_public_url(current_url)
        if current_url in visited:
            raise UnsafeUrlError(f"Redirect loop detected: {current_url}")
        visited.add(current_url)
        response = await client.get(current_url)
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("location")
        await response.aclose()
        if not location:
            raise UnsafeUrlError(f"Redirect response missing Location header: {current_url}")
        current_url = urljoin(current_url, location)
        assert_safe_public_url(current_url)
    raise UnsafeUrlError(f"Redirect limit exceeded after {MAX_REDIRECTS} redirects")
