"""Public-only HTTP transport with connection-time DNS pinning.

The pool retains the original origin for Host, TLS SNI/certificate checks and
connection reuse. Only the network backend sees a validated numeric address.
Environment proxies are deliberately not part of this transport's trust model.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Generator, Iterable, Mapping
from contextlib import contextmanager
from typing import Protocol, cast

import httpcore
import httpx

from morning_radio.newsroom.fetch import resolve_public_addresses, validate_public_url

type SocketOption = (
    tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]
)


class PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        selected = backend or httpcore.AnyIOBackend()
        if not isinstance(selected, httpcore.AsyncNetworkBackend):
            raise RuntimeError("HTTPCore asyncio backend is unavailable")
        self.backend = selected

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            async with asyncio.timeout(timeout):
                addresses = await asyncio.to_thread(resolve_public_addresses, host)
                for index, address in enumerate(addresses):
                    try:
                        return await self.backend.connect_tcp(
                            address, port, timeout, local_address, socket_options
                        )
                    except (httpcore.ConnectError, httpcore.ConnectTimeout):
                        if index == len(addresses) - 1:
                            raise
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout("Public connection/DNS timeout") from exc
        raise httpcore.ConnectError("No public destination")


@contextmanager
def http_errors() -> Generator[None]:
    try:
        yield
    except httpcore.TimeoutException as exc:
        raise httpx.TimeoutException(str(exc)) from exc
    except (
        httpcore.NetworkError,
        httpcore.ProtocolError,
        httpcore.ProxyError,
        httpcore.UnsupportedProtocol,
    ) as exc:
        raise httpx.TransportError(str(exc)) from exc


class PublicResponseStream(httpx.AsyncByteStream):
    def __init__(self, response: httpcore.Response) -> None:
        self.response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        with http_errors():
            async for chunk in self.response.aiter_stream():
                yield chunk

    async def aclose(self) -> None:
        with http_errors():
            await self.response.aclose()


class PublicAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, backend: PublicNetworkBackend | None = None) -> None:
        self.pool = httpcore.AsyncConnectionPool(
            network_backend=backend or PublicNetworkBackend(), max_connections=32
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        validate_public_url(str(request.url))
        if not isinstance(request.stream, httpx.AsyncByteStream):
            raise httpx.RequestError("Expected asynchronous request stream", request=request)
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        with http_errors():
            response = await self.pool.handle_async_request(core_request)
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=PublicResponseStream(response),
            extensions=dict(cast(ResponseExtensions, response).extensions),
        )

    async def aclose(self) -> None:
        await self.pool.aclose()


class ResponseExtensions(Protocol):
    """Narrow HTTPCore's unparameterized empty-dict extension annotation."""

    @property
    def extensions(self) -> Mapping[str, object]: ...
