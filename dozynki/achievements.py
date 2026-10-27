"""Achievements: tiered counts per labeler, derived from labels and cached payloads.

Nothing is stored; every count is recomputed from the labels table, so deleting
or re-labeling an object moves the counts with it. An object counts once per
labeler, by that labeler's latest label of it (primary class only).

"Known" and "confirmed" mean a catalog in the independent crossmatch agrees
within SAME_SOURCE_ARCSEC (as in the vetter's source classes, static/app.js),
or for asteroids a pipeline SSO association or a SkyBoT match. There is no TNS
query yet: a SIMBAD/NED name with a TNS designation (SN/AT 20xxabc) or a
supernova/nova type stands in for it.

A night counts once its batches (sources.antares_night) have taken every object
of the night and you have vetted them all. A night sampled at several detection
minimums counts by the lowest one.

Filter achievements live in the filter lab, which keeps its pipelines in the
browser, so the server lists them with count None and home.js fills them in.
"""

import json
import re
from collections import defaultdict

from . import db, sources

SAME_SOURCE_ARCSEC = 1.0

TNS_NAME = re.compile(r"^(SN|AT)\s?\d{4}[a-z]+", re.I)
SIMBAD_TRANSIENT = {"SN*", "SN", "No*", "Transient", "ev"}
SIMBAD_AGN = {"QSO", "AGN", "AG", "Sy1", "Sy2", "SyG", "Bla", "BLL", "LIN", "LeQ", "Q"}
NED_AGN = re.compile(r"^(QSO|AGN|Q_Lens|BLLac|Sy)")
# Rare transients a catalog has typed: tidal disruption events and novae.
SIMBAD_RARE = {"TDE", "No*"}
NED_RARE = {"TDE", "Nova"}

# (key, category, name, what counts, tiers); a category's order is its order on the page.
ACHIEVEMENTS = [
    ("vetted", "vetting", "Harvest Moon", "objects vetted", [100, 1000, 5000, 10000]),
    ("stars", "vetting", "Counting Stars", "objects labeled star", [10, 100, 1000, 5000]),
    ("bogus", "vetting", "It Ain't Much, But It's Honest Work", "objects labeled bogus", [10, 100, 1000, 5000]),
    ("transients", "vetting", "Champagne Supernova", "transients confirmed by a known SN / TNS name", [1, 5, 25, 100]),
    ("rare", "vetting", "Once in a Lifetime", "transients confirmed as a TDE or nova by SIMBAD or NED", [1, 3, 10]),
    ("agn", "vetting", "Black Hole Sun", "AGN confirmed by Milliquas, SIMBAD or NED", [1, 10, 50, 250]),
    ("sso", "vetting", "Like a Rolling Stone", "asteroids confirmed by an SSO association or SkyBoT", [1, 10, 50, 250]),
    ("hosts", "vetting", "Our House", "host galaxies associated", [10, 100, 500, 2000]),
    ("nights", "vetting", "All Night Long", "sampled nights with every object vetted", [1, 5, 10, 30]),
    ("first_filter", "filtering", "The First Cut Is the Deepest", "filter lab pipelines changed from the default", [1]),
]
CATEGORIES = ["vetting", "filtering"]


def _near(rows):
    return [r for r in rows or [] if r.get("sep") is not None and r["sep"] <= SAME_SOURCE_ARCSEC]


def known_transient(xm):
    simbad = any(TNS_NAME.match(str(r.get("main_id"))) or r.get("otype") in SIMBAD_TRANSIENT for r in _near(xm.get("simbad")))
    ned = any(TNS_NAME.match(str(r.get("name"))) or r.get("type") in ("SN", "Nova") for r in _near(xm.get("ned")))
    return simbad or ned


def needle(xm):
    """The catalog match that makes a transient a find, rare ones first: {kind, name, type} or None."""
    simbad = [(r.get("main_id"), r.get("otype")) for r in _near(xm.get("simbad"))]
    ned = [(r.get("name"), r.get("type")) for r in _near(xm.get("ned"))]
    for name, kind in simbad + ned:
        if kind in SIMBAD_RARE | NED_RARE:
            return {"kind": "rare", "name": name, "type": kind}
    for name, kind in simbad:
        if TNS_NAME.match(str(name)) or kind in SIMBAD_TRANSIENT:
            return {"kind": "transient", "name": name, "type": kind}
    for name, kind in ned:
        if TNS_NAME.match(str(name)) or kind in ("SN", "Nova"):
            return {"kind": "transient", "name": name, "type": kind}
    return None


def known_rare(xm):
    return (any(r.get("otype") in SIMBAD_RARE for r in _near(xm.get("simbad")))
            or any(r.get("type") in NED_RARE for r in _near(xm.get("ned"))))


def known_agn(xm):
    return bool(_near(xm.get("milliquas"))
                or any(r.get("otype") in SIMBAD_AGN for r in _near(xm.get("simbad")))
                or any(NED_AGN.match(str(r.get("type") or "")) for r in _near(xm.get("ned"))))


def known_sso(antares, skybot):
    pipeline = any(p.get("ss_object_id") or p.get("ss_name") for p in (antares or {}).get("points", []))
    return pipeline or any((skybot or {}).get(epoch) for epoch in ("first", "last"))


def _payloads(object_ids, kind):
    if not object_ids:
        return {}
    ids = list(object_ids)
    rows = db.query(f"SELECT object_id, payload FROM cache WHERE kind = ? AND object_id IN ({','.join('?' * len(ids))})",
                    (kind, *ids))
    return {r["object_id"]: json.loads(r["payload"]) for r in rows}


def nights(labeler, vetted=None):
    """Sampled nights, one row per (night, detection minimum), lowest minimum first."""
    if vetted is None:
        vetted = {r["object_id"] for r in db.query(
            "SELECT DISTINCT object_id FROM labels WHERE COALESCE(labeler, 'anonymous') = ?", (labeler,))}
    groups = defaultdict(list)
    for p in db.night_pulls():
        groups[(p["night"], p["filters"].get("min_det") or 1)].append(p)
    out = []
    for (night, min_det), batches in sorted(groups.items()):
        ids = list(dict.fromkeys(o for b in batches for o in b["object_ids"]))  # a night pulled twice repeats them
        size = batches[-1]["n_matching"] or 0
        n_vetted = sum(o in vetted for o in ids)
        out.append({"night": night, "min_det": min_det, "taken": len(ids), "vetted": n_vetted, "n_matching": size,
                    "capped": size >= sources.ES_WINDOW,
                    "done": 0 < size < sources.ES_WINDOW and len(ids) >= size and n_vetted == len(ids)})
    return out


def counts(labeler):
    """{achievement key: count} for one labeler (filter achievements excluded)."""
    latest = db.query(
        "SELECT object_id, klass, host FROM labels WHERE id IN "
        "(SELECT MAX(id) FROM labels WHERE COALESCE(labeler, 'anonymous') = ? GROUP BY object_id)",
        (labeler,),
    )
    by_class = defaultdict(set)
    for r in latest:
        by_class[r["klass"]].add(r["object_id"])
    xm = _payloads(by_class["transient"] | by_class["agn"], "xmatch")
    sso_ant, sso_sb = _payloads(by_class["sso"], "antares"), _payloads(by_class["sso"], "skybot")

    vetted = {r["object_id"] for r in latest}
    broadest = {}
    for row in nights(labeler, vetted):
        broadest.setdefault(row["night"], row)  # rows come lowest detection minimum first

    return {
        "vetted": len(latest),
        "stars": len(by_class["star"]),
        "bogus": len(by_class["bogus"]),
        "transients": sum(known_transient(xm.get(o, {})) for o in by_class["transient"]),
        "rare": sum(known_rare(xm.get(o, {})) for o in by_class["transient"]),
        "agn": sum(known_agn(xm.get(o, {})) for o in by_class["agn"]),
        "sso": sum(known_sso(sso_ant.get(o), sso_sb.get(o)) for o in by_class["sso"]),
        "hosts": sum(bool(r["host"]) and not json.loads(r["host"]).get("hostless") for r in latest),
        "nights": sum(row["done"] for row in broadest.values()),
    }


def summary(labeler):
    """Every achievement with its count, tiers earned and the next goal."""
    have = counts(labeler)
    out = []
    for key, category, name, what, tiers in ACHIEVEMENTS:
        n = have.get(key)
        earned = sum(n >= t for t in tiers) if n is not None else 0
        out.append({"key": key, "category": category, "name": name, "what": what, "tiers": tiers,
                    "count": n, "earned": earned, "next": tiers[earned] if earned < len(tiers) else None})
    return out


def newly_earned(before, after):
    """Achievements that gained a tier between two summaries."""
    was = {a["key"]: a["earned"] for a in before}
    return [a for a in after if a["earned"] > was.get(a["key"], 0)]
