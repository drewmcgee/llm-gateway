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

STRIP_REQ = {"host", "content-length", "connection", "transfer-encoding",
             "keep-alive", "upgrade", "accept-encoding", "x-api-key"}
STRIP_RESP = {"content-length", "connection", "transfer-encoding",
              "keep-alive", "upgrade", "content-encoding"}


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


def extract_usage(response_body):
    if not response_body:
        return None
    try:
        usage = json.loads(response_body).get("usage")
    except ValueError:
        return None
    if not usage:
        return None
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


async def log_rejected_request(request, reason):
    body = await request.body()
    request.app.state.log_client.send(build_record(
        created_at=datetime.now(timezone.utc).isoformat(),
        method=request.method,
        url=str(request.url),
        status_code=401,
        ttfb_ms=0.0,
        total_ms=0.0,
        request_headers=dict(request.headers),
        request_body=body,
        response_headers={},
        response_body=json.dumps({"detail": reason}).encode(),
        source="gateway",
        model=extract_model(body),
    ))


async def require_api_key(request: Request, x_api_key: str | None = Header(None, alias="X-API-Key")):
    if not x_api_key:
        await log_rejected_request(request, "missing X-API-Key header")
        raise HTTPException(status_code=401, detail="missing X-API-Key header")
    label = await db_module.get_api_key_label(
        request.app.state.keys_db, db_module.hash_key(x_api_key))
    if label is None:
        await log_rejected_request(request, "invalid API key")
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


@app.post("/v1/{path:path}")
async def proxy(path: str, request: Request, api_key_label: str = Depends(require_api_key)):
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in STRIP_REQ}
    headers["Authorization"] = f"Bearer {os.environ['OPENAI_API_KEY']}"

    client = request.app.state.client
    start = time.perf_counter()

    upstream_req = client.build_request("POST",
                                        f"{ENDPOINT}/v1/{path}",
                                        content=body,
                                        headers=headers
                                        )

    upstream = await client.send(upstream_req, stream=True)
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
