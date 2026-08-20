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

## Tests

```bash
pytest
```

`test_logclient.py` and `test_proxy.py` carry the tests specific to the split:
that `send()` returns before the backend is even contacted, that a stream
finishes while its log post is still in flight, and that a dead backend never
surfaces to the client.
