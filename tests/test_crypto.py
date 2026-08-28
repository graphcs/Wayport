"""Tests for shared-secret encryption."""

from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from wayport.common.crypto import decrypt, derive_key, encrypt


def test_derive_key_is_deterministic_and_256_bit() -> None:
    key = derive_key("hunter2")
    assert key == derive_key("hunter2")
    assert len(key) == 32


def test_different_secrets_derive_different_keys() -> None:
    assert derive_key("hunter2") != derive_key("hunter3")


def test_encrypt_decrypt_roundtrip() -> None:
    key = derive_key("hunter2")
    assert decrypt(encrypt(b"attack at dawn", key), key) == b"attack at dawn"


def test_encrypt_is_randomized() -> None:
    """A fresh nonce per call means identical plaintext yields distinct output."""
    key = derive_key("hunter2")
    assert encrypt(b"same", key) != encrypt(b"same", key)


def test_decrypt_with_wrong_key_fails_loudly() -> None:
    """The wrong secret must raise, not silently return garbage.

    Both peers currently swallow this exception, which is why a mismatched
    --secret presents as a hung browser rather than an error.
    """
    ciphertext = encrypt(b"secret", derive_key("right"))
    with pytest.raises(InvalidTag):
        decrypt(ciphertext, derive_key("wrong"))


def test_decrypt_rejects_tampered_ciphertext() -> None:
    key = derive_key("hunter2")
    blob = bytearray(encrypt(b"secret", key))
    blob[-1] ^= 0x01
    with pytest.raises(InvalidTag):
        decrypt(bytes(blob), key)


def test_decrypt_rejects_input_shorter_than_nonce() -> None:
    with pytest.raises(ValueError, match=".*"):
        decrypt(b"tooshort", derive_key("hunter2"))


def test_empty_payload_roundtrip() -> None:
    key = derive_key("hunter2")
    assert decrypt(encrypt(b"", key), key) == b""
