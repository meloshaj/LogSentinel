from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from backend.app.routers.auth_router import (
    _set_session_cookies,
    get_csrf_token,
)
from backend.app.services.sessions import CSRF_COOKIE_NAME, REFRESH_COOKIE_NAME


def _request(*, origin: str, cookie: str = "") -> Request:
    headers = [(b"origin", origin.encode())]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/api/auth/csrf",
            "raw_path": b"/api/auth/csrf",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("api.example.test", 443),
        }
    )


@pytest.mark.asyncio
async def test_csrf_bootstrap_requires_an_allowed_origin_and_existing_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRONTEND_URL", "https://frontend.example.test")
    response = Response()

    with pytest.raises(HTTPException) as missing_session:
        await get_csrf_token(_request(origin="https://frontend.example.test"), response)
    assert missing_session.value.status_code == 401

    with pytest.raises(HTTPException) as invalid_origin:
        await get_csrf_token(
            _request(
                origin="https://attacker.example.test",
                cookie=f"{REFRESH_COOKIE_NAME}=refresh; {CSRF_COOKIE_NAME}=csrf",
            ),
            response,
        )
    assert invalid_origin.value.status_code == 403

    response = Response()
    result = await get_csrf_token(
        _request(
            origin="https://frontend.example.test",
            cookie=f"{REFRESH_COOKIE_NAME}=refresh; {CSRF_COOKIE_NAME}=csrf-value",
        ),
        response,
    )
    assert result.csrf_token == "csrf-value"
    assert response.headers["Cache-Control"] == "no-store"


def test_production_cross_site_session_cookies_remain_secure() -> None:
    issued = SimpleNamespace(
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        refresh_token="refresh-value",
        csrf_token="csrf-value",
    )
    response = Response()

    _set_session_cookies(
        response,
        _request(origin="https://frontend.example.test"),
        issued,
    )

    cookies = [
        value.decode()
        for name, value in response.raw_headers
        if name.lower() == b"set-cookie"
    ]
    assert len(cookies) == 2
    assert all("SameSite=none" in cookie and "Secure" in cookie for cookie in cookies)
    refresh_cookie = next(
        cookie for cookie in cookies if cookie.startswith(REFRESH_COOKIE_NAME)
    )
    csrf_cookie = next(
        cookie for cookie in cookies if cookie.startswith(CSRF_COOKIE_NAME)
    )
    assert "HttpOnly" in refresh_cookie
    assert "HttpOnly" not in csrf_cookie
