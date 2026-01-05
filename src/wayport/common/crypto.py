"""Encryption utilities using shared secret."""

from __future__ import annotations

import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def derive_key(secret: str, salt: bytes = b"wayport-v1") -> bytes:
    """Derive a 256-bit key from a secret using PBKDF2.

    Args:
        secret: The shared secret string
        salt: Salt for key derivation (default: "wayport-v1")

    Returns:
        32-byte key suitable for AES-256
    """
    return hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        salt,
        iterations=100000,
    )


def encrypt(data: bytes, key: bytes) -> bytes:
    """Encrypt data using AES-256-GCM.

    Args:
        data: Plaintext data to encrypt
        key: 32-byte encryption key

    Returns:
        Encrypted data: nonce (12 bytes) + ciphertext + tag (16 bytes)
    """
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    return nonce + ciphertext


def decrypt(data: bytes, key: bytes) -> bytes:
    """Decrypt data using AES-256-GCM.

    Args:
        data: Encrypted data (nonce + ciphertext + tag)
        key: 32-byte encryption key

    Returns:
        Decrypted plaintext data

    Raises:
        cryptography.exceptions.InvalidTag: If authentication fails
    """
    if len(data) < 12:
        raise ValueError("Encrypted data too short")
    nonce = data[:12]
    ciphertext = data[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)
