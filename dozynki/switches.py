"""Each labeler's data-source switches: a source switched off is neither shown to them nor fetched for them.

Caches shared between labelers stay complete for the others. A broker or the archival search that is off
for one labeler simply isn't fetched, so the next labeler who wants it fetches it. A crossmatch catalog is
one part of the object's shared crossmatch, so it is recorded there as switched off (crossmatch.OFF) and
filled in by the next load that wants it (assemble.load), including every load without switches (screening).
"""

import json

from . import db

# key -> (group, label), in the order the vetter lists them; crossmatch keys are crossmatch._jobs' catalogs
SOURCES = {
    "yse": ("YSE PZ", "YSE PZ"),
    "archival": ("Archival light curves", "ZTF data releases + Pan-STARRS1 DR2"),
    "alerce": ("Brokers", "ALeRCE"),
    "fink": ("Brokers", "Fink"),
    "lasair": ("Brokers", "Lasair"),
    "babamul": ("Brokers", "Babamul"),
    "legacy_survey": ("Crossmatch", "Legacy Survey DR10"),
    "gaia": ("Crossmatch", "Gaia DR3"),
    "ps1": ("Crossmatch", "Pan-STARRS1"),
    "milliquas": ("Crossmatch", "Milliquas"),
    "simbad": ("Crossmatch", "SIMBAD"),
    "ned": ("Crossmatch", "NED"),
    "ztf": ("Crossmatch", "ZTF objects (ALeRCE)"),
}
CATALOGS = frozenset(k for k, (group, _) in SOURCES.items() if group == "Crossmatch")
BROKERS = ("alerce", "fink", "lasair", "babamul")


def listing():
    return [{"key": k, "group": g, "label": label} for k, (g, label) in SOURCES.items()]


def off(labeler):
    """The sources labeler switched off (empty for no labeler, e.g. screening)."""
    if not labeler:
        return frozenset()
    rows = db.query("SELECT off FROM source_switches WHERE labeler = ?", (labeler,))
    return frozenset(json.loads(rows[0]["off"])) & SOURCES.keys() if rows else frozenset()


def set_off(labeler, keys):
    unknown = set(keys) - SOURCES.keys()
    if unknown:
        raise ValueError(f"unknown sources: {', '.join(sorted(unknown))}")
    db.execute("INSERT OR REPLACE INTO source_switches (labeler, off) VALUES (?, ?)",
               (labeler, json.dumps(sorted(set(keys)))))
    return off(labeler)


_OFF_DATA = {"data": {"enabled": False, "off": True}, "fetched_at": None}


def hide(raw, keys):
    """raw as the vetter shows it to someone with keys switched off: those sources replaced by an "off" marker,
    switched-off catalogs taken out of the crossmatch (listed in its "off")."""
    if not keys:
        return raw
    out = dict(raw)
    for kind in ("yse", *BROKERS):
        if kind in keys:
            out[kind] = _OFF_DATA
    if "archival" in keys:
        out["archival"] = {"data": {"points": [], "errors": {}, "off": True}, "fetched_at": None}
    xm = (raw.get("xmatch") or {}).get("data")
    cats = keys & CATALOGS
    if xm and cats:
        xm = {**xm, **{c: None for c in cats}, "errors": {k: v for k, v in xm.get("errors", {}).items() if k not in cats},
              "off": sorted(cats)}
        if "legacy_survey" in cats:
            xm["host_candidates"] = []
        out["xmatch"] = {**raw["xmatch"], "data": xm}
    return out
