from __future__ import annotations

import asyncio
import gzip
import zlib

import httpx
import pytest

from morning_radio.newsroom.download import BoundedBody, get_checked_response_async
from morning_radio.newsroom.fetch import UnsafeUrlError


@pytest.mark.parametrize("encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress)])
def test_compression_is_bounded_before_allocation(encoding, compress):
    body = BoundedBody(encoding, 1024)
    with pytest.raises(UnsafeUrlError, match="size limit"):
        body.append(compress(b"x" * 1_000_000))
    assert len(body.body) <= 1024


@pytest.mark.parametrize("encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress)])
def test_valid_compressed_stream(encoding, compress):
    compressed = compress(b"article text")
    body = BoundedBody(encoding, 1024)
    for value in compressed:
        body.append(bytes([value]))
    assert body.finish() == b"article text"


@pytest.mark.parametrize(
    "data", [b"not gzip", gzip.compress(b"hello")[:-1], gzip.compress(b"hello") + b"extra"]
)
def test_invalid_compressed_stream_fails(data):
    with pytest.raises(UnsafeUrlError):
        body = BoundedBody("gzip", 1024)
        body.append(data)
        body.finish()


def test_async_redirect_rejects_private_ipv6_and_closes_response():
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://[::1]/private"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(UnsafeUrlError):
                await get_checked_response_async("http://93.184.216.34/start", client)

    asyncio.run(run())
    assert requests == ["http://93.184.216.34/start"]


def test_total_deadline_stops_slow_stream_and_closes_it():
    class SlowBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            for _ in range(20):
                await asyncio.sleep(0.01)
                yield b"x"

        async def aclose(self):
            self.closed = True

    body = SlowBody()

    async def run():
        async with httpx.AsyncClient(
            timeout=0.04,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=body)),
        ) as client:
            with pytest.raises(httpx.ReadTimeout, match="total time"):
                await get_checked_response_async("http://93.184.216.34/slow", client)

    asyncio.run(run())
    assert body.closed
