"""SQLite storage: the labeling queue, cached broker payloads, and labels."""

import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DB_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    object_id      TEXT PRIMARY KEY,   -- LSST diaObjectId
    antares_locus  TEXT,
    ra             REAL,
    dec            REAL,
    source         TEXT NOT NULL,      -- how it entered the queue (selection function)
    source_params  TEXT,               -- JSON: query, seed, etc.
    added_at       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending'   -- pending | labeled | skipped
);
CREATE TABLE IF NOT EXISTS cache (
    object_id   TEXT NOT NULL,
    kind        TEXT NOT NULL,         -- antares | alerce | babamul | xmatch
    fetched_at  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    PRIMARY KEY (object_id, kind)
);
CREATE TABLE IF NOT EXISTS labels (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id    TEXT NOT NULL,
    phase        TEXT NOT NULL,        -- blind | revealed
    klass        TEXT NOT NULL,
    subclass     TEXT,
    is_real      INTEGER,
    confidence   TEXT,
    flags        TEXT,                 -- JSON list
    host         TEXT,                 -- JSON: chosen host or {"hostless": true}
    notes        TEXT,
    labeler      TEXT,
    created_at   TEXT NOT NULL,
    snapshot     TEXT,                 -- JSON: what the labeler saw (n alerts, last mjd, fetch times)
    app_version  TEXT
);
CREATE INDEX IF NOT EXISTS labels_object ON labels(object_id);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_conn = connect()
_conn.executescript(SCHEMA)


def execute(sql, params=()):
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, params).fetchall()]


def add_to_queue(object_id, antares_locus, ra, dec, source, source_params):
    cur = execute(
        "INSERT OR IGNORE INTO queue (object_id, antares_locus, ra, dec, source, source_params, added_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(object_id), antares_locus, ra, dec, source, json.dumps(source_params), now()),
    )
    return cur.rowcount


def next_pending(limit=1, exclude=()):
    marks = ",".join("?" * len(exclude))
    extra = f"AND object_id NOT IN ({marks})" if exclude else ""
    return query(
        f"SELECT * FROM queue WHERE status = 'pending' {extra} ORDER BY added_at, rowid LIMIT ?",
        (*exclude, limit),
    )


def get_queue_item(object_id):
    rows = query("SELECT * FROM queue WHERE object_id = ?", (object_id,))
    return rows[0] if rows else None


def set_status(object_id, status):
    execute("UPDATE queue SET status = ? WHERE object_id = ?", (status, object_id))


def cache_get(object_id, kind):
    rows = query("SELECT payload, fetched_at FROM cache WHERE object_id = ? AND kind = ?", (object_id, kind))
    if not rows:
        return None
    return {"fetched_at": rows[0]["fetched_at"], "data": json.loads(rows[0]["payload"])}


def cache_put(object_id, kind, data):
    execute(
        "INSERT OR REPLACE INTO cache (object_id, kind, fetched_at, payload) VALUES (?, ?, ?, ?)",
        (object_id, kind, now(), json.dumps(data, default=str)),
    )


def add_label(row):
    cols = ",".join(row)
    marks = ",".join("?" * len(row))
    return execute(f"INSERT INTO labels ({cols}) VALUES ({marks})", tuple(row.values())).lastrowid


def labels_for(object_id):
    return query("SELECT * FROM labels WHERE object_id = ? ORDER BY id", (object_id,))


def stats():
    q = {r["status"]: r["n"] for r in query("SELECT status, COUNT(*) n FROM queue GROUP BY status")}
    # Latest label per object decides its class in the tally.
    by_class = query(
        "SELECT klass, COUNT(*) n FROM labels WHERE id IN (SELECT MAX(id) FROM labels GROUP BY object_id) GROUP BY klass"
    )
    changed = query(
        "SELECT COUNT(*) n FROM (SELECT object_id FROM labels GROUP BY object_id "
        "HAVING COUNT(DISTINCT klass) > 1)"
    )[0]["n"]
    return {"queue": q, "by_class": {r["klass"]: r["n"] for r in by_class}, "revised_after_reveal": changed}
