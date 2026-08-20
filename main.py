import os
import time
from contextlib import asynccontextmanager
from functools import partial
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
import httpx

import db as db_module


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=10.0))
    app.state.db = await db_module.connect()
    yield
    await app.state.db.close()
    await app.state.client.aclose()


app = FastAPI(lifespan=lifespan)
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
                       api_key_label=None):
    await db_module.insert_log(
        db,
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
    )
    return StreamingResponse(body_iterator(upstream, start, ttfb_ms, on_complete), status_code=upstream.status_code,
                             headers=response_headers,
                             media_type=upstream.headers.get("content-type"))
