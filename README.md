# LLM Gateway

A proxy that sits in front of the OpenAI API, authenticates callers with its own
keys, streams responses back untouched, and logs every request to a separate
logging service that a React dashboard reads live.

## Architecture

Three processes, two of them Python:

```
client ──POST /v1/*──►  proxy.py  :8000  ──►  api.openai.com
                           │
                           │ POST /logs   (fire-and-forget, non-blocking)
                           ▼
                        backend.py :8001  ──►  logs.db
                           │
                           │ GET /logs, GET /logs/{id}, GET /logs/stream (SSE)
                           ▼
                        dashboard :5173
```

**`proxy.py` (port 8000)** — the hot path. Authenticates the caller's
`X-API-Key`, swaps in the real OpenAI credential, and streams the upstream
response straight through to the client. It owns `keys.db`.

It intercepts every HTTP method — `GET, POST, PUT, PATCH, DELETE, HEAD,
OPTIONS` — not just `POST`, so management endpoints (`GET /v1/models`,
`DELETE /v1/files/{id}`) proxy and log like anything else. The verb is
forwarded as sent rather than rewritten, and the query string travels with it.

**`backend.py` (port 8001)** — the logging service. Owns `logs.db` and
everything that reads from it: paginated history, per-request detail, and the
live SSE feed the dashboard subscribes to. `POST /logs` is the ingest endpoint.

**`dashboard/`** — Vite + React, reads from the backend on 8001.

### Why the logging is decoupled

The proxy never awaits the log write. `LogClient.send()` schedules the POST with
`asyncio.create_task` and returns immediately, and `post()` swallows every
exception. Consequences worth stating plainly:

- **Gateway availability doesn't depend on the logger.** If the backend is down,
  requests are still proxied and still stream normally; the record is dropped
  with a warning on stdout. Verified: with `backend.py` killed, the proxy still
  answers in ~3ms.
- **Stream teardown isn't blocked by a database write.** Previously the log
  insert happened inline in `body_iterator`'s `finally`, so every client waited
  on it. Now the client's stream closes at its own pace.
- **Auth doesn't cross the network.** Key lookup hits the proxy's own `keys.db`,
  not the backend — no per-request HTTP hop on the hot path, and no dependency
  on the logging service to authenticate.

The tradeoff is that logging is best-effort: a dropped record is gone. That is
the right call here, because a log line is worth less than the request it
describes. `LogClient.drain()` flushes in-flight posts on proxy shutdown so
clean restarts don't lose anything.

### Streaming, and how to read TTFB

The proxy streams whatever the upstream streams — it never buffers a response
to inspect it. Whether you actually get incremental output is decided by your
request, not by the gateway:

- **`"stream": true`** — OpenAI sends response headers immediately and emits
  SSE frames as tokens are produced. The proxy forwards each chunk as it
  arrives, so the caller sees output while the model is still working.
- **No `stream` flag (the default)** — OpenAI generates the *entire* response
  before sending anything, then returns it as one JSON document.

This is the single biggest influence on the timings in the dashboard, because
`ttfb_ms` measures time until the upstream **response headers** arrive:

| Request | TTFB | Total |
| --- | --- | --- |
| `"stream": true` | a fraction of total — first token | full generation |
| default (buffered) | ≈ total — headers wait for the last token | full generation |

So a buffered request showing `TTFB 5140 ms / Total 5182 ms` is not a stall:
the model spent 5.1s generating, and the finished body then transferred in
42 ms. If you want TTFB to mean "time to first token", send `"stream": true`:

```bash
curl -sN http://localhost:8000/v1/responses \
  -H "X-API-Key: gw_..." -H "content-type: application/json" \
  -d '{"model":"gpt-5-mini","input":"write a short essay","stream":true}'
```

Streamed bodies are logged as the raw SSE frames they arrived as. Because a
short essay is ~450 single-token frames, the dashboard renders them as an event
summary plus the reconstructed output text, with the raw frames one click away.

### Token accounting

Usage is reported differently per endpoint, so `extract_usage` normalises it:
Chat Completions reports `prompt_tokens`/`completion_tokens`, the Responses API
reports the same two counts as `input_tokens`/`output_tokens`, and embeddings
report a prompt count with no completion count.

Streamed responses need one extra step: the body is a sequence of SSE frames
rather than a single JSON document, and the counts ride on the terminal frame —
`response.completed` for the Responses API (which nests the usage object one
level down, under `response`), or the final chunk for Chat Completions under
`stream_options.include_usage`. `extract_usage` scans the frames backwards and
stops at the first one carrying usage, so streamed and buffered requests report
tokens identically.

A request whose stream genuinely carries no usage still logs with no counts,
and the dashboard renders whichever counts exist instead of printing `null`.

### Wire format

Log records cross the process boundary as JSON, so the raw `bytes` request and
response bodies travel base64-encoded (`logclient.encode_body`) and are decoded
back to `bytes` before the backend writes them to the BLOB columns. Base64
rather than UTF-8 because endpoints like `/v1/audio/speech` return bodies that
aren't decodable text. Credentials are redacted in `build_record` — on the proxy
side, before anything leaves the process.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env        # add your OPENAI_API_KEY

uvicorn backend:app --port 8001 --reload    # logging service, start first
uvicorn proxy:app   --port 8000 --reload    # gateway
cd dashboard && npm install && npm run dev  # http://localhost:5173
```

Issue yourself a gateway key, then call the proxy with it:

```bash
python create_key.py my-app          # prints the raw key once; only the hash is stored

curl http://localhost:8000/v1/chat/completions \
  -H "X-API-Key: gw_..." -H "content-type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
```

`python seed_logs.py 250` fills `logs.db` with synthetic rows if you want the
dashboard populated without spending tokens.

### Authentication

The gateway accepts its own key two ways, so it is drop-in for existing OpenAI
clients:

| Header | For |
| --- | --- |
| `X-API-Key: gw_...` | curl, and anything speaking the gateway's own scheme |
| `Authorization: Bearer gw_...` | the OpenAI SDKs, unchanged |

`X-API-Key` wins if both are present. Either way the caller's credential is
**stripped** before the request is forwarded — the proxy substitutes the real
`OPENAI_API_KEY`, so a gateway key never reaches OpenAI and the caller never
sees the upstream one.

That means an OpenAI SDK needs nothing but a `base_url`:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="gw_...")

with client.responses.stream(model="gpt-5-mini", input="write an essay") as stream:
    for event in stream:        # the SDK reassembles the deltas
        ...
```

The gateway forwards SSE frames through byte-for-byte and in real time, so
streaming clients reconstruct output exactly as they would against
`api.openai.com` — the gateway never buffers a response to inspect it.

## Tests

```bash
pytest
```

`test_logclient.py` and `test_proxy.py` carry the tests specific to the split:
that `send()` returns before the backend is even contacted, that a stream
finishes while its log post is still in flight, and that a dead backend never
surfaces to the client.
