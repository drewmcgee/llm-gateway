import json

import pytest

import db


@pytest.mark.asyncio
async def test_insert_log_round_trips_all_fields(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        await db.insert_log(
            conn,
            method="POST",
            url="http://localhost:8000/v1/chat/completions",
            request_headers={"content-type": "application/json"},
            request_body=b'{"model": "gpt-5-mini"}',
            status_code=200,
            response_headers={"content-type": "application/json"},
            response_body=b'{"id": "chatcmpl-1"}',
            ttfb_ms=12.5,
            total_ms=345.0,
        )

        cursor = await conn.execute(
            "SELECT method, url, request_headers, request_body, "
            "status_code, response_headers, response_body, ttfb_ms, total_ms "
            "FROM logs"
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()

    (method, url, request_headers, request_body, status_code,
     response_headers, response_body, ttfb_ms, total_ms) = row

    assert method == "POST"
    assert url == "http://localhost:8000/v1/chat/completions"
    assert json.loads(request_headers) == {"content-type": "application/json"}
    assert request_body == b'{"model": "gpt-5-mini"}'
    assert status_code == 200
    assert json.loads(response_headers) == {"content-type": "application/json"}
    assert response_body == b'{"id": "chatcmpl-1"}'
    assert ttfb_ms == 12.5
    assert total_ms == 345.0


@pytest.mark.asyncio
async def test_insert_log_allows_null_bodies(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        await db.insert_log(
            conn,
            method="GET",
            url="http://localhost:8000/v1/models",
            request_headers={},
            request_body=None,
            status_code=200,
            response_headers={},
            response_body=None,
            ttfb_ms=1.0,
            total_ms=2.0,
        )

        cursor = await conn.execute(
            "SELECT request_body, response_body FROM logs")
        row = await cursor.fetchone()
    finally:
        await conn.close()

    assert row == (None, None)


@pytest.mark.asyncio
async def test_connect_creates_table_idempotently(tmp_path):
    path = tmp_path / "logs.db"

    conn1 = await db.connect(path)
    await conn1.close()

    conn2 = await db.connect(path)
    try:
        cursor = await conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='logs'")
        row = await cursor.fetchone()
    finally:
        await conn2.close()

    assert row is not None
