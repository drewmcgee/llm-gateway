import pytest
from pydantic import ValidationError

import db
from backend import LogRecord, store_log
from broadcast import Broadcaster
from logclient import build_record


def wire_record(**overrides):
    """A record exactly as the proxy would put it on the wire."""
    fields = dict(
        created_at="2026-08-19T12:00:00+00:00",
        method="POST",
        url="http://localhost:8000/v1/chat/completions",
        status_code=200,
        ttfb_ms=12.5,
        total_ms=345.0,
        request_headers={"content-type": "application/json",
                         "x-api-key": "gw_secret"},
        request_body=b'{"model": "gpt-5-mini"}',
        response_headers={"content-type": "application/json"},
        response_body=b'{"id": "chatcmpl-1", "usage": {"total_tokens": 30}}',
        source="upstream",
    )
    fields.update(overrides)
    return LogRecord(**build_record(**fields))


@pytest.mark.asyncio
async def test_store_log_round_trips_a_posted_record(tmp_path):
    conn = await db.connect_logs(tmp_path / "logs.db")
    try:
        log_id = await store_log(conn, None, wire_record())
        row = await db.get_log(conn, log_id)
    finally:
        await conn.close()

    assert row["method"] == "POST"
    assert row["status_code"] == 200
    assert row["request_body"] == '{"model": "gpt-5-mini"}'
    assert row["response_body"].startswith('{"id": "chatcmpl-1"')
    assert row["ttfb_ms"] == 12.5
    assert row["source"] == "upstream"


@pytest.mark.asyncio
async def test_store_log_preserves_non_utf8_response_bodies(tmp_path):
    raw = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
    conn = await db.connect_logs(tmp_path / "logs.db")
    try:
        log_id = await store_log(conn, None, wire_record(response_body=raw))
        cursor = await conn.execute(
            "SELECT response_body FROM logs WHERE id = ?", (log_id,))
        stored = (await cursor.fetchone())[0]
    finally:
        await conn.close()

    assert stored == raw


@pytest.mark.asyncio
async def test_store_log_persists_redacted_headers_as_sent(tmp_path):
    conn = await db.connect_logs(tmp_path / "logs.db")
    try:
        log_id = await store_log(conn, None, wire_record())
        row = await db.get_log(conn, log_id)
    finally:
        await conn.close()

    assert row["request_headers"]["x-api-key"] == "REDACTED"


@pytest.mark.asyncio
async def test_store_log_publishes_summary_to_subscribers(tmp_path):
    conn = await db.connect_logs(tmp_path / "logs.db")
    broadcaster = Broadcaster()
    queue = broadcaster.subscribe()
    try:
        log_id = await store_log(
            conn, broadcaster, wire_record(model="gpt-5-mini", total_tokens=30))
    finally:
        await conn.close()

    event = queue.get_nowait()
    assert event["id"] == log_id
    assert event["status_code"] == 200
    assert event["model"] == "gpt-5-mini"
    assert event["total_tokens"] == 30


@pytest.mark.asyncio
async def test_broadcast_event_omits_headers_and_bodies(tmp_path):
    # The live stream is a table feed; detail stays behind GET /logs/{id}.
    conn = await db.connect_logs(tmp_path / "logs.db")
    broadcaster = Broadcaster()
    queue = broadcaster.subscribe()
    try:
        await store_log(conn, broadcaster, wire_record())
    finally:
        await conn.close()

    event = queue.get_nowait()
    assert "request_body" not in event
    assert "response_headers" not in event


def test_log_record_rejects_unrecognized_source():
    with pytest.raises(ValidationError):
        wire_record(source="somewhere-else")


def test_log_record_rejects_a_record_missing_required_fields():
    with pytest.raises(ValidationError):
        LogRecord(method="POST", url="http://localhost:8000/v1/chat/completions")


def test_log_record_defaults_bodies_and_headers_when_absent():
    record = LogRecord(created_at="2026-08-19T12:00:00+00:00", method="POST",
                       url="http://localhost:8000/v1/chat/completions",
                       status_code=401, ttfb_ms=0.0, total_ms=0.0,
                       source="gateway")
    assert record.request_headers == {}
    assert record.request_body is None
    assert record.api_key_label is None
