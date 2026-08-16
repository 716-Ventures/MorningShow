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
    if parts.username or parts.password:
        raise UnsafeUrlError("URL credentials are not allowed")
    try:
        _ = parts.port
    except ValueError as exc:
        raise UnsafeUrlError(f"Invalid URL port: {exc}") from exc
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("URL has no hostname")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Could not resolve host: {host}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise UnsafeUrlError(f"Refusing non-public destination: {address}")
