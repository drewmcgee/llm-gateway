import asyncio
import time
from functools import partial
from types import SimpleNamespace

import httpx
import pytest

from logclient import LogClient
from proxy import body_iterator, persist_log


class FakeUpstream:
    def __init__(self, chunks, status_code=200):
        self._chunks = chunks
        self.status_code = status_code
        self.closed = False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_yields_each_chunk_unchanged():
    upstream = FakeUpstream([b"hello ", b"world"])

    yielded = [chunk async for chunk in body_iterator(upstream, time.perf_counter(), 0)]

    assert yielded == [b"hello ", b"world"]


@pytest.mark.asyncio
async def test_aggregates_chunks_into_full_body_on_complete():
    upstream = FakeUpstream([b'{"foo": ', b'"bar"}'], status_code=200)
    captured = {}

    async def on_complete(status_code, body, ttfb_ms, total_ms):
        captured["status_code"] = status_code
        captured["body"] = body

    async for _ in body_iterator(upstream, time.perf_counter(), 0, on_complete):
        pass

    assert captured["status_code"] == 200
    assert captured["body"] == b'{"foo": "bar"}'


@pytest.mark.asyncio
async def test_empty_stream_aggregates_to_empty_body():
    upstream = FakeUpstream([])
    captured = {}

    async def on_complete(status_code, body, ttfb_ms, total_ms):
        captured["body"] = body

    async for _ in body_iterator(upstream, time.perf_counter(), 0, on_complete):
        pass

    assert captured["body"] == b""


@pytest.mark.asyncio
async def test_closes_upstream_even_if_consumer_stops_early():
    upstream = FakeUpstream([b"a", b"b", b"c"])

    gen = body_iterator(upstream, time.perf_counter(), 0)
    await gen.__anext__()
    await gen.aclose()

    assert upstream.closed


@pytest.mark.asyncio
async def test_on_complete_called_even_when_upstream_iteration_raises():
    class BoomUpstream(FakeUpstream):
        async def aiter_bytes(self):
            yield b"partial"
            raise ConnectionError("boom")

    upstream = BoomUpstream([])
    captured = {}

    async def on_complete(status_code, body, ttfb_ms, total_ms):
        captured["body"] = body

    with pytest.raises(ConnectionError):
        async for _ in body_iterator(upstream, time.perf_counter(), 0, on_complete):
            pass

    assert captured["body"] == b"partial"
    assert upstream.closed


class BlockingHTTPClient:
    """A logging backend that accepts the POST but never answers."""

    def __init__(self, gate):
        self._gate = gate
        self.posts = []

    async def post(self, url, json=None):
        self.posts.append(json)
        await self._gate.wait()
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"id": 1})


def logging_on_complete(log_client):
    return partial(
        persist_log,
        log_client=log_client,
        method="POST",
        url="http://localhost:8000/v1/chat/completions",
        request_headers={"x-api-key": "gw_secret"},
        request_body=b'{"model": "gpt-5-mini"}',
        response_headers={"content-type": "application/json"},
        api_key_label="demo-user",
    )


@pytest.mark.asyncio
async def test_stream_finishes_while_the_log_post_is_still_in_flight():
    # The whole point of the split: the client's stream tears down at its own
    # pace, not at the logging backend's.
    gate = asyncio.Event()
    http = BlockingHTTPClient(gate)
    log_client = LogClient(client=http)
    upstream = FakeUpstream([b'{"usage": ', b'{"total_tokens": 30}}'])

    async for _ in body_iterator(upstream, time.perf_counter(), 0.0,
                                 logging_on_complete(log_client)):
        pass

    assert upstream.closed  # stream is done
    await asyncio.sleep(0)  # let the log task reach the network call
    assert len(log_client._pending) == 1  # ...which is still hanging

    gate.set()
    await log_client.drain()

    record = http.posts[0]
    assert record["total_tokens"] == 30
    assert record["api_key_label"] == "demo-user"
    assert record["request_headers"]["x-api-key"] == "REDACTED"


@pytest.mark.asyncio
async def test_stream_is_unaffected_when_the_logging_backend_is_down():
    class DeadBackend:
        async def post(self, url, json=None):
            raise httpx.ConnectError("connection refused")

    log_client = LogClient(client=DeadBackend())
    upstream = FakeUpstream([b"hello ", b"world"])

    yielded = [chunk async for chunk in body_iterator(
        upstream, time.perf_counter(), 0.0, logging_on_complete(log_client))]

    assert yielded == [b"hello ", b"world"]
    await log_client.drain()  # the failure stays inside the log task


# --- method coverage: the proxy driven as a real ASGI app -------------------

import json
import os
from contextlib import asynccontextmanager

import db
from proxy import PROXY_METHODS, app as proxy_app, upstream_url

os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-real")


class RecordingLogClient:
    def __init__(self):
        self.sent = []

    def send(self, record):
        self.sent.append(record)


def stream_response(payload, status=200):
    """MockTransport response usable with client.send(..., stream=True)."""
    async def body():
        yield json.dumps(payload).encode()

    return httpx.Response(status, headers={"content-type": "application/json"},
                          content=body())


@asynccontextmanager
async def gateway(tmp_path, handler):
    """The real proxy app, a stubbed upstream, and a throwaway key store."""
    seen = []

    def recording_handler(request):
        seen.append(request)
        return handler(request)

    proxy_app.state.client = httpx.AsyncClient(
        transport=httpx.MockTransport(recording_handler))
    proxy_app.state.keys_db = await db.connect_keys(tmp_path / "keys.db")
    proxy_app.state.log_client = RecordingLogClient()
    raw_key = await db.create_api_key(proxy_app.state.keys_db, "test-key")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_app),
                                 base_url="http://gw") as client:
        yield client, raw_key, seen, proxy_app.state.log_client

    await proxy_app.state.client.aclose()
    await proxy_app.state.keys_db.close()


@pytest.mark.parametrize("method", PROXY_METHODS)
@pytest.mark.asyncio
async def test_every_supported_method_is_intercepted_and_forwarded(tmp_path, method):
    async with gateway(tmp_path, lambda r: stream_response({"ok": True})) as (
            client, raw_key, seen, log_client):
        response = await client.request(
            method, "/v1/models", headers={"X-API-Key": raw_key})

    assert response.status_code == 200
    assert seen[0].method == method          # forwarded verb, not rewritten
    assert log_client.sent[0]["method"] == method   # and logged as itself


@pytest.mark.asyncio
async def test_query_string_is_forwarded_to_upstream(tmp_path):
    async with gateway(tmp_path, lambda r: stream_response({"data": []})) as (
            client, raw_key, seen, _):
        await client.get("/v1/files?purpose=assistants&limit=5",
                         headers={"X-API-Key": raw_key})

    assert str(seen[0].url) == "https://api.openai.com/v1/files?purpose=assistants&limit=5"


@pytest.mark.asyncio
async def test_bodyless_request_is_forwarded_without_a_body(tmp_path):
    async with gateway(tmp_path, lambda r: stream_response({"ok": True})) as (
            client, raw_key, seen, _):
        await client.get("/v1/models", headers={"X-API-Key": raw_key})

    assert seen[0].headers.get("content-length") in (None, "0")
    assert seen[0].content == b""


@pytest.mark.asyncio
async def test_delete_passes_the_upstream_status_through(tmp_path):
    async with gateway(tmp_path, lambda r: stream_response({"deleted": True}, 404)) as (
            client, raw_key, seen, _):
        response = await client.delete("/v1/files/file-123",
                                       headers={"X-API-Key": raw_key})

    assert response.status_code == 404
    assert seen[0].method == "DELETE"


@pytest.mark.asyncio
async def test_get_request_body_and_response_are_logged(tmp_path):
    async with gateway(tmp_path, lambda r: stream_response({"data": ["gpt-5-mini"]})) as (
            client, raw_key, _, log_client):
        await client.get("/v1/models", headers={"X-API-Key": raw_key})

    record = log_client.sent[0]
    assert record["method"] == "GET"
    assert record["status_code"] == 200
    assert record["source"] == "upstream"
    assert record["api_key_label"] == "test-key"


@pytest.mark.asyncio
async def test_unauthenticated_get_is_rejected_and_logged(tmp_path):
    async with gateway(tmp_path, lambda r: stream_response({"ok": True})) as (
            client, _, seen, log_client):
        response = await client.get("/v1/models")

    assert response.status_code == 401
    assert seen == []                                   # never reached upstream
    assert log_client.sent[0]["method"] == "GET"
    assert log_client.sent[0]["source"] == "gateway"


def test_upstream_url_omits_the_question_mark_without_a_query():
    assert upstream_url("models", "") == "https://api.openai.com/v1/models"
    assert upstream_url("models", "limit=5") == "https://api.openai.com/v1/models?limit=5"


@pytest.mark.asyncio
async def test_sdk_style_bearer_auth_reaches_upstream_with_our_key_only(tmp_path):
    """An OpenAI SDK sends Authorization: Bearer. The gateway must authenticate
    it, then replace it -- never forward the caller's key, and never send two."""
    async with gateway(tmp_path, lambda r: stream_response({"ok": True})) as (
            client, raw_key, seen, log_client):
        response = await client.post(
            "/v1/responses",
            headers={"Authorization": f"Bearer {raw_key}",
                     "content-type": "application/json"},
            content=b'{"model":"gpt-5-mini","input":"hi"}')

    assert response.status_code == 200
    sent = seen[0].headers.get_list("authorization")
    assert len(sent) == 1                                  # exactly one, not two
    assert sent[0] == f"Bearer {os.environ['OPENAI_API_KEY']}"
    assert raw_key not in str(seen[0].headers)             # caller's key never leaves


@pytest.mark.asyncio
async def test_caller_credentials_are_never_forwarded_upstream(tmp_path):
    async with gateway(tmp_path, lambda r: stream_response({"ok": True})) as (
            client, raw_key, seen, _):
        await client.post("/v1/responses",
                          headers={"X-API-Key": raw_key,
                                   "Authorization": "Bearer sk-callers-own-openai-key",
                                   "content-type": "application/json"},
                          content=b'{"model":"gpt-5-mini","input":"hi"}')

    forwarded = str(seen[0].headers)
    assert "sk-callers-own-openai-key" not in forwarded
    assert raw_key not in forwarded
    assert seen[0].headers.get_list("authorization") == [
        f"Bearer {os.environ['OPENAI_API_KEY']}"]


@pytest.mark.asyncio
async def test_missing_credentials_message_names_both_schemes(tmp_path):
    async with gateway(tmp_path, lambda r: stream_response({"ok": True})) as (
            client, _, seen, _):
        response = await client.post("/v1/responses", content=b'{}')

    assert response.status_code == 401
    detail = response.json()["detail"]
    assert "X-API-Key" in detail and "Bearer" in detail
    assert seen == []
