from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import trafilatura

from morning_radio.models import CandidateStory, ExtractionResult
from morning_radio.newsroom.fetch import UnsafeUrlError, assert_safe_public_url
from morning_radio.settings import AppSettings


class ExtractionStageError(RuntimeError):
    pass


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
    with httpx.Client(
        timeout=settings.news.request_timeout_seconds, follow_redirects=True, headers=headers
    ) as client:
        for candidate in ranked:
            result = _extract_one(candidate, settings, client)
            results.append(result)
            (output_dir / f"{candidate.candidate_id}.json").write_text(
                json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    usable = [item for item in results if item.extraction_status == "usable"]
    if len(usable) < settings.news.minimum_usable_articles:
        raise ExtractionStageError(
            f"Only {len(usable)} usable articles; need {settings.news.minimum_usable_articles}."
        )
    return results


def _extract_one(
    candidate: CandidateStory, settings: AppSettings, client: httpx.Client
) -> ExtractionResult:
    try:
        assert_safe_public_url(candidate.url)
        with client.stream("GET", candidate.url) as response:
            final_url = str(response.url)
            assert_safe_public_url(final_url)
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
            chunks = bytearray()
            for chunk in response.iter_bytes():
                chunks.extend(chunk)
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
    except (httpx.HTTPError, UnsafeUrlError) as exc:
        return ExtractionResult(
            candidate_id=candidate.candidate_id,
            url=candidate.url,
            extraction_status="fetch_failed",
            failure_reason=str(exc),
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
