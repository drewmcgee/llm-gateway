import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import httpx


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=10.0))
    yield
    await app.state.client.aclose()


app = FastAPI(lifespan=lifespan)
ENDPOINT = "https://api.openai.com"

STRIP_REQ = {"host", "content-length", "connection", "transfer-encoding",
             "keep-alive", "upgrade", "accept-encoding"}
STRIP_RESP = {"content-length", "connection", "transfer-encoding",
              "keep-alive", "upgrade", "content-encoding"}


def log_request(status_code, body, ttfb_ms, total_ms):
    print(f"[LOG] {status_code} ttfb={ttfb_ms:.0f}ms total={total_ms:.0f}ms")


async def body_iterator(upstream, start, ttfb_ms, on_complete=log_request):
    chunks = []
    try:
        async for chunk in upstream.aiter_bytes():
            chunks.append(chunk)
            yield chunk
    finally:
        await upstream.aclose()
        total_ms = (time.perf_counter() - start) * 1000
        on_complete(upstream.status_code, b"".join(chunks), ttfb_ms, total_ms)


@app.post("/v1/{path:path}")
async def proxy(path: str, request: Request):
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
    return StreamingResponse(body_iterator(upstream, start, ttfb_ms), status_code=upstream.status_code,
                             headers=response_headers,
                             media_type=upstream.headers.get("content-type"))
