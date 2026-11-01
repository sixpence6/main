from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.config import Settings
from app.request_source import client_ip
from app.security_headers import CONTENT_SECURITY_POLICY

ROOT = Path(__file__).resolve().parent.parent.parent
MAIN = ROOT / "main"


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "data_dir": tmp_path / "data",
        "web_dist_dir": tmp_path / "web",
        "allowed_hosts": ["127.0.0.1", "localhost"],
        "allowed_origins": [],
        "allow_loopback_http": False,
        "auth_cookie_secure": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_wildcards_and_public_http_are_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="wildcards"):
        _settings(tmp_path, allowed_hosts=["*"])
    with pytest.raises(ValueError, match="wildcards"):
        _settings(tmp_path, allowed_origins=["https://example.test", "*"])
    with pytest.raises(ValueError, match="HTTP origins require"):
        _settings(tmp_path, allowed_origins=["http://192.168.1.20:8090"])
    with pytest.raises(ValueError, match="loopback hosts"):
        _settings(
            tmp_path,
            allowed_origins=["http://192.168.1.20:8090"],
            allow_loopback_http=True,
            auth_cookie_secure=False,
        )
    with pytest.raises(ValueError, match="loopback hosts"):
        _settings(
            tmp_path,
            app_public_url="http://192.168.1.20:8090",
            allowed_hosts=["192.168.1.20"],
            allow_loopback_http=True,
            auth_cookie_secure=False,
        )
    with pytest.raises(ValueError, match="TRUSTED_PROXY_CIDRS"):
        _settings(tmp_path, trusted_proxy_cidrs=["0.0.0.0/0"])


def test_loopback_http_and_https_lan_are_explicit(tmp_path: Path):
    dev = _settings(
        tmp_path,
        allowed_origins=["http://127.0.0.1:8090"],
        allow_loopback_http=True,
        auth_cookie_secure=False,
        app_public_url="http://127.0.0.1:8090",
        allowed_hosts=["127.0.0.1", "localhost"],
    )
    assert dev.allowed_origins == ["http://127.0.0.1:8090"]

    lan = _settings(
        tmp_path,
        allowed_hosts=["10.13.51.8"],
        allowed_origins=["https://10.13.51.8:8443"],
        app_public_url="https://10.13.51.8:8443",
        trusted_proxy_cidrs=["172.16.0.0/12"],
    )
    assert lan.trusted_proxy_cidrs == ["172.16.0.0/12"]
    assert lan.allowed_origins == ["https://10.13.51.8:8443"]


def _request(tmp_path: Path, peer: str, headers: dict[str, str], **overrides) -> Request:
    settings = _settings(tmp_path, **overrides)
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/health",
        "headers": raw_headers,
        "client": (peer, 12345),
        "server": ("testserver", 443),
        "scheme": "https",
    }
    request = Request(scope)
    request.scope["app"] = SimpleNamespace(state=SimpleNamespace(settings=settings))
    return request


def test_client_ip_trusts_only_configured_proxy(tmp_path: Path):
    trusted = ["172.16.0.0/12"]
    assert (
        client_ip(
            _request(
                tmp_path,
                "172.18.0.5",
                {"x-real-ip": "10.13.51.23"},
                trusted_proxy_cidrs=trusted,
            )
        )
        == "10.13.51.23"
    )
    assert (
        client_ip(
            _request(
                tmp_path,
                "192.168.1.40",
                {"x-real-ip": "10.13.51.23"},
                trusted_proxy_cidrs=trusted,
            )
        )
        == "192.168.1.40"
    )
    assert (
        client_ip(
            _request(
                tmp_path,
                "172.18.0.5",
                {"x-real-ip": "forged.example"},
                trusted_proxy_cidrs=trusted,
            )
        )
        == "172.18.0.5"
    )
    assert client_ip(_request(tmp_path, "172.18.0.5", {"x-real-ip": "10.0.0.1"})) == "172.18.0.5"


async def test_api_and_static_responses_carry_csp(client):
    health = await client.get("/api/health")
    assert health.status_code == 200
    assert health.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert health.headers["x-frame-options"] == "DENY"
    assert health.headers["referrer-policy"] == "no-referrer"
    assert health.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"

    rejected = await client.get("/api/health", headers={"Host": "evil.example"})
    assert rejected.status_code == 403
    assert rejected.headers["content-security-policy"] == CONTENT_SECURITY_POLICY


def test_tls_templates_never_publish_backend_or_wildcard_origins():
    tls = (MAIN / "docker" / "nginx-tls.conf.template").read_text(encoding="utf-8")
    http = (MAIN / "docker" / "nginx.conf.template").read_text(encoding="utf-8")
    overlay = (MAIN / "docker-compose.lan.yml").read_text(encoding="utf-8")
    headers = (MAIN / "docker" / "security-headers.conf").read_text(encoding="utf-8")

    assert "listen 443 ssl;" in tls
    assert "ssl_certificate ${TLS_CERT_PATH};" in tls
    hsts = (MAIN / "docker" / "hsts.conf").read_text(encoding="utf-8")
    assert "Strict-Transport-Security" in hsts
    assert "return 308 https://" in tls
    assert "listen 443" not in http
    assert "Content-Security-Policy" in headers
    for name in (
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "X-Frame-Options",
        "Permissions-Policy",
    ):
        assert f"proxy_hide_header {name};" in tls
    assert "PUBLIC_BIND_ADDRESS" in overlay
    assert "ALLOW_LOOPBACK_HTTP: \"false\"" in overlay
    assert "AUTH_COOKIE_SECURE: \"true\"" in overlay
    assert "TRUSTED_PROXY_CIDRS" in overlay
    assert "127.0.0.1:8000:8000" in (MAIN / "docker-compose.yml").read_text(encoding="utf-8")
