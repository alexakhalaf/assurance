"""Rate limits, request size cap and trusted proxy handling."""
from assurance.config import get_settings
from assurance.security.ratelimit import _trusted, client_ip

API = "/api/v1"


def test_login_rate_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_login_per_minute", 3)
    codes = [client.post(f"{API}/auth/login", json={"username": "admin", "password": "wrong"}).status_code
             for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    r = client.post(f"{API}/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) <= 60
    assert r.headers["content-type"] == "application/problem+json" and r.headers["X-Request-ID"]


def test_api_rate_limit_per_token(client, admin_h, view_h, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 5)
    codes = [client.get(f"{API}/devices", headers=admin_h).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429
    # another token has its own budget
    r = client.get(f"{API}/devices", headers=view_h)
    assert r.status_code == 200 and r.headers["X-RateLimit-Limit"] == "5"
    # health and the UI are never limited
    assert client.get("/healthz").status_code == 200


def test_request_size_cap(client, admin_h, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_request_bytes", 1000)
    r = client.post(f"{API}/devices", headers=admin_h, content=b"x" * 2000)
    assert r.status_code == 413


def test_trusted_proxy(monkeypatch):
    s = get_settings()
    assert client_ip("203.0.113.9", "1.2.3.4") == "203.0.113.9"  # no trusted proxies: header ignored
    monkeypatch.setattr(s, "trusted_proxies", ["10.0.0.0/8"])
    _trusted.cache_clear()
    assert client_ip("10.1.1.1", "198.51.100.7, 10.2.2.2") == "198.51.100.7"
    # a client cannot spoof by prepending its own header: the right-most untrusted hop wins
    assert client_ip("10.1.1.1", "6.6.6.6, 198.51.100.7") == "198.51.100.7"
    assert client_ip("203.0.113.9", "6.6.6.6") == "203.0.113.9"  # untrusted peer
