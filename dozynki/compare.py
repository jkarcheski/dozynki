"""Confusion matrices between two sets of classifications of the same objects.

A *source* assigns at most one class to each object:
  label:blind:<labeler>          that labeler's first blind label
  label:final:<labeler>          that labeler's latest label (any phase)
  alerce:<survey>:<classifier>   ALeRCE's top class from the cached payload

Each source keeps its own class names (ALeRCE says "SN", not "transient"), so the
matrix need not be square. Agreement is decided after mapping both sides onto the
DOZYNKI taxonomy (labels.py) with ALERCE_CLASSES, or onto a coarser grouping:
real vs not real, or transient vs not transient. Classes that do not map
("unsure", unknown ALeRCE classes) are left out and counted.

Only objects both sources classify are compared. Like evaluate.py, only the
uniform random sample is used unless all_sources is set, so agreement rates
estimate the LSST stream within the sampling filters.
"""

import json
from collections import Counter

from . import db
from .evaluate import RANDOM_SOURCES, wilson
from .labels import CLASS_BY_KEY, CLASSES

# ALeRCE classes -> DOZYNKI classes. Stamp classifiers first; satellites are not
# astrophysical, so for real/bogus purposes they count with the artifacts. Then the
# ZTF light-curve classifiers: CV/nova, TDE and microlensing are DOZYNKI transients,
# YSOs count as variable stars, and "Stochastic" (AGN, YSO and CVs together) maps to nothing.
ALERCE_CLASSES = {
    "bogus": "bogus", "satellite": "bogus", "asteroid": "sso", "VS": "star", "AGN": "agn", "SN": "transient",
    **dict.fromkeys(["SNIa", "SNIbc", "SNII", "SNIIn", "SESN", "SLSN", "TDE", "CV/Nova", "Microlensing", "Transient"], "transient"),
    **dict.fromkeys(["QSO", "Blazar"], "agn"),
    **dict.fromkeys(["CEP", "DSCT", "E", "EA", "EB/EW", "LPV", "RRL", "RRLab", "RRLc", "RSCVn", "Periodic-Other", "Periodic", "YSO"], "star"),
}

VIEWS = {
    "class": None,
    "real": lambda k: {True: "real", False: "not real"}.get(CLASS_BY_KEY[k]["real"]),
    "transient": lambda k: {True: "transient", False: "not transient"}.get(CLASS_BY_KEY[k]["transient"]),
}
VIEW_ORDER = {"real": ["real", "not real"], "transient": ["transient", "not transient"]}
CLASS_ORDER = [c["key"] for c in CLASSES]
# "unsure" is a label but not a class anything can agree with
MAPPABLE = {c["key"] for c in CLASSES if c["real"] is not None}


def sources():
    out = []
    for r in db.query("SELECT labeler, COUNT(DISTINCT object_id) n, SUM(phase = 'blind') n_blind FROM labels GROUP BY labeler"):
        who = r["labeler"] or "anonymous"
        out.append({"id": f"label:blind:{who}", "label": f"{who} · first blind", "kind": "label", "n": r["n_blind"]})
        out.append({"id": f"label:final:{who}", "label": f"{who} · latest", "kind": "label", "n": r["n"]})
    seen = Counter()
    versions = {}
    for r in db.query("SELECT payload FROM cache WHERE kind = 'alerce'"):
        data = json.loads(r["payload"])
        for survey in ("lsst", "ztf"):
            for clf, entry in (data.get(survey) or {}).items():
                seen[(survey, clf)] += 1
                versions.setdefault((survey, clf), set()).add(str(entry.get("version")))
    for (survey, clf), n in sorted(seen.items()):
        out.append({"id": f"alerce:{survey}:{clf}", "label": f"ALeRCE {survey.upper()} · {clf}", "kind": "alerce", "n": n,
                    "versions": sorted(versions[(survey, clf)])})
    return out


def load(source):
    """{object_id: {"cls": native class, "mapped": DOZYNKI class or None, "alt": [...], "p": prob}}"""
    kind, a, b = (source.split(":", 2) + ["", ""])[:3]
    if kind == "label":
        if a == "blind":
            sql = "SELECT * FROM labels WHERE id IN (SELECT MIN(id) FROM labels WHERE phase = 'blind' AND labeler IS ? GROUP BY object_id)"
        elif a == "final":
            sql = "SELECT * FROM labels WHERE id IN (SELECT MAX(id) FROM labels WHERE labeler IS ? GROUP BY object_id)"
        else:
            raise ValueError(f"unknown label phase {a!r}")
        return {r["object_id"]: {"cls": r["klass"], "mapped": r["klass"] if r["klass"] in MAPPABLE else None,
                                 "subclass": r["subclass"], "alt": json.loads(r.get("alt_classes") or "[]"), "p": None}
                for r in db.query(sql, (b,))}
    if kind == "alerce":
        out = {}
        for r in db.query("SELECT object_id, payload FROM cache WHERE kind = 'alerce'"):
            entry = (json.loads(r["payload"]).get(a) or {}).get(b)
            if entry and entry.get("top"):
                top = entry["top"]
                out[r["object_id"]] = {"cls": top, "mapped": ALERCE_CLASSES.get(top), "subclass": None, "alt": [],
                                       "p": entry["probs"].get(top), "probs": entry["probs"]}
        return out
    raise ValueError(f"unknown source {source!r}")


def _order(keys, mapped):
    """Native classes in taxonomy order of what they map to, unmapped last."""
    rank = lambda k: (CLASS_ORDER.index(mapped[k]) if mapped.get(k) in CLASS_ORDER else len(CLASS_ORDER), k)
    return sorted(keys, key=rank)


def kappa(pairs):
    """Cohen's kappa for a list of (ref, comp) category pairs."""
    n = len(pairs)
    if not n:
        return None
    po = sum(r == c for r, c in pairs) / n
    rc, cc = Counter(r for r, _ in pairs), Counter(c for _, c in pairs)
    pe = sum(rc[k] * cc[k] for k in rc) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)


def compare(ref, comp, view="class", min_prob=0.0, include_alt=False, all_sources=False):
    if view not in VIEWS:
        raise ValueError(f"view must be one of {list(VIEWS)}")
    group = VIEWS[view]
    R, C = load(ref), load(comp)
    queue = {r["object_id"]: r["source"] for r in db.query("SELECT object_id, source FROM queue")}
    in_pop = lambda oid: all_sources or queue.get(oid) in RANDOM_SOURCES
    excluded = Counter()
    excluded["only in reference"] = sum(1 for o in R if o not in C and in_pop(o))
    excluded["only in compared"] = sum(1 for o in C if o not in R and in_pop(o))

    def key(entry):
        """Matrix row/column key and the category agreement is judged on."""
        if entry["mapped"] is None:
            return None, None
        if group is None:
            return entry["cls"], entry["mapped"]
        g = group(entry["mapped"])
        return g, g

    objects = []
    for oid in sorted(set(R) & set(C)):
        if not in_pop(oid):
            continue
        r, c = R[oid], C[oid]
        for side, e in (("reference", r), ("compared", c)):
            if e["mapped"] is None or (group and group(e["mapped"]) is None):
                excluded[f"{e['cls']} ({side})"] += 1
                break
        else:
            if any(e["p"] is not None and e["p"] < min_prob for e in (r, c)):
                excluded[f"top probability < {min_prob:g}"] += 1
                continue
            (rk, rcat), (ck, ccat) = key(r), key(c)
            alts = {r["mapped"], *r["alt"]} & MAPPABLE if include_alt else {r["mapped"]}
            calt = {c["mapped"], *c["alt"]} & MAPPABLE if include_alt else {c["mapped"]}
            if group:
                alts, calt = {group(a) for a in alts}, {group(a) for a in calt}
            objects.append({"object_id": oid, "row": rk, "col": ck, "ref_cat": rcat, "comp_cat": ccat,
                            "agree": rcat == ccat, "agree_alt": bool(alts & calt),
                            "ref_subclass": r["subclass"], "ref_alt": r["alt"], "comp_alt": c["alt"],
                            "ref_p": r["p"], "comp_p": c["p"], "comp_probs": c.get("probs"), "ref_probs": r.get("probs")})
    if not objects:
        raise ValueError("no objects classified by both sources")

    if group is None:
        rmap = {o["row"]: o["ref_cat"] for o in objects}
        cmap = {o["col"]: o["comp_cat"] for o in objects}
        rows, cols = _order(rmap, rmap), _order(cmap, cmap)
        cats = [k for k in CLASS_ORDER if k in set(rmap.values()) | set(cmap.values())]
    else:
        rows = cols = cats = [k for k in VIEW_ORDER[view] if any(k in (o["row"], o["col"]) for o in objects)]
        rmap = cmap = {k: k for k in cats}
    matrix = [[sum(o["row"] == r and o["col"] == c for o in objects) for c in cols] for r in rows]

    n = len(objects)
    agree = sum(o["agree"] for o in objects)
    per_class = {}
    for k in cats:
        tp = sum(o["ref_cat"] == k and o["comp_cat"] == k for o in objects)
        n_ref = sum(o["ref_cat"] == k for o in objects)
        n_comp = sum(o["comp_cat"] == k for o in objects)
        rec, prec = wilson(tp, n_ref), wilson(tp, n_comp)
        f1 = 2 * tp / (n_ref + n_comp) if n_ref + n_comp else None
        per_class[k] = {"n_ref": n_ref, "n_comp": n_comp, "tp": tp, "f1": f1,
                        "recall": {"value": rec[0], "ci95": list(rec[1:]), "k": tp, "n": n_ref},
                        "precision": {"value": prec[0], "ci95": list(prec[1:]), "k": tp, "n": n_comp}}
    acc = wilson(agree, n)
    acc_alt = wilson(sum(o["agree_alt"] for o in objects), n)
    return {
        "ref": ref, "comp": comp, "view": view,
        "rows": rows, "cols": cols, "row_maps_to": rmap, "col_maps_to": cmap, "matrix": matrix,
        "n": n,
        "accuracy": {"value": acc[0], "ci95": list(acc[1:]), "k": agree, "n": n},
        "accuracy_alt": {"value": acc_alt[0], "ci95": list(acc_alt[1:]), "k": sum(o["agree_alt"] for o in objects), "n": n},
        "kappa": kappa([(o["ref_cat"], o["comp_cat"]) for o in objects]),
        "per_class": per_class,
        "excluded": {k: v for k, v in excluded.items() if v},
        "objects": objects,
    }


def summary(ref, **kw):
    """Agreement of every other source with the reference."""
    out = []
    for s in sources():
        if s["id"] == ref:
            continue
        try:
            r = compare(ref, s["id"], **kw)
        except ValueError:
            out.append({"id": s["id"], "label": s["label"], "n": 0})
            continue
        out.append({"id": s["id"], "label": s["label"], "n": r["n"], "accuracy": r["accuracy"],
                    "accuracy_alt": r["accuracy_alt"], "kappa": r["kappa"]})
    return out
