from __future__ import annotations

import pytest

from morning_radio.newsroom.fetch import UnsafeUrlError, assert_safe_public_url


def test_rejects_unsafe_schemes() -> None:
    with pytest.raises(UnsafeUrlError):
        assert_safe_public_url("file:///etc/passwd")


def test_rejects_loopback() -> None:
    with pytest.raises(UnsafeUrlError):
        assert_safe_public_url("http://127.0.0.1:11434")
