"""Fetch (with caching) everything about one object and split it into
evidence (always shown) and broker opinions (hidden until a blind label exists).
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from . import archival, crossmatch, db, switches
from .brokers import alerce, antares, babamul_client, fink, lasair, yse

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


def _light_curve(item):
    """ANTARES's locus; for a ZTF object whose locus can't be found (ANTARES's search down), ALeRCE's light curve."""
    if item["antares_locus"]:
        return antares.get_locus(item["antares_locus"])
    if not antares.is_ztf_object(item["object_id"]):
        return antares.get_locus(antares.locus_for_dia_object(item["object_id"])["locus_id"])
    try:
        loc = antares.locus_for_ztf_object(item["object_id"])
    except requests.RequestException:
        loc = None
    return antares.get_locus(loc["locus_id"]) if loc else alerce.ztf_payload(item["object_id"])


def _alerce(object_id, ant):
    lsst_id = None if antares.is_ztf_object(object_id) else object_id
    return alerce.fetch(lsst_id, ant.get("ztf_object_id"))


def _fink(object_id):
    return fink.fetch(None if antares.is_ztf_object(object_id) else object_id)


def _fetch_antares(item):
    data = _light_curve(item)
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


def load(object_id, refresh=False, off=frozenset()):
    """Fetch all sources for an object except those in off (a labeler's switches.off); concurrent callers share
    one fetch, so one waiting on another's may find a source it wants still missing until its next load."""
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
        fetches = {
            "alerce": lambda: _cached(object_id, "alerce", lambda: _alerce(object_id, ant), refresh),
            "fink": lambda: _cached(object_id, "fink", lambda: _fink(object_id), refresh),
            "babamul": lambda: _cached(object_id, "babamul", lambda: babamul_client.fetch(object_id), refresh),
            "xmatch": lambda: _xmatch(object_id, ant, off & switches.CATALOGS, refresh),
            "archival": lambda: _cached(object_id, "archival", lambda: archival.run(ant["ra"], ant["dec"]),
                                        refresh or _archival_failed(object_id)),
        }
        with ThreadPoolExecutor(max_workers=len(fetches)) as pool:
            for j in [pool.submit(fetch) for kind, fetch in fetches.items() if kind not in off]:
                j.result()
    finally:
        with _inflight_lock:
            _inflight.pop(object_id, None)
        ev.set()
    return _gather(object_id)


def _xmatch(object_id, ant, skip, refresh):
    """The object's shared crossmatch without the catalogs in skip. Catalogs an earlier load skipped (switched
    off for whoever loaded it) are filled in now unless skip has them too, as long as nobody has labeled the
    object: like "Retry failed catalogs", a labeled object's crossmatch stays the evidence it was labeled with."""
    hit = _cached(object_id, "xmatch", lambda: crossmatch.run(ant["ra"], ant["dec"], skip=skip), refresh)
    wanted = crossmatch.switched_off(hit["data"]) - skip
    if wanted and not db.query("SELECT 1 FROM labels WHERE object_id = ? LIMIT 1", (object_id,)):
        db.cache_put(object_id, "xmatch", crossmatch.fill_missing(hit["data"], wanted))


def _archival_failed(object_id):
    """A cached archival search with a survey that failed (e.g. IRSA down), long enough ago to try it again:
    not on every open, which would make even a cached object wait on the broken survey each time."""
    hit = db.cache_get(object_id, "archival")
    if hit is None or not hit["data"]["errors"]:
        return False
    age = datetime.now(timezone.utc) - datetime.fromisoformat(hit["fetched_at"])
    return age.total_seconds() > archival.DOWN_SECONDS


def tns_matches(object_id, ant):
    """The object's TNS matches, nearest first: its cached TNS match, fetched (and cached, as
    achievements.tns_for does) when ANTARES lists one that isn't cached yet."""
    from .tns import listed  # tns imports this module
    hit = db.cache_get(object_id, "tns")
    if hit is None and listed(ant):
        db.cache_put(object_id, "tns", antares.tns_matches(ant["locus_id"]))
        hit = db.cache_get(object_id, "tns")
    return sorted(hit["data"] if hit else [], key=lambda m: m["sep"] if m.get("sep") is not None else 99)


def _tns_names(object_id, ant):
    return [m["name"] for m in tns_matches(object_id, ant)]


def with_yse(object_id, raw, labeler, refresh=False):
    """raw plus labeler's own YSE PZ match ("yse"), fetched with their login and cached under their name."""
    login = yse.login_for(labeler)
    if not login:
        return raw | {"yse": {"data": {"enabled": False}, "fetched_at": None}}
    ant = raw["antares"]["data"]
    fetch = lambda: yse.fetch(ant["ra"], ant["dec"], login, _tns_names(object_id, ant))
    return raw | {"yse": _cached(object_id, yse.cache_kind(labeler), fetch, refresh)}


LASAIR_RETRY_SECONDS = 1800  # a failed or throttled Lasair fetch is tried again after this long


def with_lasair(object_id, raw, labeler, refresh=False):
    """raw plus Lasair's Sherlock classification ("lasair"), one cache entry per object whoever fetched it.
    Fetched with labeler's token when there's none yet, it failed a while ago, or refresh; without a token,
    whatever is cached, else {"enabled": False}. A rejected token's error is shown to its owner, not cached."""
    hit = db.cache_get(object_id, "lasair")
    token = lasair.token_for(labeler)
    if token and (refresh or hit is None or _lasair_failed(hit)):
        new = lasair.fetch(None if antares.is_ztf_object(object_id) else object_id, token)
        if new["errors"].get("login"):
            return raw | {"lasair": {"data": new, "fetched_at": None}}
        if new["lsst"] or hit is None or hit["data"]["errors"]:
            db.cache_put(object_id, "lasair", new)
            hit = db.cache_get(object_id, "lasair")
    return raw | {"lasair": hit or {"data": {"enabled": False}, "fetched_at": None}}


def _lasair_failed(hit):
    if not hit["data"]["errors"]:
        return False
    age = datetime.now(timezone.utc) - datetime.fromisoformat(hit["fetched_at"])
    return age.total_seconds() > LASAIR_RETRY_SECONDS


def with_profile(object_id, raw, labeler, refresh=False):
    """raw plus what labeler's own logins fetch, YSE PZ and Lasair, unless they switched it off."""
    off = switches.off(labeler)
    if "yse" not in off:
        raw = with_yse(object_id, raw, labeler, refresh)
    return raw if "lasair" in off else with_lasair(object_id, raw, labeler, refresh)


def load_antares(object_id):
    """Only the ANTARES payload (light curve, cutouts, catalogs), cached as load() caches it."""
    item = db.get_queue_item(object_id)
    return _cached(object_id, "antares", lambda: _fetch_antares(item))["data"]


# what load() fetches after ANTARES, as the vetter names them while they're still coming
SLOW = {"alerce": "ALeRCE", "fink": "Fink", "babamul": "Babamul", "xmatch": "crossmatch", "archival": "archival light curves"}


def quick(object_id, labeler):
    """What the vetter can show at once: the ANTARES light curve and cutouts (fetched if need be) and whatever
    else is already cached, plus the names of what isn't yet; load() and with_profile() fetch the rest."""
    load_antares(object_id)
    off = switches.off(labeler)
    raw = _gather(object_id)
    raw["yse"] = (db.cache_get(object_id, yse.cache_kind(labeler)) if yse.login_for(labeler)
                  else {"data": {"enabled": False}, "fetched_at": None})
    raw["lasair"] = db.cache_get(object_id, "lasair") or (None if lasair.token_for(labeler)
                                                          else {"data": {"enabled": False}, "fetched_at": None})
    pending = ([name for kind, name in SLOW.items() if raw[kind] is None and kind not in off]
               + (["YSE PZ"] if raw["yse"] is None and "yse" not in off else [])
               + (["Lasair"] if raw["lasair"] is None and "lasair" not in off else []))
    return raw, pending


def _gather(object_id):
    return {kind: db.cache_get(object_id, kind) for kind in ("antares", "alerce", "fink", "lasair", "babamul", "xmatch", "archival", "skybot", "positions")}


def fill_missing_xmatch(object_id):
    """Retry the catalogs that failed in an object's cached crossmatch. Returns the
    catalogs that came back; the cache is rewritten only when at least one did."""
    old = db.cache_get(object_id, "xmatch")["data"]
    new = crossmatch.fill_missing(old)
    filled = sorted(set(old["errors"]) - set(new["errors"]))
    if filled:
        db.cache_put(object_id, "xmatch", new)
    return filled


def rerun_xmatch(object_id, radius, skip=frozenset()):
    ant = db.cache_get(object_id, "antares")["data"]
    return _cached(object_id, "xmatch", lambda: crossmatch.run(ant["ra"], ant["dec"], radius, skip), refresh=True)


def skybot(object_id, refresh=False):
    ant = db.cache_get(object_id, "antares")["data"]
    mjds = [p["mjd"] for p in ant["points"] if p["kind"] == "detection"]
    return _cached(object_id, "skybot", lambda: crossmatch.run_skybot(ant["ra"], ant["dec"], mjds), refresh)


_prefetch_pool = ThreadPoolExecutor(max_workers=4)
_prefetching = set()


def prefetch(object_ids, labeler=None):
    """Load objects in the background, several at once and nearest first, so the
    next ones are cached before the vetter gets to them (with labeler's YSE PZ match, if given)."""
    for oid in object_ids:
        with _inflight_lock:
            if oid in _prefetching:
                continue
            _prefetching.add(oid)
        _prefetch_pool.submit(_prefetch_one, oid, labeler)


def _prefetch_one(object_id, labeler):
    try:
        raw = load(object_id, off=switches.off(labeler))
        if labeler:
            with_profile(object_id, raw, labeler)
    except Exception:
        pass
    finally:
        with _inflight_lock:
            _prefetching.discard(object_id)


_loading = set()  # objects load_in_background is loading


def load_in_background(object_id, labeler=None):
    """load() (and labeler's YSE PZ match) for the object on screen, in a thread of its own rather than
    queued behind the prefetches of the next ones; each source is cached as it lands, for quick() to show."""
    with _inflight_lock:
        if object_id in _loading:
            return
        _loading.add(object_id)

    def run():
        try:
            raw = load(object_id, off=switches.off(labeler))
            if labeler:
                with_profile(object_id, raw, labeler)
        except Exception:
            pass
        finally:
            with _inflight_lock:
                _loading.discard(object_id)

    threading.Thread(target=run, daemon=True).start()


def loading(object_id):
    with _inflight_lock:
        return object_id in _loading


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


def refresh_brokers(object_id, labeler=None):
    """Re-fetch broker data (not the crossmatch) after new alerts arrive, except brokers labeler switched off;
    YSE PZ's only for labeler."""
    item = db.get_queue_item(object_id)
    ant = _cached(object_id, "antares", lambda: _fetch_antares(item), refresh=True)["data"]
    off = switches.off(labeler)
    if "alerce" not in off:
        _cached(object_id, "alerce", lambda: _alerce(object_id, ant), refresh=True)
    if "fink" not in off:
        _cached(object_id, "fink", lambda: _fink(object_id), refresh=True)
    if "babamul" not in off:
        _cached(object_id, "babamul", lambda: babamul_client.fetch(object_id), refresh=True)
    if labeler:
        with_profile(object_id, _gather(object_id), labeler, refresh=True)
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
        # measurements of YSE PZ's nearest transient; its name, status and class are opinions
        "yse_photometry": ((raw.get("yse") or {}).get("data") or {}).get("photometry") or [],
        # whether HST, JWST, Chandra, Spitzer have imaged the position, as YSE PZ recorded it: facts, not opinions
        "yse_archives": yse.archives(((raw.get("yse") or {}).get("data") or {}).get("header")),
        "yse_url": ((raw.get("yse") or {}).get("data") or {}).get("url"),  # the labeler's YSE PZ page for it, if any
        # ZTF data releases and Pan-STARRS1 DR2 at the position (archival.py): total magnitudes
        "archival": (raw.get("archival") or {}).get("data") or {"points": [], "errors": {}},
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
        "fink": (raw.get("fink") or {}).get("data"),
        "antares": {"tags": ant.get("tags"), "catalogs": ant.get("catalogs"), "catalog_matches": ant.get("catalog_matches")},
        "babamul": {k: bab.get(k) for k in ("enabled", "off", "found", "properties", "reliability", "cross_matches", "errors")}
        | {"ztf_match": {k: v for k, v in (bab.get("ztf_match") or {}).items() if k != "forced_phot"} or None},
        "yse": _yse_opinion((raw.get("yse") or {}).get("data") or {}),
        "lasair": (raw.get("lasair") or {}).get("data"),
    }


def _yse_opinion(y):
    """YSE PZ's nearest match, with the header fields the vetter shows (its photometry is evidence)."""
    header = y.get("header") or {}
    return {k: y.get(k) for k in ("enabled", "off", "found", "matches", "tns_names", "url", "errors")} | {
        "fields": {k: header[k] for k in yse.HEADER_FIELDS if header.get(k) not in (None, "", "None", "[]")}}


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
