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
