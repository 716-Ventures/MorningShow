from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeUrlError(RuntimeError):
    pass


def assert_safe_public_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise UnsafeUrlError(f"Unsupported URL scheme: {parts.scheme}")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("URL has no hostname")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Could not resolve host: {host}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise UnsafeUrlError(f"Refusing private or local destination: {address}")
