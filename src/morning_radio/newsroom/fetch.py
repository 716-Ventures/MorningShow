from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeUrlError(RuntimeError):
    pass


def assert_safe_public_url(url: str) -> None:
    resolve_public_addresses(validate_public_url(url))


def validate_public_url(url: str) -> str:
    """Validate URL syntax and return its host, without performing DNS I/O."""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsafeUrlError(f"Malformed URL: {exc}") from exc
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
    return host


def resolve_public_addresses(host: str) -> list[str]:
    """Return only literal public addresses; reject mixed public/private answers."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    try:
        addresses = (
            [str(literal)]
            if literal is not None
            else [
                str(info[4][0]) for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            ]
        )
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Could not resolve host: {host}") from exc
    if not addresses:
        raise UnsafeUrlError(f"No addresses for host: {host}")
    for value in addresses:
        address = ipaddress.ip_address(value)
        if not address.is_global or address.is_multicast or address.is_unspecified:
            raise UnsafeUrlError(f"Refusing non-public destination: {address}")
    return list(dict.fromkeys(addresses))
