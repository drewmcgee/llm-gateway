import json

import pytest

import db


async def insert_sample_log(conn, **overrides):
    fields = dict(
        created_at="2026-08-19T12:00:00+00:00",
        method="POST",
        url="http://localhost:8000/v1/chat/completions",
        request_headers={"content-type": "application/json"},
        request_body=b'{"model": "gpt-5-mini"}',
        status_code=200,
        response_headers={"content-type": "application/json"},
        response_body=b'{"id": "chatcmpl-1"}',
        ttfb_ms=12.5,
        total_ms=345.0,
        source="upstream",
    )
    fields.update(overrides)
    return await db.insert_log(conn, **fields)


@pytest.mark.asyncio
async def test_insert_log_round_trips_all_fields(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        await insert_sample_log(conn)

        cursor = await conn.execute(
            "SELECT created_at, method, url, request_headers, request_body, "
            "status_code, response_headers, response_body, ttfb_ms, total_ms "
            "FROM logs"
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()

    (created_at, method, url, request_headers, request_body, status_code,
     response_headers, response_body, ttfb_ms, total_ms) = row

    assert created_at == "2026-08-19T12:00:00+00:00"
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
        await insert_sample_log(
            conn, method="GET", url="http://localhost:8000/v1/models",
            request_headers={}, request_body=None,
            response_headers={}, response_body=None,
            ttfb_ms=1.0, total_ms=2.0,
        )

        cursor = await conn.execute(
            "SELECT request_body, response_body FROM logs")
        row = await cursor.fetchone()
    finally:
        await conn.close()

    assert row == (None, None)


@pytest.mark.asyncio
async def test_insert_log_returns_new_row_id(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        first_id = await insert_sample_log(conn)
        second_id = await insert_sample_log(conn)
    finally:
        await conn.close()

    assert second_id == first_id + 1


@pytest.mark.asyncio
async def test_list_logs_returns_most_recent_first(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        await insert_sample_log(conn, url="http://x/v1/first")
        await insert_sample_log(conn, url="http://x/v1/second")
        rows = await db.list_logs(conn)
    finally:
        await conn.close()

    assert [row["url"] for row in rows] == [
        "http://x/v1/second", "http://x/v1/first"]


@pytest.mark.asyncio
async def test_list_logs_filters_by_method(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        await insert_sample_log(conn, method="POST", url="http://x/v1/a")
        await insert_sample_log(conn, method="GET", url="http://x/v1/b")
        rows = await db.list_logs(conn, method="GET")
    finally:
        await conn.close()

    assert [row["url"] for row in rows] == ["http://x/v1/b"]


@pytest.mark.asyncio
async def test_list_logs_filters_by_status_code(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        await insert_sample_log(conn, status_code=200, url="http://x/v1/ok")
        await insert_sample_log(conn, status_code=500, url="http://x/v1/err")
        rows = await db.list_logs(conn, status_code=500)
    finally:
        await conn.close()

    assert [row["url"] for row in rows] == ["http://x/v1/err"]


@pytest.mark.asyncio
async def test_list_logs_filters_by_url_substring(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        await insert_sample_log(conn, url="http://x/v1/chat/completions")
        await insert_sample_log(conn, url="http://x/v1/embeddings")
        rows = await db.list_logs(conn, url_contains="chat")
    finally:
        await conn.close()

    assert [row["url"] for row in rows] == ["http://x/v1/chat/completions"]


@pytest.mark.asyncio
async def test_list_logs_before_id_returns_older_rows_only(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        id1 = await insert_sample_log(conn, url="http://x/v1/a")
        id2 = await insert_sample_log(conn, url="http://x/v1/b")
        id3 = await insert_sample_log(conn, url="http://x/v1/c")
        page = await db.list_logs(conn, before_id=id3)
    finally:
        await conn.close()

    assert [row["id"] for row in page] == [id2, id1]


@pytest.mark.asyncio
async def test_list_logs_before_id_combines_with_filters(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        await insert_sample_log(conn, method="GET", url="http://x/v1/a")
        id2 = await insert_sample_log(conn, method="POST", url="http://x/v1/b")
        await insert_sample_log(conn, method="POST", url="http://x/v1/c")
        page = await db.list_logs(conn, method="POST", before_id=id2 + 1)
    finally:
        await conn.close()

    assert [row["url"] for row in page] == ["http://x/v1/b"]


@pytest.mark.asyncio
async def test_list_logs_omits_headers_and_bodies(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        await insert_sample_log(conn)
        rows = await db.list_logs(conn)
    finally:
        await conn.close()

    assert "request_body" not in rows[0]
    assert "response_headers" not in rows[0]


@pytest.mark.asyncio
async def test_get_log_returns_full_detail(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        log_id = await insert_sample_log(conn)
        row = await db.get_log(conn, log_id)
    finally:
        await conn.close()

    assert row["id"] == log_id
    assert row["request_headers"] == {"content-type": "application/json"}
    assert row["request_body"] == '{"model": "gpt-5-mini"}'
    assert row["response_body"] == '{"id": "chatcmpl-1"}'


@pytest.mark.asyncio
async def test_list_logs_includes_model_and_token_fields(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        await insert_sample_log(
            conn, model="gpt-5-mini", prompt_tokens=10,
            completion_tokens=20, total_tokens=30)
        rows = await db.list_logs(conn)
    finally:
        await conn.close()

    assert rows[0]["model"] == "gpt-5-mini"
    assert rows[0]["prompt_tokens"] == 10
    assert rows[0]["completion_tokens"] == 20
    assert rows[0]["total_tokens"] == 30


@pytest.mark.asyncio
async def test_get_log_includes_model_and_token_fields(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        log_id = await insert_sample_log(
            conn, model="gpt-5-mini", prompt_tokens=10,
            completion_tokens=20, total_tokens=30)
        row = await db.get_log(conn, log_id)
    finally:
        await conn.close()

    assert row["model"] == "gpt-5-mini"
    assert row["prompt_tokens"] == 10
    assert row["completion_tokens"] == 20
    assert row["total_tokens"] == 30


@pytest.mark.asyncio
async def test_model_and_tokens_default_to_none(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        log_id = await insert_sample_log(conn)
        row = await db.get_log(conn, log_id)
    finally:
        await conn.close()

    assert row["model"] is None
    assert row["total_tokens"] is None


@pytest.mark.asyncio
async def test_list_logs_includes_source(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        await insert_sample_log(conn, source="gateway")
        rows = await db.list_logs(conn)
    finally:
        await conn.close()

    assert rows[0]["source"] == "gateway"


@pytest.mark.asyncio
async def test_get_log_includes_source(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        log_id = await insert_sample_log(conn, source="gateway")
        row = await db.get_log(conn, log_id)
    finally:
        await conn.close()

    assert row["source"] == "gateway"


@pytest.mark.asyncio
async def test_insert_log_rejects_unrecognized_source(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        with pytest.raises(Exception):
            await insert_sample_log(conn, source="something-else")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_insert_log_requires_source(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        with pytest.raises(TypeError):
            fields = dict(
                created_at="2026-08-19T12:00:00+00:00", method="POST",
                url="http://x/v1/y", request_headers={}, request_body=None,
                status_code=200, response_headers={}, response_body=None,
                ttfb_ms=1.0, total_ms=2.0,
            )
            await db.insert_log(conn, **fields)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_get_log_returns_none_for_unknown_id(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        row = await db.get_log(conn, 999)
    finally:
        await conn.close()

    assert row is None


def test_hash_key_is_deterministic():
    assert db.hash_key("gw_abc123") == db.hash_key("gw_abc123")


def test_hash_key_differs_for_different_keys():
    assert db.hash_key("gw_abc123") != db.hash_key("gw_abc124")


def test_hash_key_does_not_return_the_raw_key():
    assert db.hash_key("gw_abc123") != "gw_abc123"


@pytest.mark.asyncio
async def test_create_api_key_returns_prefixed_raw_key(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        raw_key = await db.create_api_key(conn, "demo-user")
    finally:
        await conn.close()

    assert raw_key.startswith("gw_")


@pytest.mark.asyncio
async def test_create_api_key_stores_hash_not_plaintext(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        raw_key = await db.create_api_key(conn, "demo-user")
        cursor = await conn.execute("SELECT key_hash FROM api_keys")
        row = await cursor.fetchone()
    finally:
        await conn.close()

    assert row[0] != raw_key
    assert row[0] == db.hash_key(raw_key)


@pytest.mark.asyncio
async def test_get_api_key_label_returns_label_for_known_key(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        raw_key = await db.create_api_key(conn, "demo-user")
        label = await db.get_api_key_label(conn, db.hash_key(raw_key))
    finally:
        await conn.close()

    assert label == "demo-user"


@pytest.mark.asyncio
async def test_get_api_key_label_returns_none_for_unknown_key(tmp_path):
    conn = await db.connect(tmp_path / "logs.db")
    try:
        label = await db.get_api_key_label(conn, db.hash_key("gw_never-issued"))
    finally:
        await conn.close()

    assert label is None


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
