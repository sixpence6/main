from __future__ import annotations

from typing import Any

from httpx import ASGITransport

from tests.support.auth import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD, TEST_ORIGIN, AuthClient, login


def _new_client(app: Any) -> AuthClient:
    return AuthClient(transport=ASGITransport(app=app), base_url=TEST_ORIGIN)


async def test_login_exposes_stable_session_lifecycle_id(app: Any) -> None:
    first = _new_client(app)
    first_body = await login(first, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
    first_session_id = first_body["session_id"]
    assert isinstance(first_session_id, str) and first_session_id

    fetched = await first.get("/api/auth/session")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["session_id"] == first_session_id

    listed = await first.get("/api/auth/sessions")
    assert listed.status_code == 200, listed.text
    current = [item for item in listed.json()["items"] if item["current"]]
    assert len(current) == 1
    assert current[0]["id"] == first_session_id

    # 同一账号重新登录也必须换一个登录生命周期，不能沿用旧会话标识。
    second = _new_client(app)
    second_body = await login(second, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
    second_session_id = second_body["session_id"]
    assert second_session_id
    assert second_session_id != first_session_id
    assert second_body["account"]["id"] == first_body["account"]["id"]

    second_session = await second.get("/api/auth/session")
    assert second_session.status_code == 200, second_session.text
    assert second_session.json()["session_id"] == second_session_id

    # 旧会话仍是独立身份，不能把新会话的生命周期串到旧客户端。
    first_again = await first.get("/api/auth/session")
    assert first_again.status_code == 200, first_again.text
    assert first_again.json()["session_id"] == first_session_id
