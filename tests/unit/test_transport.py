from __future__ import annotations

import asyncio
import socket

import httpcore
import httpx
import pytest

from morning_radio.newsroom.fetch import (
    UnsafeUrlError,
    resolve_public_addresses,
    validate_public_url,
)
from morning_radio.newsroom.transport import PublicAsyncTransport, PublicNetworkBackend, http_errors


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
    ],
)
def test_nonpublic_addresses_never_connect(address):
    with pytest.raises(UnsafeUrlError):
        resolve_public_addresses(address)


def test_mixed_dns_answers_are_rejected(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
            for ip in ("93.184.216.34", "127.0.0.1")
        ],
    )
    with pytest.raises(UnsafeUrlError):
        resolve_public_addresses("news.test")


def test_connection_pins_ip_and_preserves_tls_hostname_and_host_header(monkeypatch):
    resolved = []
    connected = []
    tls_names = []
    writes = []

    def dns(host, *args, **kwargs):
        resolved.append(host)
        # A second hostname lookup would be a rebinding attack.
        ip = "93.184.216.34" if len(resolved) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    class Stream(httpcore.AsyncMockStream):
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            assert ssl_context.check_hostname
            tls_names.append(server_hostname)
            return self

        async def write(self, buffer, timeout=None):
            writes.append(buffer)

    class Backend(httpcore.AsyncNetworkBackend):
        async def connect_tcp(
            self, host, port, timeout=None, local_address=None, socket_options=None
        ):
            connected.append(host)
            return Stream([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"])

    monkeypatch.setattr(socket, "getaddrinfo", dns)

    async def run():
        async with httpx.AsyncClient(
            transport=PublicAsyncTransport(PublicNetworkBackend(Backend())), trust_env=False
        ) as client:
            assert (await client.get("https://news.test/article")).text == "ok"

    asyncio.run(run())
    assert resolved == ["news.test"]
    assert connected == ["93.184.216.34"]
    assert tls_names == ["news.test"]
    assert b"Host: news.test\r\n" in b"".join(writes)


def test_backend_timeout_is_bounded(monkeypatch):
    class Backend(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, *args, **kwargs):
            await asyncio.sleep(10)

    async def run():
        with pytest.raises(httpcore.ConnectTimeout):
            await PublicNetworkBackend(Backend()).connect_tcp("93.184.216.34", 443, timeout=0.01)

    asyncio.run(run())


@pytest.mark.parametrize(
    "url", ["http://[invalid", "https:///no-host", "https://example.com:invalid"]
)
def test_malformed_urls_fail_before_dns(url):
    with pytest.raises(UnsafeUrlError):
        validate_public_url(url)


@pytest.mark.parametrize("fails", [False, True])
def test_dns_failure_and_empty_result_are_actionable(monkeypatch, fails):
    def resolve(*args, **kwargs):
        if fails:
            raise socket.gaierror("not found")
        return []

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(UnsafeUrlError, match="host"):
        resolve_public_addresses("missing.test")


@pytest.mark.parametrize("exhausted", [False, True])
def test_backend_tries_validated_addresses_in_order(monkeypatch, exhausted):
    from morning_radio.newsroom import transport

    addresses = ["93.184.216.34", "1.1.1.1"]
    monkeypatch.setattr(transport, "resolve_public_addresses", lambda host: addresses)
    calls = []
    stream = httpcore.AsyncMockStream([])

    class Backend(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, host, *args, **kwargs):
            calls.append(host)
            if host == addresses[0] or exhausted:
                raise httpcore.ConnectError("unreachable")
            return stream

    async def run():
        backend = PublicNetworkBackend(Backend())
        if exhausted:
            with pytest.raises(httpcore.ConnectError, match="unreachable"):
                await backend.connect_tcp("news.test", 443)
        else:
            assert await backend.connect_tcp("news.test", 443) is stream

    asyncio.run(run())
    assert calls == addresses


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpcore.ReadTimeout("slow"), httpx.TimeoutException),
        (httpcore.ConnectError("offline"), httpx.TransportError),
        (httpcore.RemoteProtocolError("bad"), httpx.TransportError),
    ],
)
def test_transport_errors_are_mapped_with_cause(error, expected):
    with pytest.raises(expected) as caught, http_errors():
        raise error
    assert caught.value.__cause__ is error
