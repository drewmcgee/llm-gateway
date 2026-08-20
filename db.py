import hashlib
import json
import secrets

import aiosqlite

DB_PATH = "logs.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    request_headers TEXT NOT NULL,
    request_body BLOB,
    status_code INTEGER NOT NULL,
    response_headers TEXT NOT NULL,
    response_body BLOB,
    ttfb_ms REAL NOT NULL,
    total_ms REAL NOT NULL,
    api_key_label TEXT
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


async def insert_log(db, *, method, url, request_headers, request_body,
                      status_code, response_headers, response_body,
                      ttfb_ms, total_ms, api_key_label=None):
    await db.execute(
        """INSERT INTO logs
           (method, url, request_headers, request_body,
            status_code, response_headers, response_body,
            ttfb_ms, total_ms, api_key_label)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
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
        ),
    )
    await db.commit()


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
