"""Broker data the rejection rules read beyond the ANTARES payload, fetched without our own crossmatch:

- lsst_details: Rubin per-detection measurements (extendedness, dipole, PSF fit, ...). New ANTARES
  fetches carry them in the payload (antares.get_locus "lsst_details"); objects cached before that
  get them here, so the evidence they were labeled with stays as it was.
- antares_catalogs: ANTARES's own catalog matches (Gaia DR3 with parallax and proper motion,
  Milliquas, VSX, ...), within about 1 arcsec of the locus (antares.catalog_matches).
"""

import threading
from concurrent.futures import ThreadPoolExecutor

from . import db
from .brokers import antares

KIND = "lsst_details"
CATALOGS = "antares_catalogs"
_lock = threading.Lock()
_state = {"running": False, "done": 0, "total": 0, "errors": 0}


def get(object_id, ant):
    """Details for one object from its cached ANTARES payload or the backfill cache; None if neither has them."""
    if "lsst_details" in ant:
        return ant["lsst_details"]
    hit = db.cache_get(object_id, KIND)
    return hit["data"] if hit else None


def catalogs(object_id):
    """ANTARES's catalog matches for one object; None if not fetched."""
    hit = db.cache_get(object_id, CATALOGS)
    return hit["data"] if hit else None


def missing():
    """Objects with a cached ANTARES payload lacking details or catalog matches, as (object_id, locus_id, kinds)."""
    return [(r["object_id"], r["locus"], [k for k, gone in ((KIND, r["no_details"]), (CATALOGS, r["no_catalogs"])) if gone])
            for r in db.query(
        "SELECT c.object_id, json_extract(c.payload, '$.locus_id') locus, "
        "json_type(c.payload, '$.lsst_details') IS NULL AND NOT EXISTS "
        "  (SELECT 1 FROM cache d WHERE d.object_id = c.object_id AND d.kind = ?) no_details, "
        "NOT EXISTS (SELECT 1 FROM cache d WHERE d.object_id = c.object_id AND d.kind = ?) no_catalogs "
        "FROM cache c WHERE c.kind = 'antares'", (KIND, CATALOGS))
            if r["no_details"] or r["no_catalogs"]]


def _fetch(object_id, locus_id, kinds):
    if KIND in kinds:
        db.cache_put(object_id, KIND, antares.alert_details(locus_id))
    if CATALOGS in kinds:
        db.cache_put(object_id, CATALOGS, antares.catalog_matches(locus_id))


def ensure(object_id):
    """Fetch whatever of the two one object lacks (screening calls this after fetching ANTARES)."""
    ant = (db.cache_get(object_id, "antares") or {}).get("data")
    if not ant:
        return
    kinds = ([KIND] if get(object_id, ant) is None else []) + ([CATALOGS] if catalogs(object_id) is None else [])
    _fetch(object_id, ant["locus_id"], kinds)


def _one(row):
    try:
        _fetch(*row)
    except Exception:
        with _lock:
            _state["errors"] += 1
    with _lock:
        _state["done"] += 1


def backfill():
    """Fetch what every cached object is missing, in the background; a no-op while one runs."""
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
