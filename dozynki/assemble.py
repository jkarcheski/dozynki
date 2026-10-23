"""Fetch (with caching) everything about one object and split it into
evidence (always shown) and broker opinions (hidden until a blind label exists).
"""

import threading
from concurrent.futures import ThreadPoolExecutor

from . import crossmatch, db
from .brokers import alerce, antares, babamul_client

# Per-point fields that are classifier / ML outputs rather than measurements.
OPINION_POINT_FIELDS = {"reliability", "rb", "drb"}

_inflight = {}
_inflight_lock = threading.Lock()


def _cached(object_id, kind, fetch, refresh=False):
    if not refresh:
        hit = db.cache_get(object_id, kind)
        if hit is not None:
            return hit
    db.cache_put(object_id, kind, fetch())
    return db.cache_get(object_id, kind)


def _fetch_antares(item):
    locus_id = item["antares_locus"] or antares.locus_for_dia_object(item["object_id"])["locus_id"]
    data = antares.get_locus(locus_id)
    data["thumbs"] = {}
    for survey in ("lsst", "ztf"):
        dets = [p for p in data["points"] if p["survey"] == survey and p["kind"] == "detection"]
        if dets:
            latest = max(dets, key=lambda p: p["mjd"])
            try:
                data["thumbs"][latest["alert_id"]] = antares.thumbnails(latest["alert_id"])
            except Exception:
                pass
    return data


def load(object_id, refresh=False):
    """Fetch all sources for an object; concurrent callers share one fetch."""
    with _inflight_lock:
        ev = _inflight.get(object_id)
        owner = ev is None
        if owner:
            ev = _inflight[object_id] = threading.Event()
    if not owner:
        ev.wait(timeout=180)
        return _gather(object_id)
    try:
        item = db.get_queue_item(object_id)
        ant = _cached(object_id, "antares", lambda: _fetch_antares(item), refresh)["data"]
        if any(p["kind"] == "detection" and "ra" not in p for p in ant["points"]):
            # cached before per-alert positions were kept: fetch them separately so the
            # rest of the evidence the object was labeled with stays as it was
            _cached(object_id, "positions", lambda: antares.alert_positions(ant["locus_id"]))
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [
                pool.submit(_cached, object_id, "alerce", lambda: alerce.fetch(object_id, ant.get("ztf_object_id")), refresh),
                pool.submit(_cached, object_id, "babamul", lambda: babamul_client.fetch(object_id), refresh),
                pool.submit(_cached, object_id, "xmatch", lambda: crossmatch.run(ant["ra"], ant["dec"]), refresh),
            ]
            for j in jobs:
                j.result()
    finally:
        with _inflight_lock:
            _inflight.pop(object_id, None)
        ev.set()
    return _gather(object_id)


def _gather(object_id):
    return {kind: db.cache_get(object_id, kind) for kind in ("antares", "alerce", "babamul", "xmatch", "skybot", "positions")}


def rerun_xmatch(object_id, radius):
    ant = db.cache_get(object_id, "antares")["data"]
    return _cached(object_id, "xmatch", lambda: crossmatch.run(ant["ra"], ant["dec"], radius), refresh=True)


def skybot(object_id, refresh=False):
    ant = db.cache_get(object_id, "antares")["data"]
    mjds = [p["mjd"] for p in ant["points"] if p["kind"] == "detection"]
    return _cached(object_id, "skybot", lambda: crossmatch.run_skybot(ant["ra"], ant["dec"], mjds), refresh)


def prefetch(object_ids):
    def work():
        for oid in object_ids:
            try:
                load(oid)
            except Exception:
                pass

    threading.Thread(target=work, daemon=True).start()


ZTF_NR_MAX_ARCSEC = 1.5  # reference source must be this close for ref + diff to mean "total flux"


def _with_ztf_total_mag(p):
    """Approximate ZTF total (science) mag as reference flux + signed difference flux.

    Only when the nearest reference-image source is the object itself
    (distnr small); otherwise the reference flux belongs to something else.
    """
    if p.get("survey") != "ztf" or p.get("kind") != "detection" or p.get("sci_mag") is not None:
        return p
    magnr, distnr, diff = p.get("magnr"), p.get("distnr"), p.get("diff_flux")
    if magnr is None or diff is None or distnr is None or not 0 <= distnr <= ZTF_NR_MAX_ARCSEC:
        return p
    total = antares._mag_to_flux(magnr) + diff
    p["sci_mag"] = antares._flux_to_mag(total)
    err = p.get("diff_flux_err")
    p["sci_mag_err"] = 1.0857 * err / total if err and total > 0 else None
    p["sci_mag_approx"] = True
    return p


def refresh_brokers(object_id):
    """Re-fetch broker data (not the crossmatch) after new alerts arrive."""
    item = db.get_queue_item(object_id)
    ant = _cached(object_id, "antares", lambda: _fetch_antares(item), refresh=True)["data"]
    _cached(object_id, "alerce", lambda: alerce.fetch(object_id, ant.get("ztf_object_id")), refresh=True)
    _cached(object_id, "babamul", lambda: babamul_client.fetch(object_id), refresh=True)
    return ant


def evidence(object_id, raw):
    ant = raw["antares"]["data"]
    bab = (raw["babamul"] or {}).get("data") or {}
    positions = (raw.get("positions") or {}).get("data") or {}
    points = [
        _with_ztf_total_mag({**positions.get(p.get("alert_id"), {}), **{k: v for k, v in p.items() if k not in OPINION_POINT_FIELDS}})
        for p in ant["points"]
    ]
    points += bab.get("forced_phot") or []
    lsst_ss = sorted({p["ss_object_id"] for p in points if p.get("ss_object_id")})
    ztf_ss = sorted({p["ss_name"] for p in points if p.get("ss_name")})
    return {
        "object_id": object_id,
        "locus_id": ant["locus_id"],
        "ra": ant["ra"],
        "dec": ant["dec"],
        "ztf_object_id": ant.get("ztf_object_id"),
        "points": points,
        "thumbs": ant.get("thumbs", {}),
        "sso_associations": {"lsst_ssObjectId": lsst_ss, "ztf_ssnamenr": ztf_ss},
        "xmatch": (raw["xmatch"] or {}).get("data"),
        "skybot": (raw.get("skybot") or {}).get("data"),
        "babamul_enabled": bool(bab.get("enabled")),
        "fetched_at": {k: (v or {}).get("fetched_at") for k, v in raw.items()},
    }


def opinions(raw):
    ant = raw["antares"]["data"]
    bab = (raw["babamul"] or {}).get("data") or {}
    lsst_rel = [p.get("reliability") for p in ant["points"] if p["survey"] == "lsst" and p.get("reliability") is not None]
    ztf_drb = [p.get("drb") for p in ant["points"] if p["survey"] == "ztf" and p.get("drb") is not None]
    return {
        "reliability": {
            "lsst_per_alert": lsst_rel,
            "lsst_mean": sum(lsst_rel) / len(lsst_rel) if lsst_rel else None,
            "ztf_drb_per_alert": ztf_drb,
            "ztf_drb_mean": sum(ztf_drb) / len(ztf_drb) if ztf_drb else None,
        },
        "alerce": (raw["alerce"] or {}).get("data"),
        "antares": {"tags": ant.get("tags"), "catalogs": ant.get("catalogs"), "catalog_matches": ant.get("catalog_matches")},
        "babamul": {k: bab.get(k) for k in ("enabled", "found", "properties", "reliability", "cross_matches", "errors")}
        | {"ztf_match": {k: v for k, v in (bab.get("ztf_match") or {}).items() if k != "forced_phot"} or None},
    }


def snapshot(ev):
    dets = [p for p in ev["points"] if p["kind"] == "detection"]
    return {
        "n_lsst_det": sum(p["survey"] == "lsst" for p in dets),
        "n_ztf_det": sum(p["survey"] == "ztf" for p in dets),
        "n_forced": sum(p["kind"] == "forced" for p in ev["points"]),
        "first_mjd": min((p["mjd"] for p in dets), default=None),
        "last_mjd": max((p["mjd"] for p in dets), default=None),
        "fetched_at": ev["fetched_at"],
    }
