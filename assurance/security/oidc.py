"""OpenID Connect sign-in: authorization code flow with PKCE, ID token verified against the
identity provider's published keys (issuer, audience, expiry and nonce checked)."""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time

import httpx
import jwt

from ..config import get_settings

ROLE_ORDER = {"viewer": 1, "operator": 2, "admin": 3}
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()
TTL = 3600


class OidcError(Exception):
    pass


def enabled() -> bool:
    s = get_settings()
    return bool(s.oidc_issuer and s.oidc_client_id)


def _client() -> httpx.Client:  # replaced in tests
    return httpx.Client(timeout=10, verify=True)


def _get_json(url: str) -> dict:
    with _lock:
        hit = _cache.get(url)
        if hit and hit[0] > time.monotonic():
            return hit[1]
    with _client() as c:
        r = c.get(url)
        r.raise_for_status()
        data = r.json()
    with _lock:
        _cache[url] = (time.monotonic() + TTL, data)
    return data


def metadata() -> dict:
    issuer = get_settings().oidc_issuer.rstrip("/")
    md = _get_json(f"{issuer}/.well-known/openid-configuration")
    if md.get("issuer", "").rstrip("/") != issuer:
        raise OidcError("issuer in the discovery document does not match ASSURANCE_OIDC_ISSUER")
    return md


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize_url(state: str, nonce: str, challenge: str) -> str:
    s = get_settings()
    params = {"response_type": "code", "client_id": s.oidc_client_id, "redirect_uri": s.oidc_redirect_uri,
              "scope": s.oidc_scopes, "state": state, "nonce": nonce, "code_challenge": challenge,
              "code_challenge_method": "S256"}
    return str(httpx.URL(metadata()["authorization_endpoint"], params=params))


def exchange(code: str, verifier: str, nonce: str) -> dict:
    """Code for tokens; returns the verified ID token claims."""
    s = get_settings()
    md = metadata()
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": s.oidc_redirect_uri,
            "code_verifier": verifier, "client_id": s.oidc_client_id}
    auth = (s.oidc_client_id, s.oidc_client_secret) if s.oidc_client_secret else None
    with _client() as c:
        r = c.post(md["token_endpoint"], data=data, auth=auth)
    if r.status_code != 200:
        raise OidcError(f"token endpoint returned {r.status_code}")
    id_token = r.json().get("id_token")
    if not id_token:
        raise OidcError("no id_token in the token response")
    return verify_id_token(id_token, nonce)


def verify_id_token(token: str, nonce: str) -> dict:
    s = get_settings()
    md = metadata()
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as e:
        raise OidcError(f"malformed id_token: {e}") from e
    keys = _get_json(md["jwks_uri"]).get("keys", [])
    jwk = next((k for k in keys if k.get("kid") == kid), None) or (keys[0] if len(keys) == 1 else None)
    if jwk is None:
        _cache.pop(md["jwks_uri"], None)  # key rotation: refetch once
        keys = _get_json(md["jwks_uri"]).get("keys", [])
        jwk = next((k for k in keys if k.get("kid") == kid), None)
    if jwk is None:
        raise OidcError("id_token signed with an unknown key")
    key = jwt.PyJWK(jwk)
    try:
        claims = jwt.decode(token, key.key, algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"],
                            audience=s.oidc_client_id, issuer=md["issuer"], leeway=60,
                            options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except jwt.PyJWTError as e:
        raise OidcError(f"id_token rejected: {e}") from e
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OidcError("id_token nonce does not match")
    return claims


def role_for(claims: dict) -> str | None:
    s = get_settings()
    groups = claims.get(s.oidc_groups_claim) or []
    if isinstance(groups, str):
        groups = [groups]
    roles = [s.oidc_role_map[g] for g in groups if g in s.oidc_role_map]
    if roles:
        return max(roles, key=lambda r: ROLE_ORDER.get(r, 0))
    return None if s.oidc_default_role == "none" else s.oidc_default_role
