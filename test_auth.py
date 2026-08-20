from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import db
from proxy import require_api_key


class FakeLogClient:
    """Stands in for LogClient -- records what the proxy handed the backend."""

    def __init__(self):
        self.sent = []

    def send(self, record):
        self.sent.append(record)


def fake_request(conn, log_client=None, method="POST",
                  url="http://localhost:8000/v1/chat/completions",
                  headers=None, body=b""):
    async def _body():
        return body

    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            keys_db=conn, log_client=log_client or FakeLogClient())),
        method=method,
        url=url,
        headers=headers or {},
        body=_body,
    )


@pytest.mark.asyncio
async def test_require_api_key_rejects_missing_header(tmp_path):
    conn = await db.connect_keys(tmp_path / "keys.db")
    try:
        with pytest.raises(HTTPException) as exc_info:
            await require_api_key(fake_request(conn), x_api_key=None)
    finally:
        await conn.close()

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_api_key_rejects_unknown_key(tmp_path):
    conn = await db.connect_keys(tmp_path / "keys.db")
    try:
        with pytest.raises(HTTPException) as exc_info:
            await require_api_key(fake_request(conn), x_api_key="gw_never-issued")
    finally:
        await conn.close()

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_api_key_accepts_valid_key_and_returns_label(tmp_path):
    conn = await db.connect_keys(tmp_path / "keys.db")
    try:
        raw_key = await db.create_api_key(conn, "demo-user")
        label = await require_api_key(fake_request(conn), x_api_key=raw_key)
    finally:
        await conn.close()

    assert label == "demo-user"


@pytest.mark.asyncio
async def test_rejected_request_with_missing_key_is_shipped_to_backend(tmp_path):
    conn = await db.connect_keys(tmp_path / "keys.db")
    log_client = FakeLogClient()
    try:
        with pytest.raises(HTTPException):
            await require_api_key(fake_request(conn, log_client), x_api_key=None)
    finally:
        await conn.close()

    record = log_client.sent[0]
    assert (record["status_code"], record["api_key_label"], record["model"]) == (
        401, None, None)
    assert record["source"] == "gateway"


@pytest.mark.asyncio
async def test_rejected_request_with_invalid_key_is_shipped_to_backend(tmp_path):
    conn = await db.connect_keys(tmp_path / "keys.db")
    log_client = FakeLogClient()
    try:
        with pytest.raises(HTTPException):
            await require_api_key(
                fake_request(conn, log_client), x_api_key="gw_never-issued")
    finally:
        await conn.close()

    record = log_client.sent[0]
    assert (record["status_code"], record["api_key_label"], record["model"]) == (
        401, None, None)


@pytest.mark.asyncio
async def test_rejected_request_captures_attempted_model(tmp_path):
    conn = await db.connect_keys(tmp_path / "keys.db")
    log_client = FakeLogClient()
    try:
        body = b'{"model": "gpt-4", "messages": []}'
        with pytest.raises(HTTPException):
            await require_api_key(
                fake_request(conn, log_client, body=body), x_api_key=None)
    finally:
        await conn.close()

    assert log_client.sent[0]["model"] == "gpt-4"


@pytest.mark.asyncio
async def test_rejected_request_does_not_ship_the_attempted_key(tmp_path):
    conn = await db.connect_keys(tmp_path / "keys.db")
    log_client = FakeLogClient()
    try:
        headers = {"x-api-key": "gw_never-issued",
                   "authorization": "Bearer sk-real-openai-key"}
        with pytest.raises(HTTPException):
            await require_api_key(
                fake_request(conn, log_client, headers=headers),
                x_api_key="gw_never-issued")
    finally:
        await conn.close()

    shipped = log_client.sent[0]["request_headers"]
    assert shipped == {"x-api-key": "REDACTED", "authorization": "REDACTED"}


@pytest.mark.asyncio
async def test_valid_key_does_not_log_anything_itself(tmp_path):
    # require_api_key only logs on rejection -- a successful lookup just
    # returns the label; persist_log (called later, after the upstream
    # response) is what logs the eventual outcome of the proxied request.
    conn = await db.connect_keys(tmp_path / "keys.db")
    log_client = FakeLogClient()
    try:
        raw_key = await db.create_api_key(conn, "demo-user")
        await require_api_key(fake_request(conn, log_client), x_api_key=raw_key)
    finally:
        await conn.close()

    assert log_client.sent == []
