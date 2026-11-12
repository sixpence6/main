from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from typing import Any

from tests.support.auth import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD, bootstrap_admin_sync

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def process_auth_env(settings: Any, port: int) -> dict[str, str]:
    """Explicit loopback development override for real-uvicorn tests only."""

    return {
        "ALLOWED_HOSTS": json.dumps(["127.0.0.1", "localhost"]),
        "ALLOWED_ORIGINS": json.dumps([f"http://127.0.0.1:{port}"]),
        "ALLOW_LOOPBACK_HTTP": "true",
        "AUTH_COOKIE_SECURE": "false",
    }


class ProcessSession:
    """Browser-like HTTP session for a real uvicorn subprocess."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.origin = self.base_url
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.csrf_token: str | None = None

    def cookies(self) -> dict[str, str]:
        return {cookie.name: cookie.value for cookie in self.jar}

    def login(self, email: str = TEST_ADMIN_EMAIL, password: str = TEST_ADMIN_PASSWORD) -> dict[str, Any]:
        status, body = self.request("GET", "/api/auth/csrf")
        assert status == 200, body
        self.csrf_token = body["csrf_token"]
        status, body = self.request(
            "POST", "/api/auth/login", {"email": email, "password": password}
        )
        assert status == 200, body
        self.csrf_token = body["csrf_token"]
        return body

    def request(
        self, method: str, path: str, payload: Any = None, timeout: float = 30
    ) -> tuple[int, Any]:
        method = method.upper()
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers: dict[str, str] = {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if method not in SAFE_METHODS:
            headers["Origin"] = self.origin
            if self.csrf_token:
                headers["X-CSRF-Token"] = self.csrf_token
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read()
                return response.status, json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read()
            return error.code, json.loads(raw.decode("utf-8")) if raw else None

    def bytes(self, path: str, timeout: float = 10):
        request = urllib.request.Request(self.base_url + path, method="GET")
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def httpx_client(self, timeout: float = 20):
        import httpx

        return httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            cookies=self.cookies(),
            headers={"Origin": self.origin, "X-CSRF-Token": self.csrf_token or ""},
        )


_sessions: dict[int, ProcessSession] = {}
_prepared_databases: set[str] = set()


def process_session(port: int) -> ProcessSession:
    session = _sessions.get(port)
    if session is None:
        session = ProcessSession(port)
        session.login()
        _sessions[port] = session
    return session


def bootstrap_process_admin(settings: Any) -> None:
    bootstrap_admin_sync(settings)


def prepare_process(settings: Any) -> None:
    """Bootstrap the admin once per database before starting a real server.

    A second bootstrap after a uvicorn process has touched the same SQLite
    file can hit a transient WAL-mode lock on Windows, so repeated starts for
    one test database are a no-op: the first call already created the admin.
    """

    key = str(settings.database_path)
    if key in _prepared_databases:
        return
    bootstrap_admin_sync(settings)
    _prepared_databases.add(key)
