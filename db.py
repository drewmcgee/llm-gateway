import json

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
    total_ms REAL NOT NULL
)
"""


async def connect(path=DB_PATH):
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute(CREATE_TABLE_SQL)
    await db.commit()
    return db


async def insert_log(db, *, method, url, request_headers, request_body,
                      status_code, response_headers, response_body,
                      ttfb_ms, total_ms):
    await db.execute(
        """INSERT INTO logs
           (method, url, request_headers, request_body,
            status_code, response_headers, response_body,
            ttfb_ms, total_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        ),
    )
    await db.commit()
