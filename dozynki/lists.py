"""Object lists: any set of LSST objects, loaded by ID or position, to vet, filter, map and compare.

Each line of a list names one object, as any of
- an ANTARES alert ID: lsst:<diaSourceId>, or ztf_candidate:<candid> (its position is then looked up as below)
- an LSST diaObjectId, or a bare diaSourceId (tried as a diaObjectId first)
- an ANTARES locus ID (ANT2026...)
- coordinates: RA and Dec in degrees, or sexagesimal (03:37:23.3 -28:40:12.7, 03h37m23.3s -28d40m12.7s,
  or 03 37 23.3 -28 40 12.7). The nearest LSST diaObject within the radius is taken, from ALeRCE's
  cone search (ANTARES v2's sky_distance query fails on its side).
Columns after the object (a name, a note) are ignored, as are blank lines and lines starting with #.
Objects must be in ANTARES, which the vetter and the filter rules read.

A list is stored as membership (db list_members) next to the queue, so an object on several lists, or
already sampled, is one queue row; objects new to the queue get source "object_list".
"""

import math
import re
from concurrent.futures import ThreadPoolExecutor

import astropy.units as u
import requests
from astropy.coordinates import SkyCoord

from . import db
from .brokers import alerce, antares

MAX_ENTRIES = 5000
DEFAULT_RADIUS_ARCSEC = 1.0
MAX_RADIUS_ARCSEC = 30.0

LSST_ALERT = re.compile(r"^lsst:(\d+)$", re.I)
ZTF_ALERT = re.compile(r"^ztf_candidate:(\d+)$", re.I)
LOCUS = re.compile(r"^ANT\d{4}[a-z0-9]+$")
ID = re.compile(r"^\d{6,}$")  # shorter integers are degrees
NUMBER = re.compile(r"^[+-]?\d+(\.\d*)?$")
SEXAGESIMAL = re.compile(r"^[+-]?\d+[:hd]\d+[:md]\d+(\.\d*)?s?$", re.I)


def _coord(ra, dec):
    try:
        c = SkyCoord(ra, dec, unit=(u.deg, u.deg)) if NUMBER.match(ra) else SkyCoord(ra, dec, unit=(u.hourangle, u.deg))
    except ValueError:
        return None
    return c.ra.deg, c.dec.deg


def parse(line):
    """One line as {"entry", "kind", ...}, or None for a blank or comment line. kind "bad" if unreadable."""
    entry = line.strip()
    if not entry or entry.startswith("#"):
        return None
    t = [x for x in re.split(r"[\s,;|]+", entry) if x]
    first = t[0].strip("\"'")
    if m := LSST_ALERT.match(first):
        return {"entry": entry, "kind": "lsst_alert", "id": m[1]}
    if m := ZTF_ALERT.match(first):
        return {"entry": entry, "kind": "ztf_alert", "id": m[1]}
    if LOCUS.match(first):
        return {"entry": entry, "kind": "locus", "id": first}
    if ID.match(first):
        return {"entry": entry, "kind": "dia_object", "id": first}
    pos = None
    if len(t) >= 2 and (NUMBER.match(t[0]) and NUMBER.match(t[1]) or SEXAGESIMAL.match(t[0]) and SEXAGESIMAL.match(t[1])):
        pos = _coord(t[0], t[1])
        if pos and len(t) >= 6 and all(NUMBER.match(x) for x in t[:6]) and "." not in t[0] + t[1]:
            pos = _coord(" ".join(t[:3]), " ".join(t[3:6]))  # hh mm ss dd mm ss
    if pos and 0 <= pos[0] < 360 and -90 <= pos[1] <= 90:
        return {"entry": entry, "kind": "coords", "ra": pos[0], "dec": pos[1]}
    return {"entry": entry, "kind": "bad"}


def _sep_arcsec(ra1, dec1, ra2, dec2):
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    h = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1, math.sqrt(h)))) * 3600


def _in_antares(dia_object_id, missing_ok=False):
    """ANTARES's listing summary of a diaObject. While ANTARES's search is failing (its locus and alert pages
    may still work), ALeRCE's position stands in and the locus is looked up when the object is opened."""
    try:
        loc = antares.locus_for_dia_object(dia_object_id)
    except requests.HTTPError:
        try:
            ra, dec = alerce.lsst_position(dia_object_id)
        except Exception:
            raise LookupError("ANTARES's search is failing and ALeRCE doesn't have this object")
        return {"locus_id": None, "ra": ra, "dec": dec, "dia_object_id": str(dia_object_id)}
    if not loc and not missing_ok:
        raise LookupError(f"diaObject {dia_object_id} is not in ANTARES")
    return loc


def _cone(ra, dec, radius):
    found = sorted((_sep_arcsec(ra, dec, r, d), oid) for oid, r, d in alerce.lsst_cone(ra, dec, radius))
    found = [(s, oid) for s, oid in found if s <= radius]
    if not found:
        raise LookupError(f"no LSST object within {radius:g}\"")
    sep, oid = found[0]
    return _in_antares(oid), sep, len(found)


def resolve(e, radius):
    """{"loc": ANTARES listing summary, "sep", "n_within"} for one parsed entry; raises LookupError if not found."""
    kind = e["kind"]
    if kind == "bad":
        raise LookupError("not an ID or coordinates")
    if kind == "coords":
        loc, sep, n = _cone(e["ra"], e["dec"], radius)
        return {"loc": loc, "sep": sep, "n_within": n}
    if kind == "dia_object":
        try:
            loc = _in_antares(e["id"], missing_ok=True)
        except LookupError:
            loc = None
        if loc:
            return {"loc": loc}
        props = antares.alert(f"lsst:{e['id']}")  # a bare diaSourceId
        if not props:
            raise LookupError("no LSST diaObject or alert with this ID in ANTARES")
        return {"loc": _in_antares(props["lsst_diaSource_diaObjectId"]), "kind": "lsst_alert"}
    if kind == "lsst_alert":
        props = antares.alert(f"lsst:{e['id']}")
        if not props:
            raise LookupError("no such alert in ANTARES")
        return {"loc": _in_antares(props["lsst_diaSource_diaObjectId"])}
    if kind == "ztf_alert":
        props = antares.alert(f"ztf_candidate:{e['id']}")
        if not props:
            raise LookupError("no such alert in ANTARES")
        loc, sep, n = _cone(props["ztf_ra"], props["ztf_dec"], radius)
        return {"loc": loc, "sep": sep, "n_within": n}
    if kind == "locus":
        try:
            loc = antares.locus_summary(e["id"])
        except Exception:
            raise LookupError("no such locus in ANTARES")
        if not loc["dia_object_id"]:
            raise LookupError("this locus has no LSST diaObject")
        return {"loc": loc}
    raise ValueError(kind)


def _try(e, radius):
    try:
        return resolve(e, radius), None
    except LookupError as err:
        return None, str(err)
    except Exception as err:  # a broker that errors or times out: report it with the line, keep going
        return None, f"lookup failed ({type(err).__name__})"


def load(name, text, radius=DEFAULT_RADIUS_ARCSEC, owner=None, note=None):
    """Resolve every line of text and add what is found to the list `name` (created if new)."""
    entries = [e for e in map(parse, text.splitlines()) if e]
    if not entries:
        raise ValueError("no objects in the list")
    if len(entries) > MAX_ENTRIES:
        raise ValueError(f"at most {MAX_ENTRIES} lines at a time")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda e: _try(e, radius), entries))
    members, missing, seen, crowded = [], [], {}, []
    for e, (r, err) in zip(entries, results):
        if err:
            missing.append({"entry": e["entry"], "reason": err})
            continue
        loc = r["loc"]
        oid = str(loc["dia_object_id"])
        if oid in seen:
            seen[oid].append(e["entry"])
            continue
        seen[oid] = [e["entry"]]
        if r.get("n_within", 1) > 1:
            crowded.append({"entry": e["entry"], "object_id": oid, "n_within": r["n_within"]})
        kind = r.get("kind", e["kind"])
        params = {"list": name, "entry": e["entry"], "kind": kind,
                  "unit": "object (LSST diaObject) named in a user-supplied list"}
        if kind in ("coords", "ztf_alert"):
            params["radius_arcsec"] = radius
        members.append(((oid, loc["locus_id"], loc["ra"], loc["dec"], "object_list", params, loc),
                        e["entry"], kind, r.get("sep")))
    added, queued = db.add_list_members(name, owner, note, members)
    return {
        "name": name, "lines": len(entries), "found": len(members), "added": added, "queued": queued,
        "missing": missing,
        # several lines naming one object (two alerts of it, or positions a few tenths of an arcsec apart)
        "duplicates": [{"object_id": oid, "entries": es} for oid, es in seen.items() if len(es) > 1],
        # positions with more than one LSST object within the radius: the nearest was taken
        "crowded": crowded,
        "object_ids": [m[0][0] for m in members],
    }
