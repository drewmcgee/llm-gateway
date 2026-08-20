import asyncio
import json

import httpx
import pytest

from logclient import (LogClient, build_record, decode_body, encode_body,
                       redact_headers)


class FakeResponse:
    def __init__(self, log_id=1, error=None):
        self._log_id = log_id
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return {"id": self._log_id}


class FakeHTTPClient:
    """Records posts instead of making them."""

    def __init__(self, response=None, raises=None):
        self.posts = []
        self._response = response or FakeResponse()
        self._raises = raises

    async def post(self, url, json=None):
        self.posts.append((url, json))
        if self._raises:
            raise self._raises
        return self._response


def sample_record(**overrides):
    fields = dict(
        created_at="2026-08-19T12:00:00+00:00",
        method="POST",
        url="http://localhost:8000/v1/chat/completions",
        status_code=200,
        ttfb_ms=12.5,
        total_ms=345.0,
        request_headers={"content-type": "application/json"},
        request_body=b'{"model": "gpt-5-mini"}',
        response_headers={"content-type": "application/json"},
        response_body=b'{"id": "chatcmpl-1"}',
        source="upstream",
    )
    fields.update(overrides)
    return build_record(**fields)


def test_redact_headers_redacts_authorization():
    result = redact_headers({"Authorization": "Bearer sk-real-openai-key"})
    assert result["Authorization"] == "REDACTED"


def test_redact_headers_redacts_x_api_key_case_insensitive():
    result = redact_headers({"x-api-key": "gw_super-secret"})
    assert result["x-api-key"] == "REDACTED"


def test_redact_headers_leaves_other_headers_untouched():
    result = redact_headers({"content-type": "application/json"})
    assert result["content-type"] == "application/json"


def test_encode_body_round_trips_utf8():
    assert decode_body(encode_body(b'{"model": "gpt-5-mini"}')) == b'{"model": "gpt-5-mini"}'


def test_encode_body_round_trips_non_utf8_bytes():
    # Audio and image endpoints return bytes that aren't decodable text; the
    # wire format has to survive them without mangling or raising.
    raw = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
    assert decode_body(encode_body(raw)) == raw


def test_encode_body_preserves_none():
    assert encode_body(None) is None
    assert decode_body(None) is None


def test_build_record_is_json_serializable():
    # Raw bytes bodies would blow up json.dumps -- this is what base64 buys.
    json.dumps(sample_record())


def test_build_record_redacts_request_credentials():
    record = sample_record(
        request_headers={"x-api-key": "gw_secret", "content-type": "application/json"})
    assert record["request_headers"]["x-api-key"] == "REDACTED"
    assert record["request_headers"]["content-type"] == "application/json"


def test_build_record_defaults_optional_fields_to_none():
    record = sample_record()
    assert record["api_key_label"] is None
    assert record["model"] is None
    assert record["total_tokens"] is None


@pytest.mark.asyncio
async def test_post_sends_the_record_to_the_backend_logs_endpoint():
    http = FakeHTTPClient()
    client = LogClient("http://localhost:8001", client=http)
    record = sample_record()

    log_id = await client.post(record)

    assert http.posts == [("http://localhost:8001/logs", record)]
    assert log_id == 1


@pytest.mark.asyncio
async def test_post_swallows_connection_errors():
    # Backend down: the proxy must keep serving requests regardless.
    http = FakeHTTPClient(raises=httpx.ConnectError("connection refused"))
    client = LogClient(client=http)

    assert await client.post(sample_record()) is None


@pytest.mark.asyncio
async def test_post_swallows_backend_error_responses():
    http = FakeHTTPClient(response=FakeResponse(
        error=httpx.HTTPStatusError("500", request=None, response=None)))
    client = LogClient(client=http)

    assert await client.post(sample_record()) is None


@pytest.mark.asyncio
async def test_send_returns_before_the_backend_has_been_called():
    gate = asyncio.Event()

    class BlockingClient(FakeHTTPClient):
        async def post(self, url, json=None):
            self.posts.append((url, json))
            await gate.wait()
            return FakeResponse()

    http = BlockingClient()
    client = LogClient(client=http)

    task = client.send(sample_record())

    assert http.posts == []  # send() didn't even start the POST, let alone await it
    await asyncio.sleep(0)  # let the task start
    assert not task.done()  # it is now blocked on the backend, and we are not
    gate.set()
    await task


@pytest.mark.asyncio
async def test_send_does_not_raise_when_the_backend_is_down():
    http = FakeHTTPClient(raises=httpx.ConnectError("connection refused"))
    client = LogClient(client=http)

    await client.send(sample_record())  # must not propagate


@pytest.mark.asyncio
async def test_pending_tasks_are_referenced_until_they_finish():
    # asyncio only weakly references running tasks; an unreferenced one can be
    # collected mid-flight and silently drop the log.
    gate = asyncio.Event()

    class BlockingClient(FakeHTTPClient):
        async def post(self, url, json=None):
            await gate.wait()
            return FakeResponse()

    client = LogClient(client=BlockingClient())
    task = client.send(sample_record())
    await asyncio.sleep(0)

    assert client._pending == {task}
    gate.set()
    await task
    assert client._pending == set()


@pytest.mark.asyncio
async def test_drain_waits_for_in_flight_posts():
    finished = []

    class SlowClient(FakeHTTPClient):
        async def post(self, url, json=None):
            await asyncio.sleep(0.01)
            finished.append(url)
            return FakeResponse()

    client = LogClient(client=SlowClient())
    client.send(sample_record())
    client.send(sample_record())

    await client.drain()

    assert len(finished) == 2


@pytest.mark.asyncio
async def test_drain_with_nothing_pending_returns_immediately():
    client = LogClient(client=FakeHTTPClient())
    await client.drain()  # must not hang or raise


@pytest.mark.asyncio
async def test_a_dropped_record_is_reported(capsys):
    # A silent drop is the dangerous failure: logs vanish with no signal.
    http = FakeHTTPClient(raises=httpx.ConnectError("connection refused"))
    client = LogClient(client=http)

    await client.post(sample_record())

    assert "dropped record" in capsys.readouterr().out
