from __future__ import annotations

import asyncio
import socket

import httpcore
import httpx
import pytest

from morning_radio.newsroom.fetch import UnsafeUrlError, resolve_public_addresses
from morning_radio.newsroom.transport import PublicAsyncTransport, PublicNetworkBackend


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
