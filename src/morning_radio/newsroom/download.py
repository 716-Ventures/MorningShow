"""Checked redirects and bounded streaming decompression for discovery/research."""

from __future__ import annotations

import asyncio
import zlib
from urllib.parse import urljoin

import httpx

from morning_radio.newsroom.fetch import UnsafeUrlError, assert_safe_public_url

MAX_REDIRECTS = 5
REDIRECTS = {301, 302, 303, 307, 308}


class BoundedBody:
    def __init__(self, encoding: str, limit: int) -> None:
        self.limit = limit
        self.raw_size = 0
        self.body = bytearray()
        encoding = encoding.strip().lower()
        if encoding not in {"", "identity", "gzip", "deflate"}:
            raise UnsafeUrlError(f"Unsupported content encoding: {encoding}")
        self.decoder = (
            zlib.decompressobj(31 if encoding == "gzip" else 15)
            if encoding in {"gzip", "deflate"}
            else None
        )

    def append(self, chunk: bytes) -> None:
        self.raw_size += len(chunk)
        if self.raw_size > self.limit:
            raise UnsafeUrlError("Encoded body exceeded configured size limit")
        try:
            decoded = (
                self.decoder.decompress(chunk, self.limit - len(self.body) + 1)
                if self.decoder
                else chunk
            )
        except zlib.error as exc:
            raise UnsafeUrlError("Invalid compressed response") from exc
        if len(self.body) + len(decoded) > self.limit:
            raise UnsafeUrlError("Decoded body exceeded configured size limit")
        self.body.extend(decoded)

    def finish(self) -> bytes:
        if self.decoder and (not self.decoder.eof or self.decoder.unused_data):
            raise UnsafeUrlError("Incomplete or trailing compressed response")
        return bytes(self.body)


def buffered_response(response: httpx.Response, body: bytes) -> httpx.Response:
    headers = dict(response.headers)
    headers.pop("content-encoding", None)
    headers.pop("content-length", None)
    return httpx.Response(
        response.status_code, headers=headers, content=body, request=response.request
    )


def read_body(response: httpx.Response, limit: int) -> bytes:
    # Prebuffered responses are used by injected test transports only.
    if response.is_stream_consumed:
        if len(response.content) > limit:
            raise UnsafeUrlError("Decoded body exceeded configured size limit")
        return response.content
    body = BoundedBody(response.headers.get("content-encoding", ""), limit)
    for chunk in response.iter_raw():
        body.append(chunk)
    return body.finish()


async def read_body_async(response: httpx.Response, limit: int) -> bytes:
    if response.is_stream_consumed:
        return read_body(response, limit)
    body = BoundedBody(response.headers.get("content-encoding", ""), limit)
    async for chunk in response.aiter_raw():
        body.append(chunk)
    return body.finish()


def next_redirect(response: httpx.Response, current_url: str) -> str:
    location = response.headers.get("location")
    if not location:
        raise UnsafeUrlError(f"Redirect response missing Location header: {current_url}")
    return urljoin(current_url, location)


def get_checked_response(
    url: str, client: httpx.Client, *, max_bytes: int = 2_000_000
) -> httpx.Response:
    visited: set[str] = set()
    for _ in range(MAX_REDIRECTS + 1):
        assert_safe_public_url(url)
        if url in visited:
            raise UnsafeUrlError(f"Redirect loop detected: {url}")
        visited.add(url)
        with client.stream(
            "GET", url, follow_redirects=False, headers={"Accept-Encoding": "gzip, deflate"}
        ) as response:
            if response.status_code not in REDIRECTS:
                return buffered_response(response, read_body(response, max_bytes))
            url = next_redirect(response, url)
    raise UnsafeUrlError(f"Redirect limit exceeded after {MAX_REDIRECTS} redirects")


async def get_checked_response_async(
    url: str, client: httpx.AsyncClient, *, max_bytes: int = 2_000_000
) -> httpx.Response:
    visited: set[str] = set()
    for _ in range(MAX_REDIRECTS + 1):
        await asyncio.to_thread(assert_safe_public_url, url)
        if url in visited:
            raise UnsafeUrlError(f"Redirect loop detected: {url}")
        visited.add(url)
        async with client.stream(
            "GET", url, follow_redirects=False, headers={"Accept-Encoding": "gzip, deflate"}
        ) as response:
            if response.status_code not in REDIRECTS:
                return buffered_response(response, await read_body_async(response, max_bytes))
            url = next_redirect(response, url)
    raise UnsafeUrlError(f"Redirect limit exceeded after {MAX_REDIRECTS} redirects")
