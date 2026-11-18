"""Envelope encryption for device credentials.

Each credential profile gets its own random 256-bit data key. Secrets are encrypted with the data
key (AES-256-GCM); the data key is encrypted ("wrapped") with the master key, which lives outside
the database (mounted file or env var). A database dump alone reveals no secret.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import get_settings

_NONCE = 12
_VERSION = b"\x01"


class CryptoError(Exception):
    pass


def generate_master_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def _master_key() -> bytes:
    s = get_settings()
    raw = None
    if s.master_key_file and s.master_key_file.exists():
        raw = s.master_key_file.read_text().strip()
    elif s.master_key:
        raw = s.master_key.strip()
    if not raw:
        raise CryptoError("No master key: set ASSURANCE_MASTER_KEY_FILE or ASSURANCE_MASTER_KEY")
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise CryptoError("Master key must be 32 bytes, base64 encoded")
    return key


def _seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(_NONCE)
    return _VERSION + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _open(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if not blob or blob[:1] != _VERSION:
        raise CryptoError("Unknown ciphertext version")
    nonce, ct = blob[1 : 1 + _NONCE], blob[1 + _NONCE :]
    try:
        return AESGCM(key).decrypt(nonce, ct, aad)
    except Exception as e:  # InvalidTag
        raise CryptoError("Decryption failed (wrong master key or tampered data)") from e


def new_wrapped_key() -> bytes:
    return _seal(_master_key(), os.urandom(32), b"assurance:data-key")


def _data_key(wrapped: bytes) -> bytes:
    return _open(_master_key(), wrapped, b"assurance:data-key")


def encrypt_secret(wrapped_key: bytes, secret: str, context: str) -> bytes:
    return _seal(_data_key(wrapped_key), secret.encode(), context.encode())


def decrypt_secret(wrapped_key: bytes, blob: bytes, context: str) -> str:
    return _open(_data_key(wrapped_key), blob, context.encode()).decode()
