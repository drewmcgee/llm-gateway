import hashlib
import json
import secrets

import aiosqlite

DB_PATH = "logs.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    request_headers TEXT NOT NULL,
    request_body BLOB,
    status_code INTEGER NOT NULL,
    response_headers TEXT NOT NULL,
    response_body BLOB,
    ttfb_ms REAL NOT NULL,
    total_ms REAL NOT NULL,
    api_key_label TEXT,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    source TEXT NOT NULL CHECK (source IN ('gateway', 'upstream'))
)
"""

CREATE_API_KEYS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    label TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE
)
"""


async def connect(path=DB_PATH):
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute(CREATE_TABLE_SQL)
    await db.execute(CREATE_API_KEYS_TABLE_SQL)
    await db.commit()
    return db


async def insert_log(db, *, created_at, method, url, request_headers, request_body,
                      status_code, response_headers, response_body, ttfb_ms,
                      total_ms, source, api_key_label=None, model=None,
                      prompt_tokens=None, completion_tokens=None, total_tokens=None):
    cursor = await db.execute(
        """INSERT INTO logs
           (created_at, method, url, request_headers, request_body,
            status_code, response_headers, response_body,
            ttfb_ms, total_ms, api_key_label,
            model, prompt_tokens, completion_tokens, total_tokens, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            created_at,
            method,
            url,
            json.dumps(request_headers),
            request_body,
            status_code,
            json.dumps(response_headers),
            response_body,
            ttfb_ms,
            total_ms,
            api_key_label,
            model,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            source,
        ),
    )
    await db.commit()
    return cursor.lastrowid


LOG_SUMMARY_COLUMNS = ("id", "created_at", "method", "url", "status_code",
                       "ttfb_ms", "total_ms", "api_key_label", "model",
                       "prompt_tokens", "completion_tokens", "total_tokens",
                       "source")


def _row_to_summary(row):
    return dict(zip(LOG_SUMMARY_COLUMNS, row))


async def list_logs(db, *, method=None, status_code=None, url_contains=None, limit=200):
    clauses = []
    params = []
    if method:
        clauses.append("method = ?")
        params.append(method)
    if status_code is not None:
        clauses.append("status_code = ?")
        params.append(status_code)
    if url_contains:
        clauses.append("url LIKE ?")
        params.append(f"%{url_contains}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    cursor = await db.execute(
        f"""SELECT {', '.join(LOG_SUMMARY_COLUMNS)} FROM logs
            {where} ORDER BY id DESC LIMIT ?""",
        params,
    )
    rows = await cursor.fetchall()
    return [_row_to_summary(row) for row in rows]


async def get_log(db, log_id):
    cursor = await db.execute(
        """SELECT id, created_at, method, url, request_headers, request_body,
                  status_code, response_headers, response_body,
                  ttfb_ms, total_ms, api_key_label,
                  model, prompt_tokens, completion_tokens, total_tokens, source
           FROM logs WHERE id = ?""",
        (log_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    (log_id, created_at, method, url, request_headers, request_body,
     status_code, response_headers, response_body, ttfb_ms, total_ms,
     api_key_label, model, prompt_tokens, completion_tokens, total_tokens,
     source) = row
    return {
        "id": log_id,
        "created_at": created_at,
        "method": method,
        "url": url,
        "request_headers": json.loads(request_headers),
        "request_body": _decode_body(request_body),
        "status_code": status_code,
        "response_headers": json.loads(response_headers),
        "response_body": _decode_body(response_body),
        "ttfb_ms": ttfb_ms,
        "total_ms": total_ms,
        "api_key_label": api_key_label,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "source": source,
    }


def _decode_body(body):
    if body is None:
        return None
    return body.decode("utf-8", errors="replace")


def hash_key(raw_key):
    # Unlike passwords, gateway keys are high-entropy random tokens (256 bits),
    # so a fast unsalted hash is sufficient -- brute force isn't feasible and
    # a slow KDF (bcrypt/argon2) would just add latency for no benefit here.
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def create_api_key(db, label, prefix="gw"):
    raw_key = f"{prefix}_{secrets.token_urlsafe(32)}"
    await db.execute(
        "INSERT INTO api_keys (label, key_hash) VALUES (?, ?)",
        (label, hash_key(raw_key)),
    )
    await db.commit()
    return raw_key


async def get_api_key_label(db, key_hash):
    cursor = await db.execute(
        "SELECT label FROM api_keys WHERE key_hash = ?", (key_hash,))
    row = await cursor.fetchone()
    return row[0] if row else None
