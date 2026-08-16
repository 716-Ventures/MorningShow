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
        with httpx.Client(follow_redirects=False) as client, pytest.raises(
            UnsafeUrlError, match="Redirect loop"
        ):
            _get_checked_response("http://public.test/start", client)


def test_redirect_limit_is_rejected() -> None:
    with respx.mock(assert_all_called=False) as router:
        for index in range(7):
            router.get(f"http://public.test/{index}").mock(
                Response(302, headers={"location": f"/{index + 1}"})
            )
        with httpx.Client(follow_redirects=False) as client, pytest.raises(
            UnsafeUrlError, match="Redirect limit"
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
