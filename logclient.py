"""Log shipping from the proxy to the logging backend.

The proxy and the backend are separate processes, so a log record has to
survive a JSON round trip. Request/response bodies are raw bytes (and may not
be valid UTF-8 -- think /v1/audio/speech), so they travel base64-encoded and
are decoded back to bytes before the backend writes them to the BLOB columns.
"""

import asyncio
import base64

import httpx

DEFAULT_BACKEND_URL = "http://localhost:8001"
# Short relative to the 300s upstream timeout: a log post is best-effort
# bookkeeping, and a wedged backend shouldn't leave tasks pending for minutes.
POST_TIMEOUT_S = 5.0

REDACT_HEADERS = {"authorization", "x-api-key"}


def redact_headers(headers):
    return {k: ("REDACTED" if k.lower() in REDACT_HEADERS else v)
            for k, v in headers.items()}


def encode_body(body):
    if body is None:
        return None
    return base64.b64encode(body).decode("ascii")


def decode_body(encoded):
    if encoded is None:
        return None
    return base64.b64decode(encoded)


def build_record(*, created_at, method, url, status_code, ttfb_ms, total_ms,
                 request_headers, request_body, response_headers, response_body,
                 source, api_key_label=None, model=None, prompt_tokens=None,
                 completion_tokens=None, total_tokens=None):
    # Redaction happens here rather than at the backend so credentials never
    # cross the wire in the first place.
    return {
        "created_at": created_at,
        "method": method,
        "url": url,
        "status_code": status_code,
        "ttfb_ms": ttfb_ms,
        "total_ms": total_ms,
        "request_headers": redact_headers(request_headers),
        "request_body": encode_body(request_body),
        "response_headers": dict(response_headers),
        "response_body": encode_body(response_body),
        "source": source,
        "api_key_label": api_key_label,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


class LogClient:
    """POSTs log records to the backend without ever blocking the hot path."""

    def __init__(self, base_url=DEFAULT_BACKEND_URL, client=None,
                 timeout=POST_TIMEOUT_S):
        self._url = f"{base_url.rstrip('/')}/logs"
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._pending = set()

    def send(self, record):
        """Schedule a log post and return immediately.

        This is the "asynchronously" in the spec, and the reason a slow or dead
        backend can't stall a client's response: the caller never awaits the
        network round trip, and post() swallows every failure.
        """
        task = asyncio.create_task(self.post(record))
        # asyncio only holds a weak reference to running tasks, so a task with
        # no other referent can be garbage collected mid-flight.
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return task

    async def post(self, record):
        try:
            response = await self._client.post(self._url, json=record)
            response.raise_for_status()
            return response.json().get("id")
        except Exception as exc:
            # Losing a log line is strictly better than failing the request it
            # describes -- gateway availability must not depend on the logger.
            # flush: stdout is block-buffered when redirected to a file, and a
            # dropped-log warning nobody sees is worse than useless.
            print(f"[log] dropped record for {record.get('url')}: {exc!r}",
                  flush=True)
            return None

    async def drain(self):
        """Wait for in-flight posts, so shutdown doesn't discard fresh logs."""
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)

    async def aclose(self):
        await self.drain()
        if self._owns_client:
            await self._client.aclose()
