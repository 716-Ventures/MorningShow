from __future__ import annotations

import asyncio
import socket
from datetime import datetime

import httpx
import pytest
import respx
from httpx import Response
from pydantic import HttpUrl

from morning_radio.models import CandidateStory, ExtractionResult
from morning_radio.newsroom import extract as extract_module
from morning_radio.newsroom.extract import _get_checked_response
from morning_radio.newsroom.fetch import UnsafeUrlError
from morning_radio.settings import (
    AppSettings,
    LLMSettings,
    NewsSettings,
    SelectionSettings,
    VerificationSettings,
)


def _fake_addrinfo(address: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]


@pytest.fixture(autouse=True)
def public_test_dns(monkeypatch) -> None:
    def fake_getaddrinfo(host, *args, **kwargs):
        if host in {"public.test", "safe.test"}:
            return _fake_addrinfo("93.184.216.34")
        if host == "127.0.0.1":
            return _fake_addrinfo("127.0.0.1")
        if host == "10.0.0.1":
            return _fake_addrinfo("10.0.0.1")
        if host == "169.254.169.254":
            return _fake_addrinfo("169.254.169.254")
        return _fake_addrinfo("93.184.216.34")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_redirect_to_loopback_is_rejected_before_second_request() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get("http://public.test/start").mock(
            Response(302, headers={"location": "http://127.0.0.1/private"})
        )
        private_route = router.get("http://127.0.0.1/private").mock(
            Response(200, headers={"content-type": "text/html"}, text="<html>private</html>")
        )
        with httpx.Client(follow_redirects=False) as client, pytest.raises(UnsafeUrlError):
            _get_checked_response("http://public.test/start", client)
    assert private_route.call_count == 0


def test_redirect_to_private_ipv4_is_rejected_before_second_request() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get("http://public.test/start").mock(
            Response(302, headers={"location": "http://10.0.0.1/private"})
        )
        private_route = router.get("http://10.0.0.1/private").mock(Response(200))
        with httpx.Client(follow_redirects=False) as client, pytest.raises(UnsafeUrlError):
            _get_checked_response("http://public.test/start", client)
    assert private_route.call_count == 0


def test_redirect_to_metadata_destination_is_rejected_before_second_request() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get("http://public.test/start").mock(
            Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
        )
        metadata_route = router.get("http://169.254.169.254/latest/meta-data").mock(Response(200))
        with httpx.Client(follow_redirects=False) as client, pytest.raises(UnsafeUrlError):
            _get_checked_response("http://public.test/start", client)
    assert metadata_route.call_count == 0


def test_safe_relative_redirect_succeeds() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("http://public.test/start").mock(Response(302, headers={"location": "/final"}))
        router.get("http://public.test/final").mock(
            Response(200, headers={"content-type": "text/html"}, text="<html>ok</html>")
        )
        with httpx.Client(follow_redirects=False) as client:
            response = _get_checked_response("http://public.test/start", client)
    assert str(response.url) == "http://public.test/final"
    assert response.status_code == 200


def test_redirect_loop_is_rejected() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get("http://public.test/start").mock(Response(302, headers={"location": "/start"}))
        with (
            httpx.Client(follow_redirects=False) as client,
            pytest.raises(UnsafeUrlError, match="Redirect loop"),
        ):
            _get_checked_response("http://public.test/start", client)


def test_redirect_limit_is_rejected() -> None:
    with respx.mock(assert_all_called=False) as router:
        for index in range(7):
            router.get(f"http://public.test/{index}").mock(
                Response(302, headers={"location": f"/{index + 1}"})
            )
        with (
            httpx.Client(follow_redirects=False) as client,
            pytest.raises(UnsafeUrlError, match="Redirect limit"),
        ):
            _get_checked_response("http://public.test/0", client)


def app_settings(*, concurrency: int = 2) -> AppSettings:
    return AppSettings(
        llm=LLMSettings(base_url=HttpUrl("http://ollama.test"), model="test", timeout_seconds=5),
        news=NewsSettings(
            request_timeout_seconds=5,
            max_html_bytes=10240,
            minimum_article_words=1,
            minimum_usable_articles=1,
            candidate_max_age_hours=72,
            max_candidates=10,
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


def candidate(candidate_id: str) -> CandidateStory:
    return CandidateStory(
        candidate_id=candidate_id,
        feed_id="feed",
        title=candidate_id,
        url=f"http://public.test/{candidate_id}",
        retrieved_at=datetime.now().astimezone(),
    )


def test_extract_ranked_articles_respects_concurrency(monkeypatch) -> None:
    active = 0
    max_active = 0

    async def fake_extract(candidate_story, settings, client, semaphore):
        nonlocal active, max_active
        async with semaphore:
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return ExtractionResult(
                candidate_id=candidate_story.candidate_id,
                url=candidate_story.url,
                extraction_status="usable",
                text="ok",
                word_count=1,
            )

    monkeypatch.setattr(extract_module, "_extract_one_async", fake_extract)
    asyncio.run(
        extract_module.extract_ranked_articles(
            [candidate("a"), candidate("b"), candidate("c")],
            app_settings(concurrency=2),
            {},
        )
    )
    assert max_active == 2


def test_extract_ranked_articles_preserves_ranked_order(monkeypatch) -> None:
    async def fake_extract(candidate_story, settings, client, semaphore):
        async with semaphore:
            if candidate_story.candidate_id == "a":
                await asyncio.sleep(0.01)
            return ExtractionResult(
                candidate_id=candidate_story.candidate_id,
                url=candidate_story.url,
                extraction_status="usable",
                text="ok",
                word_count=1,
            )

    monkeypatch.setattr(extract_module, "_extract_one_async", fake_extract)
    results = asyncio.run(
        extract_module.extract_ranked_articles(
            [candidate("a"), candidate("b")],
            app_settings(concurrency=2),
            {},
        )
    )
    assert [item.candidate_id for item in results] == ["a", "b"]


def test_async_download_stops_at_limit_and_closes_stream() -> None:
    class Body(httpx.AsyncByteStream):
        closed = False
        consumed = 0

        async def __aiter__(self):
            for _ in range(10):
                self.consumed += 1
                yield b"x" * 1024

        async def aclose(self):
            self.closed = True

    body = Body()

    async def run():
        transport = httpx.MockTransport(lambda request: Response(200, stream=body))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(UnsafeUrlError, match="size limit"):
                await extract_module._get_checked_response_async(
                    "http://public.test/large", client, max_bytes=2048
                )

    asyncio.run(run())
    assert body.consumed == 3
    assert body.closed


@pytest.mark.parametrize(
    "status,kind,text,expected",
    [
        (200, "application/pdf", "body", "unsupported_content"),
        (404, "text/html", "body", "fetch_failed"),
        (200, "text/html", None, "parse_failed"),
        (200, "text/html", "one", "too_short"),
        (200, "text/html", "two words", "usable"),
    ],
)
def test_article_result_states(monkeypatch, status, kind, text, expected):
    settings = app_settings()
    settings.news.minimum_article_words = 2
    monkeypatch.setattr(extract_module.trafilatura, "extract", lambda *args, **kwargs: text)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: Response(status, headers={"content-type": kind}, content=b"html")
        )
    ) as client:
        result = extract_module._extract_one(candidate("state"), settings, client)
    assert result.extraction_status == expected


def test_async_article_failure_isolated():
    def fail(request):
        raise httpx.ReadTimeout("slow source")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            return await extract_module._extract_one_async(
                candidate("timeout"), app_settings(), client, asyncio.Semaphore(1)
            )

    result = asyncio.run(run())
    assert result.extraction_status == "fetch_failed"
    assert result.failure_reason is not None
    assert "slow source" in result.failure_reason


def test_extraction_stage_persists_failures_before_raising(monkeypatch, tmp_path):
    async def failed(*args):
        return [
            ExtractionResult(
                candidate_id="failed", url="https://public.test", extraction_status="parse_failed"
            )
        ]

    monkeypatch.setattr(extract_module, "extract_ranked_articles", failed)
    with pytest.raises(extract_module.ExtractionStageError, match="Only 0"):
        extract_module.extract_articles([candidate("failed")], app_settings(), tmp_path)
    assert (tmp_path / "extracted/failed.json").exists()


def test_parser_error_does_not_abort_other_articles(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("bad document")

    monkeypatch.setattr(extract_module.trafilatura, "extract", fail)
    response = Response(
        200,
        content=b"html",
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", "https://public.test"),
    )
    result = extract_module._extraction_from_response(candidate("broken"), app_settings(), response)
    assert result.extraction_status == "parse_failed"
