from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from miniclaw2 import app as app_module
from miniclaw2.providers.claude_native import hook_runtime
from miniclaw2.webauth import SESSION_COOKIE


def _protected_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MINICLAW_PASSCODE", "1234")
    return TestClient(app_module.create_app())


def test_auth_is_disabled_by_default() -> None:
    client = TestClient(app_module.create_app())

    assert client.get("/auth/state").json() == {
        "required": False,
        "authenticated": True,
        "locked": False,
    }
    assert client.get("/migrations/status").status_code == 200


def test_login_sets_session_cookie_and_unlocks_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _protected_client(monkeypatch)

    denied = client.get("/migrations/status")
    assert denied.status_code == 401
    assert denied.json()["state"] == "auth_required"

    response = client.post("/auth/login", json={"passcode": "1234"})
    assert response.status_code == 204
    assert SESSION_COOKIE in client.cookies
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "max-age" not in cookie
    assert "expires" not in cookie
    assert client.get("/migrations/status").status_code == 200
    assert client.get("/auth/state").json()["authenticated"] is True

    logout = client.post("/auth/logout")
    assert logout.status_code == 204
    assert client.get("/migrations/status").status_code == 401


def test_success_before_limit_resets_failure_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _protected_client(monkeypatch)

    for remaining in range(9, 0, -1):
        response = client.post("/auth/login", json={"passcode": "0000"})
        assert response.status_code == 401
        assert f"还可尝试 {remaining} 次" in response.json()["detail"]

    assert client.post("/auth/login", json={"passcode": "1234"}).status_code == 204
    assert client.post("/auth/logout").status_code == 204
    assert client.post("/auth/login", json={"passcode": "0000"}).status_code == 401


def test_tenth_failure_locks_new_logins_but_keeps_existing_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINICLAW_PASSCODE", "1234")
    app = app_module.create_app()
    authenticated = TestClient(app)
    attacker = TestClient(app)

    assert authenticated.post("/auth/login", json={"passcode": "1234"}).status_code == 204
    for attempt in range(10):
        response = attacker.post("/auth/login", json={"passcode": "9999"})
        assert response.status_code == (423 if attempt == 9 else 401)

    locked = attacker.post("/auth/login", json={"passcode": "1234"})
    assert locked.status_code == 423
    assert locked.json()["state"] == "auth_locked"
    assert attacker.get("/auth/state").json()["locked"] is True
    assert authenticated.get("/migrations/status").status_code == 200


def test_health_and_hook_keep_their_own_access_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _protected_client(monkeypatch)

    assert client.get("/health").status_code == 200
    denied_hook = client.post("/hook/session-ready", json={"session_id": "s1"})
    assert denied_hook.status_code == 403
    accepted_hook = client.post(
        "/hook/session-ready",
        json={"session_id": "s1"},
        headers={"Authorization": f"Bearer {hook_runtime.token()}"},
    )
    assert accepted_hook.status_code == 200


def test_auth_routes_remain_available_during_storage_maintenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _protected_client(monkeypatch)
    client.app.state.storage_error = {
        "state": "migration_required",
        "detail": "maintenance",
    }

    assert client.get("/auth/state").status_code == 200
    assert client.post("/auth/login", json={"passcode": "1234"}).status_code == 204
    assert client.get("/migrations/status").status_code == 200


def test_websocket_requires_a_session_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _protected_client(monkeypatch)

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/ws/-"):
            pass

    assert caught.value.code == 4401


def test_invalid_environment_passcode_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINICLAW_PASSCODE", "12ab")

    with pytest.raises(ValueError, match="exactly four digits"):
        app_module.create_app()
