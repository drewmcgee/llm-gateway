import time

import pytest

from main import body_iterator


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
