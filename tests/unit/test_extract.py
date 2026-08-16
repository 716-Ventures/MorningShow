from __future__ import annotations

import socket

import httpx
import pytest
import respx
from httpx import Response

from morning_radio.newsroom.extract import _get_checked_response
from morning_radio.newsroom.fetch import UnsafeUrlError


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
