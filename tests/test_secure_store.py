from __future__ import annotations

import sys

from pidtool.secure_store import DPAPI_PREFIX, available, protect, unprotect

WINDOWS = sys.platform.startswith("win")


def test_available_matches_platform():
    assert available() == WINDOWS


def test_plaintext_passthrough_and_empty():
    assert unprotect("") == ""
    assert unprotect("plain-value") == "plain-value"
    assert protect("") == ""


def test_protect_unprotect_roundtrip():
    if not WINDOWS:
        assert protect("hello") == "hello"
        return
    token = protect("hello-世界")
    assert token.startswith(DPAPI_PREFIX)
    assert "hello" not in token
    assert unprotect(token) == "hello-世界"


def test_unprotect_invalid_token():
    assert unprotect(DPAPI_PREFIX + "!!!not-base64!!!") == ""


def test_unprotect_foreign_ciphertext():
    if not WINDOWS:
        return
    other = protect("first")
    assert unprotect(other) == "first"
    assert unprotect(DPAPI_PREFIX + "AAAA") == ""
