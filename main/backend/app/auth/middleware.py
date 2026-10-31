from __future__ import annotations

import contextlib
import json
from typing import Any
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders

from app.auth.sessions import (
    PREAUTH_CSRF_COOKIE,
    SESSION_COOKIE,
    load_session,
    touch_session,
)
from app.auth.tokens import constant_time_equals
from app.config import Settings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PUBLIC_API_PATHS = frozenset(
    {
        "/api/health",
        "/api/config/status",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/session",
        "/api/auth/csrf",
    }
)
AUTH_JSON_BODY_LIMIT = 16 * 1024


def _json_bytes(code: str, message: str) -> bytes:
    return json.dumps(
        {"error": {"code": code, "message": message}}, ensure_ascii=False
    ).encode("utf-8")


async def _send_json(
    send: Any,
    status: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> None:
    body = _json_bytes(code, message)
    raw_headers = [(b"content-type", b"application/json; charset=utf-8")]
    raw_headers.append((b"content-length", str(len(body)).encode("ascii")))
    raw_headers.append((b"cache-control", b"no-store"))
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
    await send({"type": "http.response.start", "status": status, "headers": raw_headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})


def _host_only(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[: end + 1] if end >= 0 else value
    return value.split(":", 1)[0]


def _allowed_hosts(settings: Settings) -> set[str]:
    hosts = {host.lower() for host in settings.allowed_hosts}
    hosts.add(settings.host.lower())
    return hosts


def _allowed_origins(settings: Settings) -> set[str]:
    origins = {origin.rstrip("/").lower() for origin in settings.allowed_origins}
    for host in _allowed_hosts(settings):
        origins.add(f"https://{host}".lower())
        origins.add(f"https://{host}:{settings.port}".lower())
        if settings.allow_loopback_http and host in {"127.0.0.1", "localhost", "[::1]"}:
            origins.add(f"http://{host}".lower())
            origins.add(f"http://{host}:{settings.port}".lower())
    return origins


def check_host(headers: Headers, settings: Settings) -> bool:
    host = headers.get("host")
    if not host:
        return False
    return _host_only(host) in _allowed_hosts(settings)


def check_origin(origin: str | None, settings: Settings) -> bool:
    if not origin:
        return False
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.username or parsed.password or parsed.path not in {"", "/"}:
        return False
    if parsed.query or parsed.fragment:
        return False
    candidate = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    return candidate in _allowed_origins(settings)


async def _read_limited_body(receive: Any, limit: int) -> Any | None:
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        chunk = message.get("body", b"")
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
        if not message.get("more_body"):
            break
    body = b"".join(chunks)
    sent = False

    async def replay() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


class AuthMiddleware:
    """One guard: Host/Origin, body cap, default-deny, CSRF and idle touch."""

    def __init__(self, app: Any, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        headers = Headers(scope=scope)
        if not check_host(headers, self.settings):
            await _send_json(send, 403, "HOST_INVALID", "请求来源不受信任")
            return
        is_api = path == "/api" or path.startswith("/api/")
        if not is_api:
            await self.app(scope, receive, send)
            return
        if method not in SAFE_METHODS:
            origin = headers.get("origin")
            if not check_origin(origin, self.settings):
                await _send_json(send, 403, "ORIGIN_INVALID", "请求来源校验失败")
                return
            if path.startswith("/api/auth/"):
                content_length = headers.get("content-length")
                if content_length:
                    try:
                        declared = int(content_length)
                    except ValueError:
                        await _send_json(send, 400, "INVALID_REQUEST", "请求格式不合法")
                        return
                    if declared > AUTH_JSON_BODY_LIMIT:
                        await _send_json(send, 413, "AUTH_BODY_TOO_LARGE", "请求体过大")
                        return
                replay = await _read_limited_body(receive, AUTH_JSON_BODY_LIMIT)
                if replay is None:
                    await _send_json(send, 413, "AUTH_BODY_TOO_LARGE", "请求体过大")
                    return
                receive = replay
        state = scope.setdefault("state", {})
        context = None
        raw_cookie = headers.get("cookie") or ""
        cookie_map: dict[str, str] = {}
        for part in raw_cookie.split(";"):
            if "=" in part:
                key, value = part.split("=", 1)
                cookie_map[key.strip()] = value.strip()
        raw_session = cookie_map.get(SESSION_COOKIE)
        app_state = scope["app"].state
        if raw_session:
            try:
                context = await load_session(
                    app_state.session_factory, raw_session, app_state.clock.now()
                )
            except Exception:
                # A session cookie that cannot be revalidated is never anonymous.
                needs_session = path in {"/api/auth/session", "/api/auth/logout"} or (
                    path == "/api/auth/csrf" and bool(raw_session)
                )
                if path not in PUBLIC_API_PATHS or needs_session:
                    await _send_json(send, 503, "AUTH_UNAVAILABLE", "认证服务当前不可用")
                    return
                context = None
        if context is not None:
            state["principal"] = {
                "account_id": context.account.id,
                "email": context.account.email_normalized,
                "is_admin": bool(context.account.is_admin),
                "session_id": context.session.id,
                "account_epoch": context.account.session_epoch,
            }
            state["auth_session"] = context.session
            state["auth_account"] = context.account
        public = path in PUBLIC_API_PATHS
        if not public and context is None:
            await _send_json(send, 401, "AUTH_REQUIRED", "请先登录")
            return
        if method not in SAFE_METHODS:
            if path == "/api/auth/login":
                presented = headers.get("x-csrf-token")
                cookie_token = cookie_map.get(PREAUTH_CSRF_COOKIE)
                if not presented or not cookie_token or not constant_time_equals(
                    presented, cookie_token
                ):
                    await _send_json(send, 403, "CSRF_INVALID", "请求校验失败，请刷新页面重试")
                    return
            else:
                if context is None:
                    await _send_json(send, 401, "AUTH_REQUIRED", "请先登录")
                    return
                presented = headers.get("x-csrf-token")
                cookie_token = cookie_map.get(PREAUTH_CSRF_COOKIE)
                if not presented or not cookie_token or not constant_time_equals(
                    presented, cookie_token
                ):
                    await _send_json(send, 403, "CSRF_INVALID", "请求校验失败，请刷新页面重试")
                    return
                from app.auth.tokens import token_digest

                if not constant_time_equals(context.session.csrf_digest, token_digest(presented)):
                    await _send_json(send, 403, "CSRF_INVALID", "请求校验失败，请刷新页面重试")
                    return
        status_holder: dict[str, int] = {}

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message["status"])
                mutable = MutableHeaders(scope=message)
                mutable["Cache-Control"] = "no-store"
                mutable["Pragma"] = "no-cache"
                mutable["X-Content-Type-Options"] = "nosniff"
                mutable["Referrer-Policy"] = "no-referrer"
                mutable["X-Frame-Options"] = "DENY"
            await send(message)

        await self.app(scope, receive, send_with_headers)
        status = status_holder.get("status", 500)
        polling = (headers.get("x-polling") or "").lower() in {"1", "true"}
        active_read = (
            method == "GET"
            and (headers.get("x-auth-activity") or "").lower() == "user"
            and path not in PUBLIC_API_PATHS
        )
        should_touch = (
            context is not None
            and status < 400
            and not polling
            and (method not in SAFE_METHODS or active_read)
        )
        if should_touch:
            with contextlib.suppress(Exception):
                await touch_session(
                    app_state.session_factory, context.session.id, app_state.clock.now()
                )
