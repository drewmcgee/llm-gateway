from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import db
from main import redact_headers, require_api_key


def fake_request(conn):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=conn)))


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


def test_redact_headers_redacts_authorization():
    result = redact_headers({"Authorization": "Bearer sk-real-openai-key"})
    assert result["Authorization"] == "REDACTED"


def test_redact_headers_redacts_x_api_key_case_insensitive():
    result = redact_headers({"x-api-key": "gw_super-secret"})
    assert result["x-api-key"] == "REDACTED"


def test_redact_headers_leaves_other_headers_untouched():
    result = redact_headers({"content-type": "application/json"})
    assert result["content-type"] == "application/json"
