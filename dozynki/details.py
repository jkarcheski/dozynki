"""Rubin per-detection measurements for the rejection rules (extendedness, dipole, PSF fit, ...).

New ANTARES fetches carry them in the payload (antares.get_locus "lsst_details"). Objects cached
before that get them in a separate cache kind, so the evidence they were labeled with stays as it was.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

from . import db
from .brokers import antares

KIND = "lsst_details"
_lock = threading.Lock()
_state = {"running": False, "done": 0, "total": 0, "errors": 0}


def get(object_id, ant):
    """Details for one object from its cached ANTARES payload or the backfill cache; None if neither has them."""
    if "lsst_details" in ant:
        return ant["lsst_details"]
    hit = db.cache_get(object_id, KIND)
    return hit["data"] if hit else None


def missing():
    """Objects with a cached ANTARES payload but no details, as (object_id, locus_id)."""
    return [(r["object_id"], r["locus"]) for r in db.query(
        "SELECT c.object_id, json_extract(c.payload, '$.locus_id') locus FROM cache c WHERE c.kind = 'antares' "
        "AND json_type(c.payload, '$.lsst_details') IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM cache d WHERE d.object_id = c.object_id AND d.kind = ?)", (KIND,))]


def ensure(object_id):
    """Fetch details for one object if it lacks them (screening calls this after assemble.load)."""
    ant = (db.cache_get(object_id, "antares") or {}).get("data")
    if ant and get(object_id, ant) is None:
        db.cache_put(object_id, KIND, antares.alert_details(ant["locus_id"]))


def _one(row):
    oid, locus = row
    try:
        db.cache_put(oid, KIND, antares.alert_details(locus))
    except Exception:
        with _lock:
            _state["errors"] += 1
    with _lock:
        _state["done"] += 1


def backfill():
    """Fetch details for every cached object missing them, in the background; a no-op while one runs."""
    with _lock:
        if _state["running"]:
            return
        rows = missing()
        _state.update(running=True, done=0, total=len(rows), errors=0)

    def work():
        try:
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(_one, rows))
        finally:
            with _lock:
                _state["running"] = False

    threading.Thread(target=work, daemon=True).start()


def status():
    with _lock:
        return {**_state, "missing": len(missing()) if not _state["running"] else _state["total"] - _state["done"]}
