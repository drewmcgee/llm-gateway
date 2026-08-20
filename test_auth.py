from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import db
from broadcast import Broadcaster
from main import redact_headers, require_api_key


def fake_request(conn, broadcaster=None, method="POST",
                  url="http://localhost:8000/v1/chat/completions",
                  headers=None, body=b""):
    async def _body():
        return body

    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            db=conn, broadcaster=broadcaster or Broadcaster())),
        method=method,
        url=url,
        headers=headers or {},
        body=_body,
    )


async def latest_log(conn):
    cursor = await conn.execute(
        "SELECT status_code, api_key_label, model FROM logs ORDER BY id DESC LIMIT 1")
    return await cursor.fetchone()


@pytest.mark.asyncio
async def test_require_api_key_rejects_missing_header(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        with pytest.raises(HTTPException) as exc_info:
            await require_api_key(fake_request(conn), x_api_key=None)
    finally:
        await conn.close()

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_api_key_rejects_unknown_key(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        with pytest.raises(HTTPException) as exc_info:
            await require_api_key(fake_request(conn), x_api_key="gw_never-issued")
    finally:
        await conn.close()

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_api_key_accepts_valid_key_and_returns_label(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        raw_key = await db.create_api_key(conn, "demo-user")
        label = await require_api_key(fake_request(conn), x_api_key=raw_key)
    finally:
        await conn.close()

    assert label == "demo-user"


@pytest.mark.asyncio
async def test_rejected_request_with_missing_key_is_logged(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        with pytest.raises(HTTPException):
            await require_api_key(fake_request(conn), x_api_key=None)
        row = await latest_log(conn)
    finally:
        await conn.close()

    assert row == (401, None, None)


@pytest.mark.asyncio
async def test_rejected_request_with_invalid_key_is_logged(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        with pytest.raises(HTTPException):
            await require_api_key(fake_request(conn), x_api_key="gw_never-issued")
        row = await latest_log(conn)
    finally:
        await conn.close()

    assert row == (401, None, None)


@pytest.mark.asyncio
async def test_rejected_request_captures_attempted_model(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        body = b'{"model": "gpt-4", "messages": []}'
        with pytest.raises(HTTPException):
            await require_api_key(fake_request(conn, body=body), x_api_key=None)
        row = await latest_log(conn)
    finally:
        await conn.close()

    assert row == (401, None, "gpt-4")


@pytest.mark.asyncio
async def test_rejected_request_broadcasts_to_dashboard(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    broadcaster = Broadcaster()
    queue = broadcaster.subscribe()
    try:
        with pytest.raises(HTTPException):
            await require_api_key(
                fake_request(conn, broadcaster=broadcaster), x_api_key=None)
    finally:
        await conn.close()

    event = queue.get_nowait()
    assert event["status_code"] == 401
    assert event["api_key_label"] is None


@pytest.mark.asyncio
async def test_valid_key_does_not_log_anything_itself(tmp_path):
    # require_api_key only logs on rejection -- a successful lookup just
    # returns the label; persist_log (called later, after the upstream
    # response) is what logs the eventual outcome of the proxied request.
    conn = await db.connect(tmp_path / "logs.db")
    try:
        raw_key = await db.create_api_key(conn, "demo-user")
        await require_api_key(fake_request(conn), x_api_key=raw_key)
        cursor = await conn.execute("SELECT COUNT(*) FROM logs")
        count = (await cursor.fetchone())[0]
    finally:
        await conn.close()

    assert count == 0


def test_redact_headers_redacts_authorization():
    result = redact_headers({"Authorization": "Bearer sk-real-openai-key"})
    assert result["Authorization"] == "REDACTED"


def test_redact_headers_redacts_x_api_key_case_insensitive():
    result = redact_headers({"x-api-key": "gw_super-secret"})
    assert result["x-api-key"] == "REDACTED"


def test_redact_headers_leaves_other_headers_untouched():
    result = redact_headers({"content-type": "application/json"})
    assert result["content-type"] == "application/json"
