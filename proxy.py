"""LLM gateway proxy (port 8000).

Authenticates callers, forwards /v1/* to OpenAI while streaming the response
back untouched, and ships a log record to the logging backend afterwards.
Log shipping is fire-and-forget: nothing on the request path ever awaits it.
"""

import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import partial
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
import httpx
from dotenv import load_dotenv

import db as db_module
from logclient import DEFAULT_BACKEND_URL, LogClient, build_record

load_dotenv()

ENDPOINT = "https://api.openai.com"
BACKEND_URL = os.getenv("BACKEND_URL", DEFAULT_BACKEND_URL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=10.0))
    # Keys are the proxy's own state: authenticating a request must not depend
    # on the logging backend being up, and must not cost a network hop.
    app.state.keys_db = await db_module.connect_keys()
    app.state.log_client = LogClient(BACKEND_URL)
    yield
    await app.state.log_client.aclose()
    await app.state.keys_db.close()
    await app.state.client.aclose()


app = FastAPI(lifespan=lifespan)

# "authorization" matters here: the caller now presents the gateway key in that
# header, and Starlette gives us lowercase names, so leaving it in would send
# BOTH the caller's key and ours upstream (httpx keeps both -- they differ only
# by case in the dict we build).
STRIP_REQ = {"host", "content-length", "connection", "transfer-encoding",
             "keep-alive", "upgrade", "accept-encoding", "x-api-key",
             "authorization"}
STRIP_RESP = {"content-length", "connection", "transfer-encoding",
              "keep-alive", "upgrade", "content-encoding"}

# Every method the spec asks us to intercept. GET/DELETE reach the management
# endpoints (models, files, batches); PUT/PATCH and the rest are forwarded so
# the gateway stays transparent as the upstream API grows.
PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def upstream_url(path, query):
    # GET endpoints carry their arguments in the query string, so dropping it
    # would silently change the request (e.g. GET /v1/files?purpose=assistants).
    url = f"{ENDPOINT}/v1/{path}"
    return f"{url}?{query}" if query else url


async def log_request(status_code, body, ttfb_ms, total_ms):
    print(f"[LOG] {status_code} ttfb={ttfb_ms:.0f}ms total={total_ms:.0f}ms",
          flush=True)


def extract_model(request_body):
    if not request_body:
        return None
    try:
        return json.loads(request_body).get("model")
    except ValueError:
        return None


def _first_present(usage, *names):
    # Not `a or b`: a legitimate count of 0 must not fall through to the alias.
    for name in names:
        value = usage.get(name)
        if value is not None:
            return value
    return None


def _usage_object(document):
    if not isinstance(document, dict):
        return None
    usage = document.get("usage")
    if usage is None:
        # Responses API stream frames nest the response object one level down.
        response = document.get("response")
        if isinstance(response, dict):
            usage = response.get("usage")
    return usage if isinstance(usage, dict) else None


def _iter_sse_payloads(response_body):
    for line in response_body.split(b"\n"):
        if line.startswith(b"data:"):
            payload = line[len(b"data:"):].strip()
            if payload and payload != b"[DONE]":
                yield payload


def _find_usage(response_body):
    # Buffered responses are a single JSON document.
    try:
        return _usage_object(json.loads(response_body))
    except ValueError:
        pass
    # Streamed responses are SSE frames. Usage rides on the terminal frame
    # (`response.completed`, or the final chunk under stream_options), so scan
    # backwards and stop at the first frame that carries it.
    for payload in reversed(list(_iter_sse_payloads(response_body))):
        try:
            usage = _usage_object(json.loads(payload))
        except ValueError:
            continue
        if usage:
            return usage
    return None


def extract_usage(response_body):
    if not response_body:
        return None
    usage = _find_usage(response_body)
    if not usage:
        return None
    # Chat Completions reports prompt_tokens/completion_tokens; the Responses
    # API reports the same two counts as input_tokens/output_tokens. Embeddings
    # report a prompt count and no completion count at all.
    return {
        "prompt_tokens": _first_present(usage, "prompt_tokens", "input_tokens"),
        "completion_tokens": _first_present(usage, "completion_tokens", "output_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


async def log_gateway_response(request, status_code, reason, *,
                               total_ms=0.0, api_key_label=None):
    """Log a response the gateway authored itself -- an auth rejection or an
    upstream failure. source="gateway" tells the dashboard the request never
    completed against OpenAI."""
    body = await request.body()
    request.app.state.log_client.send(build_record(
        created_at=datetime.now(timezone.utc).isoformat(),
        method=request.method,
        url=str(request.url),
        status_code=status_code,
        ttfb_ms=0.0,
        total_ms=total_ms,
        request_headers=dict(request.headers),
        request_body=body,
        response_headers={},
        response_body=json.dumps({"detail": reason}).encode(),
        source="gateway",
        api_key_label=api_key_label,
        model=extract_model(body),
    ))


MISSING_CREDENTIALS = ("missing credentials: send X-API-Key, or "
                       "Authorization: Bearer <gateway key>")


def presented_key(x_api_key, authorization):
    """The gateway key the caller presented, from either supported header.

    `X-API-Key` is the gateway's own scheme. `Authorization: Bearer` is also
    accepted so an OpenAI SDK works against the gateway with nothing but
    base_url changed -- the credential is a gateway key either way, and it is
    stripped before the request is forwarded upstream.
    """
    if x_api_key:
        return x_api_key
    if authorization:
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() == "bearer":
            return credential.strip() or None
    return None


# Annotated form (rather than `= Header(None)`) so the Python default is a real
# None: these stay callable directly, outside FastAPI's dependency injection.
async def require_api_key(
        request: Request,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
        authorization: Annotated[str | None, Header()] = None):
    key = presented_key(x_api_key, authorization)
    if not key:
        await log_gateway_response(request, 401, MISSING_CREDENTIALS)
        raise HTTPException(status_code=401, detail=MISSING_CREDENTIALS)
    label = await db_module.get_api_key_label(
        request.app.state.keys_db, db_module.hash_key(key))
    if label is None:
        await log_gateway_response(request, 401, "invalid API key")
        raise HTTPException(status_code=401, detail="invalid API key")
    return label


async def persist_log(status_code, body, ttfb_ms, total_ms, *, log_client,
                       method, url, request_headers, request_body,
                       response_headers, api_key_label=None):
    # Deliberately awaits nothing: send() only schedules the POST, so stream
    # teardown finishes at the client's pace, not the backend's.
    model = extract_model(request_body)
    usage = extract_usage(body) or {}
    log_client.send(build_record(
        created_at=datetime.now(timezone.utc).isoformat(),
        method=method,
        url=url,
        status_code=status_code,
        ttfb_ms=ttfb_ms,
        total_ms=total_ms,
        request_headers=request_headers,
        request_body=request_body,
        response_headers=response_headers,
        response_body=body,
        source="upstream",
        api_key_label=api_key_label,
        model=model,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
    ))


async def body_iterator(upstream, start, ttfb_ms, on_complete=log_request):
    chunks = []
    try:
        async for chunk in upstream.aiter_bytes():
            chunks.append(chunk)
            yield chunk
    finally:
        await upstream.aclose()
        total_ms = (time.perf_counter() - start) * 1000
        await on_complete(upstream.status_code, b"".join(chunks), ttfb_ms, total_ms)


@app.api_route("/v1/{path:path}", methods=PROXY_METHODS)
async def proxy(path: str, request: Request, api_key_label: str = Depends(require_api_key)):
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in STRIP_REQ}
    headers["Authorization"] = f"Bearer {os.environ['OPENAI_API_KEY']}"

    client = request.app.state.client
    start = time.perf_counter()

    upstream_req = client.build_request(request.method,
                                        upstream_url(path, request.url.query),
                                        # Bodyless methods must stay bodyless:
                                        # content=b"" would add content-length: 0.
                                        content=body or None,
                                        headers=headers
                                        )

    try:
        upstream = await client.send(upstream_req, stream=True)
    except httpx.RequestError as exc:
        # The upstream never answered (unreachable, DNS failure, connect
        # timeout), so there is no response to proxy. Answer 502 and log it
        # like an auth rejection: authored by the gateway, not OpenAI.
        total_ms = (time.perf_counter() - start) * 1000
        reason = f"upstream request failed: {exc!r}"
        await log_gateway_response(request, 502, reason,
                                   total_ms=total_ms,
                                   api_key_label=api_key_label)
        raise HTTPException(status_code=502, detail=reason)
    ttfb_ms = (time.perf_counter() - start) * 1000

    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in STRIP_RESP}

    on_complete = partial(
        persist_log,
        log_client=request.app.state.log_client,
        method=request.method,
        url=str(request.url),
        request_headers=dict(request.headers),
        request_body=body,
        response_headers=dict(upstream.headers),
        api_key_label=api_key_label,
    )
    return StreamingResponse(body_iterator(upstream, start, ttfb_ms, on_complete), status_code=upstream.status_code,
                             headers=response_headers,
                             media_type=upstream.headers.get("content-type"))
