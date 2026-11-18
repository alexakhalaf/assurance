import base64

import pytest

from assurance.config import get_settings
from assurance.security import crypto
from assurance.security.passwords import hash_password, hash_token, new_token, verify_password


def test_envelope_roundtrip_and_context_binding():
    wk = crypto.new_wrapped_key()
    ct = crypto.encrypt_secret(wk, "hunter2", "credential:a:password")
    assert crypto.decrypt_secret(wk, ct, "credential:a:password") == "hunter2"
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_secret(wk, ct, "credential:b:password")  # blob moved to another profile
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_secret(wk, ct[:-1] + bytes([ct[-1] ^ 1]), "credential:a:password")


def test_wrong_master_key_fails(monkeypatch):
    wk = crypto.new_wrapped_key()
    ct = crypto.encrypt_secret(wk, "s", "ctx")
    monkeypatch.setattr(get_settings(), "master_key", base64.b64encode(b"z" * 32).decode())
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_secret(wk, ct, "ctx")


def test_master_key_required(monkeypatch):
    monkeypatch.setattr(get_settings(), "master_key", None)
    monkeypatch.setattr(get_settings(), "master_key_file", None)
    with pytest.raises(crypto.CryptoError):
        crypto.new_wrapped_key()


def test_passwords_and_tokens():
    h = hash_password("a-long-password")
    assert verify_password(h, "a-long-password") and not verify_password(h, "nope")
    t = new_token()
    assert t.startswith("asr_") and len(hash_token(t)) == 64 and new_token() != t
