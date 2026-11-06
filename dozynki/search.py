"""Find one object by name, for the search box in the tab bar: a TNS name or anything a list line takes.

A TNS name (SN 2025abc, AT2025abc, 2025abc) is looked up first among the TNS matches already cached
(tns.py) and the names searched before (cache kind "search:tns"), then on TNS's public search for its position, where the nearest LSST diaObject within
TNS_RADIUS_ARCSEC is taken (lists._cone). Anything else (diaObjectId, lsst:<diaSourceId>, ANTARES locus,
coordinates) resolves as a line of an object list does (lists.resolve). An object already queued under
the name (a ZTF object id, say) is taken as it is.

Objects new to the queue get source "search", which keeps them out of the analysis pages' random sample.
Nothing TNS says beyond the position is passed on: its SN prefix and type are a classification, which
blind mode keeps hidden.
"""

import csv
import io
import json
import re
import time

import astropy.units as u
import requests
from astropy.coordinates import SkyCoord

from . import db, lists
from .config import HTTP_TIMEOUT

TNS_SEARCH = "https://www.wis-tns.org/search"
TNS_RADIUS_ARCSEC = 2.0  # TNS positions come from whichever survey reported first
# a prefix (SN, AT, TDE, ...) then year and letters; the letters run to three or four after the first 676 of a year
TNS_NAME = re.compile(r"^(?:[a-z]+[\s_-]*)?((?:19|20)\d{2}[a-z]{2,4})$", re.I)


def tns_name(q):
    """The bare TNS designation (2025abc) in q, or None if q isn't a TNS name (an ANTARES locus ANT2026... isn't)."""
    m = not lists.LOCUS.match(q.strip()) and TNS_NAME.match(q.strip())
    return m[1][:4] + m[1][4:].lower() if m else None


def _bare(name):
    return name.split()[-1] if name else None


class Unavailable(Exception):
    """TNS didn't answer with search results (it is down, or limiting requests)."""


def _cached_tns(name):
    """(object_id, match) of the queued object whose cached TNS match, or earlier search, is this name, nearest first."""
    found = []
    for r in db.query("SELECT object_id, payload FROM cache WHERE kind IN ('tns', 'search:tns') AND payload LIKE ?", (f'%{name}"%',)):
        found += [(m.get("sep") if m.get("sep") is not None else 99, r["object_id"], m)
                  for m in json.loads(r["payload"]) if _bare(m.get("name")) == name]
    for _, oid, m in sorted(found, key=lambda f: f[0]):
        if db.get_queue_item(oid):
            return oid, m
    return None


def tns_position(name):
    """(ra, dec) from TNS's public search; LookupError if TNS has no such object. An empty answer is asked
    again once, as TNS has been seen to give one for an object it has."""
    for attempt in range(2):
        r = requests.get(TNS_SEARCH, params={"name": name, "format": "csv"}, timeout=HTTP_TIMEOUT,
                         headers={"User-Agent": "DOZYNKI"})
        r.raise_for_status()
        rows = csv.DictReader(io.StringIO(r.text))
        if "Name" not in (rows.fieldnames or []):
            raise Unavailable("TNS didn't answer with search results; try again in a minute")
        for row in rows:
            if _bare(row.get("Name")) == name:
                c = SkyCoord(row["RA"], row["DEC"], unit=(u.hourangle, u.deg))
                return c.ra.deg, c.dec.deg
        if not attempt:
            time.sleep(2)
    raise LookupError(f"TNS has no object {name}")


def find(q):
    """{"object_id", "ra", "dec", "kind", ...} for what q names, queueing it if new; LookupError if nothing is found."""
    hit = _find(q.strip())
    item = db.get_queue_item(hit["object_id"])
    return {**hit, "ra": item["ra"], "dec": item["dec"]}


def _find(q):
    if not q:
        raise LookupError("nothing to search for")
    if db.get_queue_item(q):
        return {"object_id": q, "kind": "queued"}
    name = tns_name(q)
    if name:
        if hit := _cached_tns(name):
            return {"object_id": hit[0], "kind": "tns", "sep": hit[1].get("sep")}
        try:
            loc, sep, n = lists._cone(*tns_position(name), TNS_RADIUS_ARCSEC)
        except LookupError as err:
            raise LookupError(f"{q}: {err}") from None
        db.cache_put(str(loc["dia_object_id"]), "search:tns", [{"name": name, "sep": sep}])  # so TNS isn't asked again
        kind, extra = "tns", {"sep": sep, "n_within": n}
    else:
        e = lists.parse(q) or {"kind": "bad"}  # None for a line lists skip, like "# ..."
        r = lists.resolve(e, lists.DEFAULT_RADIUS_ARCSEC)
        loc, kind = r["loc"], r.get("kind", e["kind"])
        extra = {k: r[k] for k in ("sep", "n_within") if k in r}
    oid = str(loc["dia_object_id"])
    params = {"entry": q, "kind": kind, "unit": "object (LSST diaObject) found by name in the search box",
              **({"radius_arcsec": TNS_RADIUS_ARCSEC} if kind == "tns" else {})}
    db.add_to_queue(oid, loc["locus_id"], loc["ra"], loc["dec"], "search", params, summary=loc)
    return {"object_id": oid, "kind": kind, **extra}
