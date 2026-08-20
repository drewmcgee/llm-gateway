"""Logging backend (port 8001).

Owns the log store and everything that reads from it: the dashboard's history
and detail endpoints, and the live SSE stream. The proxy never touches this
database -- it ships records here over HTTP via POST /logs.
"""

import json
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import db as db_module
from broadcast import Broadcaster
from logclient import decode_body


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await db_module.connect_logs()
    app.state.broadcaster = Broadcaster()
    yield
    await app.state.db.close()


app = FastAPI(lifespan=lifespan)
# Dashboard is a separate dev-server origin; this is a local inspection tool
# with no cookies/credentials in play, so a permissive policy is fine here.
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                    allow_methods=["*"], allow_headers=["*"])


class LogRecord(BaseModel):
    """The wire format the proxy POSTs. Bodies arrive base64-encoded."""

    created_at: str
    method: str
    url: str
    status_code: int
    ttfb_ms: float
    total_ms: float
    request_headers: dict[str, str] = {}
    request_body: str | None = None
    response_headers: dict[str, str] = {}
    response_body: str | None = None
    source: Literal["gateway", "upstream"]
    api_key_label: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


# What the dashboard's live stream needs -- headers and bodies stay behind
# GET /logs/{id}, same as the paginated history endpoint.
SUMMARY_FIELDS = {"created_at", "method", "url", "status_code", "ttfb_ms",
                  "total_ms", "source", "api_key_label", "model",
                  "prompt_tokens", "completion_tokens", "total_tokens"}


async def store_log(db, broadcaster, record: LogRecord):
    log_id = await db_module.insert_log(
        db,
        created_at=record.created_at,
        method=record.method,
        url=record.url,
        request_headers=record.request_headers,
        request_body=decode_body(record.request_body),
        status_code=record.status_code,
        response_headers=record.response_headers,
        response_body=decode_body(record.response_body),
        ttfb_ms=record.ttfb_ms,
        total_ms=record.total_ms,
        source=record.source,
        api_key_label=record.api_key_label,
        model=record.model,
        prompt_tokens=record.prompt_tokens,
        completion_tokens=record.completion_tokens,
        total_tokens=record.total_tokens,
    )
    if broadcaster is not None:
        broadcaster.publish(
            {"id": log_id, **record.model_dump(include=SUMMARY_FIELDS)})
    return log_id


@app.post("/logs", status_code=201)
async def create_log(record: LogRecord, request: Request):
    log_id = await store_log(
        request.app.state.db, request.app.state.broadcaster, record)
    return {"id": log_id}


@app.get("/logs")
async def list_logs(request: Request, method: str | None = None,
                     status_code: int | None = None, url_contains: str | None = None,
                     before_id: int | None = None, limit: int = 200):
    return await db_module.list_logs(
        request.app.state.db, method=method, status_code=status_code,
        url_contains=url_contains, before_id=before_id, limit=limit)


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
