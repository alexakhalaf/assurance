"""OIDC sign-in against a mock identity provider that signs real RS256 ID tokens and checks PKCE."""
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from assurance.config import get_settings
from assurance.security import oidc

API = "/api/v1"
ISSUER = "https://idp.example.net/realms/net"


class MockIdP:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwks = {"keys": [{**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}]}
        self.codes: dict[str, dict] = {}
        self.claims = {"preferred_username": "dana", "groups": ["netops"], "email": "dana@example.net"}
        self.tamper = None

    def authorize(self, url: str) -> str:
        """What the IdP does after the user signs in: redirect back with a code."""
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert q["code_challenge_method"] == "S256" and q["response_type"] == "code"
        code = f"code-{len(self.codes)}"
        self.codes[code] = q
        return f"{q['redirect_uri']}?code={code}&state={q['state']}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/auth",
                                             "token_endpoint": f"{ISSUER}/token", "jwks_uri": f"{ISSUER}/certs"})
        if request.url.path.endswith("/certs"):
            return httpx.Response(200, json=self.jwks)
        if request.url.path.endswith("/token"):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            q = self.codes.pop(form["code"], None)
            if q is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()) \
                .decode().rstrip("=")
            if challenge != q["code_challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "pkce"})
            now = int(time.time())
            claims = {"iss": ISSUER, "aud": q["client_id"], "sub": "user-123", "iat": now, "exp": now + 300,
                      "nonce": q["nonce"], **self.claims}
            if self.tamper:
                claims.update(self.tamper)
            tok = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})
            return httpx.Response(200, json={"id_token": tok, "access_token": "x", "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture
def idp(monkeypatch):
    m = MockIdP()
    s = get_settings()
    for k, v in {"oidc_issuer": ISSUER, "oidc_client_id": "assurance", "oidc_client_secret": "s3cret",
                 "oidc_redirect_uri": "http://testserver/api/v1/auth/oidc/callback",
                 "oidc_role_map": {"netops": "operator", "netops-admins": "admin"}}.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(oidc, "_client", lambda: httpx.Client(transport=httpx.MockTransport(m.handler)))
    oidc._cache.clear()
    return m


def sso(client, idp):
    r = client.get(f"{API}/auth/oidc/login", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith(f"{ISSUER}/auth?")
    back = idp.authorize(r.headers["location"])
    r = client.get(back.replace("http://testserver", ""), follow_redirects=False)
    assert r.status_code == 302
    return parse_qs(urlparse(r.headers["location"]).query)


def test_sso_login(client, idp):
    assert client.get(f"{API}/auth/oidc").json()["enabled"] is True
    q = sso(client, idp)
    code = q["sso"][0]
    r = client.post(f"{API}/auth/oidc/exchange", json={"code": code})
    assert r.status_code == 200
    tok = r.json()["token"]
    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me["username"] == "dana" and me["role"] == "operator" and me["auth_source"] == "oidc"
    # the handoff code is single use
    assert client.post(f"{API}/auth/oidc/exchange", json={"code": code}).status_code == 401
    # SSO users cannot use the password form
    assert client.post(f"{API}/auth/login", json={"username": "dana", "password": "!oidc"}).status_code == 401
    # Role follows the IdP groups on the next sign-in
    idp.claims["groups"] = ["netops", "netops-admins"]
    tok2 = client.post(f"{API}/auth/oidc/exchange", json={"code": sso(client, idp)["sso"][0]}).json()
    assert tok2["user"]["role"] == "admin"


def test_sso_rejections(client, idp):
    idp.claims["groups"] = ["finance"]
    assert "no Assurance role" in sso(client, idp)["sso_error"][0]
    idp.claims["groups"] = ["netops"]
    idp.tamper = {"nonce": "replayed"}
    assert "could not be verified" in sso(client, idp)["sso_error"][0]
    idp.tamper = {"aud": "other-app"}
    assert "could not be verified" in sso(client, idp)["sso_error"][0]
    idp.tamper = None
    idp.claims["preferred_username"] = "admin"  # a local account: never taken over by SSO
    assert "local account" in sso(client, idp)["sso_error"][0]
    r = client.get(f"{API}/auth/oidc/callback?state=forged&code=x", follow_redirects=False)
    assert "sso_error" in r.headers["location"]


def test_local_login_can_be_disabled(client, monkeypatch):
    from conftest import PASSWORD

    monkeypatch.setattr(get_settings(), "local_login", False)
    assert client.post(f"{API}/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 403
    assert client.get(f"{API}/auth/oidc").json() == {"enabled": False, "name": "Single sign-on", "local_login": False}
