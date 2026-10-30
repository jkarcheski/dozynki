"""TNS through ANTARES: its match of LSST loci to TNS public objects, as a classification and an object list.

ANTARES matches every locus against its own copy of TNS public objects (antares.tns_matches), so nothing
here downloads TNS. An object's matches are cached as kind "tns" (achievements.tns_for, check_tns, and
build_list below). tns_class() maps a TNS type onto the DOZYNKI taxonomy (labels.py): a class and, for
transients, a subclass. Most TNS objects are never classified ("AT" names with no type); those map to nothing.

build_list() makes an object list (lists.py) of the LSST loci ANTARES matched to TNS, or only the
classified ones. TNS reports are mostly bright supernovae, and classified ones even more so, so the list
is a sample of what TNS reports, not of the LSST stream. Being a list, it stays out of the analysis pages'
random sample unless chosen as the population.
"""

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from . import assemble, db
from .brokers import antares

CATALOG = "tns_public_objects"  # ANTARES's catalog name, in a locus's catalogs
LIST_ALL = "TNS (ANTARES match)"
LIST_CLASSIFIED = "TNS classified (ANTARES match)"
WORKERS = 8

# TNS types -> (DOZYNKI class, transient subclass), first match wins. Gap transients (ILRT, LRN, LBV
# eruptions, SN impostors) count as SN-like; M-dwarf flares go with CVs and novae. Galaxy, Other,
# Std-spec, FRB, Light-Echo and the simulated Computed-* types map to nothing.
TNS_TYPES = [
    (r"^TDE", "transient", "nuclear"),
    (r"^(Nova|CV|M dwarf)", "transient", "cv_nova"),
    (r"^(Kilonova|FBOT|GRB|Afterglow|Microlensing)", "transient", "fast"),
    (r"^(SN|SLSN|ILRT|LRN|LBV|Impostor|Gap)", "transient", "sn_like"),
    (r"^(AGN|QSO|Blazar|NLS1)", "agn", None),
    (r"^(Varstar|WR|YSO)", "star", None),
]
_TNS_TYPES = [(re.compile(p, re.I), k, s) for p, k, s in TNS_TYPES]


def tns_class(tns_type):
    """(DOZYNKI class, transient subclass) for a TNS type; (None, None) if unclassified or unmapped."""
    for pattern, klass, subclass in _TNS_TYPES:
        if tns_type and pattern.match(tns_type):
            return klass, subclass
    return None, None


def best(matches):
    """The match that speaks for an object: the nearest classified one, else the nearest."""
    by_sep = sorted(matches or [], key=lambda m: m["sep"] if m.get("sep") is not None else 99)
    return next((m for m in by_sep if m.get("type")), by_sep[0] if by_sep else None)


def listed(antares_data):
    """Whether ANTARES lists a TNS match on the locus (its cached payload's catalogs)."""
    return CATALOG in ((antares_data or {}).get("catalogs") or [])


def summary(match):
    """What the analysis pages show of a match: name, TNS type, and the class it maps to."""
    klass, subclass = tns_class(match.get("type"))
    return {"name": match["name"], "type": match.get("type"), "klass": klass, "subclass": subclass,
            "sep": match.get("sep"), "discovered": match.get("discovered")}


def cached():
    """{object_id: best match} for every object with a cached TNS match."""
    out = {}
    for r in db.query("SELECT object_id, payload FROM cache WHERE kind = 'tns'"):
        m = best(json.loads(r["payload"]))
        if m:
            out[r["object_id"]] = m
    return out


# ---------- the TNS object list ----------

_lock = threading.Lock()
_state = {"running": False}


def status():
    return dict(_state)


def start(classified_only=True):
    """Build the list in the background, unless a build is going."""
    with _lock:
        if _state["running"]:
            return False
        _state.clear()
        _state.update(running=True, classified_only=classified_only, phase="listing", started_at=db.now())
    threading.Thread(target=_build, args=(classified_only,), daemon=True).start()
    return True


def _matches(loc):
    try:
        return antares.tns_matches(loc["locus_id"])
    except Exception:
        return None


def _fetch(object_id):
    """The cached ANTARES payload is all the similar page and the filter rules need to place an object;
    the rest (ALeRCE, crossmatch) is fetched when it is opened in the vetter."""
    try:
        assemble.load_antares(object_id)
        return True
    except Exception:
        return False


def _build(classified_only):
    from . import nightrun  # imports the filter rules, which this module doesn't otherwise need

    name = LIST_CLASSIFIED if classified_only else LIST_ALL
    try:
        filters = [{"exists": {"field": "properties.survey.lsst.dia_object_id"}}, {"term": {"catalogs": CATALOG}}]
        loci = nightrun._list(filters, nightrun._slices(filters))
        _state.update(phase="matching", listed=len(loci), checked=0)
        found, errors = [], 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for loc, matches in zip(loci, pool.map(_matches, loci)):
                _state["checked"] += 1
                if matches is None:
                    errors += 1
                    continue
                oid = str(loc["dia_object_id"])
                db.cache_put(oid, "tns", matches)
                m = best(matches)
                if m and (m.get("type") or not classified_only):
                    found.append((oid, loc, m))
        members = []
        for oid, loc, m in found:
            params = {"list": name, "entry": m["name"], "kind": "antares_tns", "tns_type": m.get("type"),
                      "es_query": filters, "classified_only": classified_only,
                      "unit": "object (LSST diaObject) ANTARES matched to a TNS public object"
                              + (" with a TNS classification" if classified_only else "")}
            members.append(((oid, loc["locus_id"], loc["ra"], loc["dec"], "object_list", params, loc), m["name"], "antares_tns", m.get("sep")))
        note = ("LSST objects ANTARES matched to a TNS public object" + (" that TNS has classified" if classified_only else "")
                + ". Built by the TNS button in the vetter's list dialog (tns.py).")
        added, queued = db.add_list_members(name, None, note, members) if members else (0, 0)
        _state.update(phase="fetching", list=name, found=len(members), added=added, queued=queued, match_errors=errors, fetched=0)
        need = [oid for oid, _, _ in found if db.cache_get(oid, "antares") is None]
        _state["to_fetch"] = len(need)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for ok in pool.map(_fetch, need):
                _state["fetched"] += ok
        _state.update(phase="done", finished_at=db.now())
    except Exception as e:
        _state.update(phase="error", error=f"{type(e).__name__}: {e}")
    finally:
        _state["running"] = False
