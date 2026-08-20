import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import partial
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import httpx

import db as db_module
from broadcast import Broadcaster


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=10.0))
    app.state.db = await db_module.connect()
    app.state.broadcaster = Broadcaster()
    yield
    await app.state.db.close()
    await app.state.client.aclose()


app = FastAPI(lifespan=lifespan)
# Dashboard is a separate dev-server origin; this is a local inspection tool
# with no cookies/credentials in play, so a permissive policy is fine here.
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                    allow_methods=["*"], allow_headers=["*"])
ENDPOINT = "https://api.openai.com"

STRIP_REQ = {"host", "content-length", "connection", "transfer-encoding",
             "keep-alive", "upgrade", "accept-encoding", "x-api-key"}
STRIP_RESP = {"content-length", "connection", "transfer-encoding",
              "keep-alive", "upgrade", "content-encoding"}


async def log_request(status_code, body, ttfb_ms, total_ms):
    print(f"[LOG] {status_code} ttfb={ttfb_ms:.0f}ms total={total_ms:.0f}ms")


REDACT_HEADERS = {"authorization", "x-api-key"}


def redact_headers(headers):
    return {k: ("REDACTED" if k.lower() in REDACT_HEADERS else v)
            for k, v in headers.items()}


async def require_api_key(request: Request, x_api_key: str | None = Header(None, alias="X-API-Key")):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="missing X-API-Key header")
    label = await db_module.get_api_key_label(
        request.app.state.db, db_module.hash_key(x_api_key))
    if label is None:
        raise HTTPException(status_code=401, detail="invalid API key")
    return label


async def persist_log(status_code, body, ttfb_ms, total_ms, *, db, method,
                       url, request_headers, request_body, response_headers,
                       api_key_label=None, broadcaster=None):
    created_at = datetime.now(timezone.utc).isoformat()
    log_id = await db_module.insert_log(
        db,
        created_at=created_at,
        method=method,
        url=url,
        request_headers=redact_headers(request_headers),
        request_body=request_body,
        status_code=status_code,
        response_headers=response_headers,
        response_body=body,
        ttfb_ms=ttfb_ms,
        total_ms=total_ms,
        api_key_label=api_key_label,
    )
    if broadcaster is not None:
        broadcaster.publish({
            "id": log_id,
            "created_at": created_at,
            "method": method,
            "url": url,
            "status_code": status_code,
            "ttfb_ms": ttfb_ms,
            "total_ms": total_ms,
            "api_key_label": api_key_label,
        })


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
        db=request.app.state.db,
        method=request.method,
        url=str(request.url),
        request_headers=dict(request.headers),
        request_body=body,
        response_headers=dict(upstream.headers),
        api_key_label=api_key_label,
        broadcaster=request.app.state.broadcaster,
    )
    return StreamingResponse(body_iterator(upstream, start, ttfb_ms, on_complete), status_code=upstream.status_code,
                             headers=response_headers,
                             media_type=upstream.headers.get("content-type"))


@app.get("/logs")
async def list_logs(request: Request, method: str | None = None,
                     status_code: int | None = None, url_contains: str | None = None,
                     limit: int = 200):
    return await db_module.list_logs(
        request.app.state.db, method=method, status_code=status_code,
        url_contains=url_contains, limit=limit)


def format_sse_event(data):
    return f"data: {json.dumps(data)}\n\n"


@app.get("/logs/stream")
async def stream_logs(request: Request):
    queue = request.app.state.broadcaster.subscribe()

    async def event_source():
        try:
            while True:
                event = await queue.get()
                yield format_sse_event(event)
        finally:
            request.app.state.broadcaster.unsubscribe(queue)

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.get("/logs/{log_id}")
async def get_log(log_id: int, request: Request):
    row = await db_module.get_log(request.app.state.db, log_id)
    if row is None:
        raise HTTPException(status_code=404, detail="log not found")
    return row
