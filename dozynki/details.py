"""Broker data the rejection rules read beyond the ANTARES payload, fetched without our own crossmatch:

- lsst_details: Rubin per-detection measurements (extendedness, dipole, PSF fit, ...). New ANTARES
  fetches carry them in the payload (antares.get_locus "lsst_details"); objects cached before that
  get them here, so the evidence they were labeled with stays as it was.
- antares_catalogs: ANTARES's own catalog matches (Gaia DR3 with parallax and proper motion,
  Milliquas, VSX, galaxies, ...), within about 1 arcsec of the locus (antares.catalog_matches).
- ls_hosts: Legacy Survey DR10 galaxies within the crossmatch radius, ranked by DLR, for the
  nuclear rules. Not a broker, but one quick Data Lab query, fetched only when a filter uses
  them; objects with the full crossmatch read its host candidates instead.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from . import crossmatch, db
from .brokers import antares
from .config import XMATCH_RADIUS_ARCSEC

KIND = "lsst_details"
CATALOGS = "antares_catalogs"
HOSTS = "ls_hosts"
_lock = threading.Lock()
_state = {"running": False, "done": 0, "total": 0, "errors": 0}


def _current(det):
    """Whether details carry every field kept now (template flux came later); empty ones count."""
    return not det or "template_flux" in next(iter(det.values()))


def get(object_id, ant):
    """Details for one object: the backfill cache when the ANTARES payload's copy predates a field,
    else the payload's; None if neither has them."""
    hit = db.cache_get(object_id, KIND)
    if "lsst_details" in ant and (_current(ant["lsst_details"]) or not hit):
        return ant["lsst_details"]
    return hit["data"] if hit else None


def catalogs(object_id):
    """ANTARES's catalog matches for one object; None if not fetched."""
    hit = db.cache_get(object_id, CATALOGS)
    return hit["data"] if hit else None


def hosts(object_id):
    """Legacy Survey host candidates, best (lowest DLR) first: from the full crossmatch if
    cached and Legacy Survey answered, else from ls_hosts; None if neither has them."""
    xm = (db.cache_get(object_id, "xmatch") or {}).get("data")
    if xm and xm.get("legacy_survey") is not None:
        return xm.get("host_candidates") or []
    hit = db.cache_get(object_id, HOSTS)
    return hit["data"] if hit else None


def _fetch_hosts(object_id, ra, dec):
    galaxies = [g for g in crossmatch.legacy_survey(ra, dec, XMATCH_RADIUS_ARCSEC) if g.get("d_dlr") is not None]
    db.cache_put(object_id, HOSTS, sorted(galaxies, key=lambda g: g["d_dlr"])[:5])


def missing():
    """Objects with a cached ANTARES payload lacking current details or catalog matches, as (object_id, locus_id, kinds)."""
    rows = db.query(
        "SELECT c.object_id, json_extract(c.payload, '$.locus_id') locus, json_extract(c.payload, '$.lsst_details') payload_det, "
        "(SELECT d.payload FROM cache d WHERE d.object_id = c.object_id AND d.kind = ?) cached_det, "
        "NOT EXISTS (SELECT 1 FROM cache d WHERE d.object_id = c.object_id AND d.kind = ?) no_catalogs "
        "FROM cache c WHERE c.kind = 'antares'", (KIND, CATALOGS))
    out = []
    for r in rows:
        dets = [json.loads(d) for d in (r["payload_det"], r["cached_det"]) if d is not None]
        kinds = ([] if any(_current(d) for d in dets) else [KIND]) + ([CATALOGS] if r["no_catalogs"] else [])
        if kinds:
            out.append((r["object_id"], r["locus"], kinds))
    return out


def _fetch(object_id, locus_id, kinds):
    if KIND in kinds:
        db.cache_put(object_id, KIND, antares.alert_details(locus_id))
    if CATALOGS in kinds:
        db.cache_put(object_id, CATALOGS, antares.catalog_matches(locus_id))


def ensure(object_id, with_hosts=False):
    """Fetch what one object lacks (screening calls this after fetching ANTARES); Legacy Survey
    hosts only with_hosts, and a failed host query leaves them missing (the rules then keep it)."""
    ant = (db.cache_get(object_id, "antares") or {}).get("data")
    if not ant:
        return
    det = get(object_id, ant)
    kinds = ([KIND] if det is None or not _current(det) else []) + ([CATALOGS] if catalogs(object_id) is None else [])
    _fetch(object_id, ant["locus_id"], kinds)
    if with_hosts and hosts(object_id) is None:
        try:
            _fetch_hosts(object_id, ant["ra"], ant["dec"])
        except Exception:
            pass


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
