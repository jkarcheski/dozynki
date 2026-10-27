"""SQLite storage: the labeling queue, cached broker payloads, and labels."""

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

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
CREATE TABLE IF NOT EXISTS night_pulls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    night       TEXT NOT NULL,         -- Rubin dayObs, YYYY-MM-DD
    filters     TEXT,                  -- JSON: filter bar at pull time
    object_ids  TEXT NOT NULL,         -- JSON list: the objects in this batch
    pulled_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rejection_pipelines (
    version     TEXT PRIMARY KEY,      -- hash of the resolved pipeline (rejection.py)
    config      TEXT NOT NULL,         -- JSON list of {"rule", **params}
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rejections (
    object_id   TEXT NOT NULL,
    version     TEXT NOT NULL,
    step        TEXT NOT NULL,         -- rule name with params, e.g. gaia_star[max_sep=1.0]
    decision    INTEGER,               -- 1 reject, 0 keep, NULL not enough data (kept)
    evaluated_at TEXT NOT NULL,
    PRIMARY KEY (object_id, version, step)
);
CREATE TABLE IF NOT EXISTS saved_pipelines (
    name        TEXT PRIMARY KEY,      -- chosen in the filter lab
    config      TEXT NOT NULL,         -- JSON list of {"rule", **params}, in order
    note        TEXT,                  -- free text, e.g. the questionnaire's answers
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_conn = connect()
_conn.executescript(SCHEMA)

# Columns added after the first release; ALTER keeps existing databases and labels.
MIGRATIONS = {
    "labels": {"alt_classes": "TEXT"},  # JSON list of "could also be" classes
    # locus summary used by the queue filters (filters.py); NULL until fetched
    "queue": {"n_det": "INTEGER", "first_mjd": "REAL", "last_mjd": "REAL"},
    # night batches: the random order (seed), where this batch starts in it, and the night's size then
    "night_pulls": {"seed": "INTEGER", "start": "INTEGER", "n_matching": "INTEGER"},
}
for _table, _cols in MIGRATIONS.items():
    _have = {r["name"] for r in _conn.execute(f"PRAGMA table_info({_table})")}
    for _col, _type in _cols.items():
        if _col not in _have:
            _conn.execute(f"ALTER TABLE {_table} ADD COLUMN {_col} {_type}")
# Night pulls from before batches took the whole night at once: one batch covering it.
_conn.execute("UPDATE night_pulls SET seed = 0, start = 0, n_matching = json_array_length(object_ids) "
              "WHERE seed IS NULL AND json_array_length(object_ids) > 0")
_conn.commit()


def execute(sql, params=()):
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, params).fetchall()]


def add_to_queue(object_id, antares_locus, ra, dec, source, source_params, summary=None):
    summary = summary or {}
    cur = execute(
        "INSERT OR IGNORE INTO queue (object_id, antares_locus, ra, dec, source, source_params, added_at, "
        "n_det, first_mjd, last_mjd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (str(object_id), antares_locus, ra, dec, source, json.dumps(source_params), now(),
         summary.get("n_det"), summary.get("first_mjd"), summary.get("last_mjd")),
    )
    return cur.rowcount


def next_pending(limit=1, exclude=(), where="", params=()):
    marks = ",".join("?" * len(exclude))
    extra = f"AND object_id NOT IN ({marks})" if exclude else ""
    extra += f" AND {where}" if where else ""
    return query(
        f"SELECT * FROM queue WHERE status = 'pending' {extra} ORDER BY added_at, rowid LIMIT ?",
        (*exclude, *params, limit),
    )


def count_pending(where="", params=()):
    return query(f"SELECT COUNT(*) n FROM queue WHERE status = 'pending'{' AND ' + where if where else ''}", params)[0]["n"]


def pending_missing_summary():
    return query("SELECT object_id, antares_locus FROM queue WHERE status = 'pending' AND n_det IS NULL")


def set_summary(object_id, summary):
    execute("UPDATE queue SET n_det = ?, first_mjd = ?, last_mjd = ? WHERE object_id = ?",
            (summary.get("n_det"), summary.get("first_mjd"), summary.get("last_mjd"), object_id))


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


def save_pipeline(version, config):
    execute("INSERT OR IGNORE INTO rejection_pipelines (version, config, created_at) VALUES (?, ?, ?)",
            (version, json.dumps(config), now()))


def saved_pipelines():
    return [r | {"config": json.loads(r["config"])} for r in query("SELECT * FROM saved_pipelines ORDER BY updated_at DESC")]


def get_saved_pipeline(name):
    rows = query("SELECT config FROM saved_pipelines WHERE name = ?", (name,))
    return json.loads(rows[0]["config"]) if rows else None


def put_saved_pipeline(name, config, note=None):
    """Create or overwrite by name; overwriting keeps created_at."""
    t = now()
    execute("INSERT INTO saved_pipelines (name, config, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET config = excluded.config, note = excluded.note, updated_at = excluded.updated_at",
            (name, json.dumps(config), note, t, t))


def delete_saved_pipeline(name):
    return execute("DELETE FROM saved_pipelines WHERE name = ?", (name,)).rowcount


def save_rejections(object_id, version, decisions):
    with _lock:
        _conn.executemany(
            "INSERT OR REPLACE INTO rejections (object_id, version, step, decision, evaluated_at) VALUES (?, ?, ?, ?, ?)",
            [(object_id, version, step, None if d is None else int(d), now()) for step, d in decisions.items()],
        )
        _conn.commit()


def add_night_pull(night, filters, object_ids, seed, start, n_matching):
    return execute("INSERT INTO night_pulls (night, filters, object_ids, pulled_at, seed, start, n_matching) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (night, json.dumps(filters), json.dumps(object_ids), now(), seed, start, n_matching)).lastrowid


def night_pulls():
    # rows without a seed are empty whole-night pulls from before nights were sampled in batches
    return [r | {"filters": json.loads(r["filters"] or "{}"), "object_ids": json.loads(r["object_ids"])}
            for r in query("SELECT * FROM night_pulls WHERE seed IS NOT NULL ORDER BY id")]


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


def leaderboard(days=7):
    """Objects labeled per labeler, overall and in the last `days` days, most first."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return query(
        "SELECT COALESCE(labeler, 'anonymous') labeler, COUNT(DISTINCT object_id) objects, "
        "COUNT(DISTINCT CASE WHEN phase = 'blind' THEN object_id END) blind, "
        "COUNT(DISTINCT CASE WHEN created_at >= ? THEN object_id END) recent, MAX(created_at) last_at "
        "FROM labels GROUP BY COALESCE(labeler, 'anonymous') ORDER BY objects DESC, labeler",
        (since,),
    )
