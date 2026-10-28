"""SQLite storage: the labeling queue, cached broker payloads, and labels."""

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .config import DB_PATH, LABELER

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
    status         TEXT NOT NULL DEFAULT 'pending'   -- pending | labeled | skipped | watch | screening | filtered
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
CREATE TABLE IF NOT EXISTS night_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    night       TEXT NOT NULL,         -- Rubin dayObs, YYYY-MM-DD
    screen      TEXT NOT NULL,         -- saved filter name
    version     TEXT NOT NULL,         -- its pipeline's hash then (rejection.version)
    pipeline    TEXT NOT NULL,         -- JSON: the resolved pipeline then
    seed        INTEGER NOT NULL,      -- the random order passing objects were queued in
    state       TEXT NOT NULL,         -- counting | listing | done | error (nightrun.py)
    n_night     INTEGER,               -- LSST objects alerting that night
    n_listed    INTEGER,               -- left after the rules ANTARES applies itself
    n_passed    INTEGER,               -- left after every rule the listing answers; all queued
    object_ids  TEXT,                  -- JSON list: those objects, in queue order
    error       TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
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
CREATE TABLE IF NOT EXISTS purchases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    labeler     TEXT NOT NULL,
    item        TEXT NOT NULL,         -- farm.ITEMS key
    price       INTEGER NOT NULL,      -- coins paid then
    bought_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wearing (
    labeler     TEXT NOT NULL,
    slot        TEXT NOT NULL,         -- farm.SLOTS
    item        TEXT NOT NULL,         -- farm.WARDROBE key; no row means the default
    PRIMARY KEY (labeler, slot)
);
CREATE TABLE IF NOT EXISTS queue_status (
    object_id   TEXT NOT NULL,
    labeler     TEXT NOT NULL,
    status      TEXT NOT NULL,         -- labeled | skipped | watch; no row means still to vet for this labeler
    PRIMARY KEY (object_id, labeler)
);
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    handle      TEXT NOT NULL UNIQUE COLLATE NOCASE,  -- also the labeler name on their labels
    password    TEXT NOT NULL,         -- scrypt hash (accounts.py)
    email_hash  TEXT NOT NULL,         -- scrypt hash of the lower-cased email; the address itself is never stored
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,      -- sha256 of the cookie value
    user_id     INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash  TEXT PRIMARY KEY,      -- sha256 of the emailed token
    user_id     INTEGER NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT
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
    "queue": {"n_det": "INTEGER", "first_mjd": "REAL", "last_mjd": "REAL",
              # filtered mode (screen.py): saved filter name the pull was screened with (NULL in random mode),
              # and its verdict: "kept", the rejecting step, or "error" (couldn't fetch, so kept)
              "screen": "TEXT", "screen_result": "TEXT"},
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
# Labeled, skipped and watched were once one status for everyone; now they are each labeler's own
# (queue_status), and queue.status keeps only whether an object is ready: pending | screening | filtered.
# A labeled or watched object goes to its latest labeler, a skipped one to this install's labeler.
_conn.execute("INSERT OR IGNORE INTO queue_status (object_id, labeler, status) "
              "SELECT q.object_id, COALESCE((SELECT COALESCE(l.labeler, 'anonymous') FROM labels l "
              "WHERE l.object_id = q.object_id ORDER BY l.id DESC LIMIT 1), ?), q.status "
              "FROM queue q WHERE q.status IN ('labeled', 'skipped', 'watch')", (LABELER,))
_conn.execute("UPDATE queue SET status = 'pending' WHERE status IN ('labeled', 'skipped', 'watch')")
_conn.commit()


def execute(sql, params=()):
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, params).fetchall()]


def add_to_queue(object_id, antares_locus, ra, dec, source, source_params, summary=None, screen=None):
    """Queue an object; with screen (a saved filter name) it waits as 'screening' for screen.py.

    An object already queued by a filtered-mode pull and not yet labeled is taken over by a
    random-mode pull, so random mode stays random even for objects a filter threw away.
    """
    summary = summary or {}
    row = (str(object_id), antares_locus, ra, dec, source, json.dumps(source_params), now(),
           summary.get("n_det"), summary.get("first_mjd"), summary.get("last_mjd"), screen,
           "screening" if screen else "pending")
    cur = execute(
        "INSERT OR IGNORE INTO queue (object_id, antares_locus, ra, dec, source, source_params, added_at, "
        "n_det, first_mjd, last_mjd, screen, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
    if cur.rowcount or screen:
        return cur.rowcount
    return execute("UPDATE queue SET source = ?, source_params = ?, added_at = ?, screen = NULL, screen_result = NULL, "
                   "status = 'pending' WHERE object_id = ? AND screen IS NOT NULL AND status IN ('screening', 'filtered', 'pending') "
                   "AND object_id NOT IN (SELECT object_id FROM queue_status)",
                   (source, json.dumps(source_params), now(), str(object_id))).rowcount


def add_many_screening(rows, screen):
    """Queue (object_id, antares_locus, ra, dec, source, source_params, summary) rows as 'screening'
    in one transaction, keeping their order; objects already queued are left as they are."""
    t = now()
    with _lock:
        before = _conn.total_changes
        _conn.executemany(
            "INSERT OR IGNORE INTO queue (object_id, antares_locus, ra, dec, source, source_params, added_at, "
            "n_det, first_mjd, last_mjd, screen, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'screening')",
            [(str(oid), locus, ra, dec, source, json.dumps(params), t, sm.get("n_det"), sm.get("first_mjd"),
              sm.get("last_mjd"), screen) for oid, locus, ra, dec, source, params, sm in rows])
        _conn.commit()
        return _conn.total_changes - before


def add_night_run(night, screen, version, pipeline, seed):
    return execute("INSERT INTO night_runs (night, screen, version, pipeline, seed, state, started_at) "
                   "VALUES (?, ?, ?, ?, ?, 'counting', ?)",
                   (night, screen, version, json.dumps(pipeline), seed, now())).lastrowid


def update_night_run(run_id, **fields):
    if "object_ids" in fields:
        fields["object_ids"] = json.dumps(fields["object_ids"])
    if fields.get("state") in ("done", "error"):
        fields["finished_at"] = now()
    execute(f"UPDATE night_runs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), run_id))


def night_runs(screen=None):
    rows = query("SELECT * FROM night_runs" + (" WHERE screen = ?" if screen else "") + " ORDER BY id",
                 (screen,) if screen else ())
    return [r | {"pipeline": json.loads(r["pipeline"]), "object_ids": json.loads(r["object_ids"] or "[]")} for r in rows]


def set_screened(object_id, status, result):
    # only while still waiting: a random-mode pull may have taken the object over meanwhile
    execute("UPDATE queue SET status = ?, screen_result = ? WHERE object_id = ? AND status = 'screening'",
            (status, result, object_id))


def mode_counts(screen, labeler):
    """Queue counts within one mode (screen None is random mode), with pending meaning still to vet for this labeler."""
    rows = query("SELECT status, COUNT(*) n FROM queue WHERE screen IS ? AND status != 'pending' GROUP BY status", (screen,))
    return {r["status"]: r["n"] for r in rows} | {"pending": count_pending(labeler, "screen IS ?", (screen,))}


def _not_done(labeler):
    """WHERE over queue rows this labeler hasn't labeled, skipped or put on their watchlist."""
    return "NOT EXISTS (SELECT 1 FROM queue_status s WHERE s.object_id = queue.object_id AND s.labeler = ?)", (labeler,)


def next_pending(labeler, limit=1, exclude=(), where="", params=()):
    done, done_params = _not_done(labeler)
    marks = ",".join("?" * len(exclude))
    extra = f"AND object_id NOT IN ({marks})" if exclude else ""
    extra += f" AND {where}" if where else ""
    return query(
        f"SELECT * FROM queue WHERE status = 'pending' AND {done} {extra} ORDER BY added_at, rowid LIMIT ?",
        (*done_params, *exclude, *params, limit),
    )


def count_pending(labeler, where="", params=()):
    done, done_params = _not_done(labeler)
    return query(f"SELECT COUNT(*) n FROM queue WHERE status = 'pending' AND {done}{' AND ' + where if where else ''}",
                 (*done_params, *params))[0]["n"]


def pending_missing_summary():
    return query("SELECT object_id, antares_locus FROM queue WHERE status = 'pending' AND n_det IS NULL")


def set_summary(object_id, summary):
    execute("UPDATE queue SET n_det = ?, first_mjd = ?, last_mjd = ? WHERE object_id = ?",
            (summary.get("n_det"), summary.get("first_mjd"), summary.get("last_mjd"), object_id))


def get_queue_item(object_id):
    rows = query("SELECT * FROM queue WHERE object_id = ?", (object_id,))
    return rows[0] if rows else None


def set_status(object_id, labeler, status):
    """This labeler's own status for an object (labeled | skipped | watch); None puts it back in their queue."""
    if status is None:
        execute("DELETE FROM queue_status WHERE object_id = ? AND labeler = ?", (object_id, labeler))
    else:
        execute("INSERT OR REPLACE INTO queue_status (object_id, labeler, status) VALUES (?, ?, ?)",
                (object_id, labeler, status))


def watched(labeler):
    return query("SELECT q.* FROM queue q JOIN queue_status s USING (object_id) WHERE s.labeler = ? AND s.status = 'watch'",
                 (labeler,))


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


def labels_for(object_id, labeler):
    """One labeler's labels of an object: everyone vets blind, so nobody sees another's labels here."""
    return query("SELECT * FROM labels WHERE object_id = ? AND COALESCE(labeler, 'anonymous') = ? ORDER BY id",
                 (object_id, labeler))


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


def stats(labeler):
    """This labeler's queue (pending in every mode, and their labeled / skipped / watch); class tallies over everyone."""
    q = {"pending": count_pending(labeler)}
    q |= {r["status"]: r["n"] for r in query("SELECT status, COUNT(*) n FROM queue_status WHERE labeler = ? GROUP BY status",
                                             (labeler,))}
    # Latest label per object decides its class in the tally.
    by_class = query(
        "SELECT klass, COUNT(*) n FROM labels WHERE id IN (SELECT MAX(id) FROM labels GROUP BY object_id) GROUP BY klass"
    )
    changed = query(
        "SELECT COUNT(*) n FROM (SELECT object_id FROM labels GROUP BY object_id "
        "HAVING COUNT(DISTINCT klass) > 1)"
    )[0]["n"]
    return {"queue": q, "by_class": {r["klass"]: r["n"] for r in by_class}, "revised_after_reveal": changed}


# Guests label as "guest:<random id>" (accounts.py); the leaderboard counts them together.
WHO = "CASE WHEN labeler LIKE 'guest:%' THEN 'guests' ELSE COALESCE(labeler, 'anonymous') END"


def leaderboard(days=7):
    """Objects labeled per labeler (guests as one), overall and in the last `days` days, most first."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return query(
        f"SELECT {WHO} labeler, COUNT(DISTINCT object_id) objects, "
        "COUNT(DISTINCT CASE WHEN phase = 'blind' THEN object_id END) blind, "
        "COUNT(DISTINCT CASE WHEN created_at >= ? THEN object_id END) recent, MAX(created_at) last_at "
        f"FROM labels GROUP BY {WHO} ORDER BY objects DESC, labeler",
        (since,),
    )


def add_purchase(labeler, item, price):
    execute("INSERT INTO purchases (labeler, item, price, bought_at) VALUES (?, ?, ?, ?)", (labeler, item, price, now()))


def purchases(labeler):
    return query("SELECT item, price, bought_at FROM purchases WHERE labeler = ? ORDER BY id", (labeler,))


def wearing(labeler):
    return {r["slot"]: r["item"] for r in query("SELECT slot, item FROM wearing WHERE labeler = ?", (labeler,))}


def put_on(labeler, slot, item=None):
    """Wear item in slot, or with None go back to the default."""
    if item is None:
        execute("DELETE FROM wearing WHERE labeler = ? AND slot = ?", (labeler, slot))
    else:
        execute("INSERT OR REPLACE INTO wearing (labeler, slot, item) VALUES (?, ?, ?)", (labeler, slot, item))
