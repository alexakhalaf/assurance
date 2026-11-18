"""Credential profiles: create/update with envelope encryption, and per-device resolution."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..collection.target import Credentials
from ..models import CredentialProfile, Device
from ..security import crypto

DEFAULT_PROFILE = "default"


def _ctx(name: str, field: str) -> str:
    # Bind ciphertext to its profile and field, so blobs can't be swapped between rows.
    return f"credential:{name}:{field}"


def set_secrets(profile: CredentialProfile, password: str | None, enable_password: str | None,
                clear_enable: bool = False) -> None:
    if not profile.wrapped_key:
        profile.wrapped_key = crypto.new_wrapped_key()
    if password is not None:
        profile.password_ct = crypto.encrypt_secret(profile.wrapped_key, password, _ctx(profile.name, "password"))
    if enable_password is not None:
        profile.enable_ct = crypto.encrypt_secret(profile.wrapped_key, enable_password, _ctx(profile.name, "enable"))
    elif clear_enable:
        profile.enable_ct = None


def decrypt(profile: CredentialProfile) -> Credentials:
    pw = crypto.decrypt_secret(profile.wrapped_key, profile.password_ct, _ctx(profile.name, "password"))
    en = (crypto.decrypt_secret(profile.wrapped_key, profile.enable_ct, _ctx(profile.name, "enable"))
          if profile.enable_ct else None)
    return Credentials(profile.username, pw, en)


def resolve_profile(db: Session, device: Device) -> CredentialProfile | None:
    """Most specific wins: device, then its site, then the profile named 'default'."""
    if device.credential is not None:
        return device.credential
    if device.site is not None and device.site.credential is not None:
        return device.site.credential
    return db.scalar(select(CredentialProfile).where(CredentialProfile.name == DEFAULT_PROFILE))
