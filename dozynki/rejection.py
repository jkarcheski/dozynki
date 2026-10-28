"""Rejection filters: rules that throw objects away, run as an ordered pipeline.

Unlike filters.py (which picks what gets sampled and served for labeling), these
are the cuts being *evaluated*: evaluate.py scores them against the blind labels.

Each rule sees a feature dict built from the cached payloads only (no network),
and returns True (reject), False (keep), or None (can't tell: missing data, so
the object is kept). Rules that read broker output (reliability, drb, ALeRCE)
are marked broker=True, since revealed labels may have been nudged by the same numbers.

A pipeline is a list of {"rule": name, **params}; its version is a hash of
that list, so results for different thresholds are stored side by side.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Callable

from . import assemble, db


def _rms_offset(dets):
    """rms of LSST detection positions about their mean, arcsec."""
    pos = [(p["ra"], p["dec"]) for p in dets if p.get("ra") is not None]
    if len(pos) < 2:
        return None
    ra0 = sum(r for r, _ in pos) / len(pos)
    dec0 = sum(d for _, d in pos) / len(pos)
    cosd = math.cos(math.radians(dec0))
    sq = [((r - ra0) * cosd * 3600) ** 2 + ((d - dec0) * 3600) ** 2 for r, d in pos]
    return math.sqrt(sum(sq) / len(sq))


def _nearest(rows, keep=lambda r: True):
    seps = [r["sep"] for r in rows or [] if r.get("sep") is not None and keep(r)]
    return min(seps, default=None)


def features(object_id):
    """Flat feature dict for one object from its cached payloads, or None if not fetched."""
    raw = assemble._gather(object_id)
    if raw["antares"] is None:
        return None
    ev = assemble.evidence(object_id, raw)
    op = assemble.opinions(raw)
    dets = [p for p in ev["points"] if p["kind"] == "detection"]
    lsst = [p for p in dets if p["survey"] == "lsst"]
    xm = ev["xmatch"] or {}
    rel = op["reliability"]
    stamp = ((op["alerce"] or {}).get("lsst") or {})
    # newest stamp classifier version (names sort by date suffix)
    stamp = stamp[max(stamp)] if stamp else {}
    top_host = (xm.get("host_candidates") or [None])[0] or {}
    return {
        "n_det": len(dets),
        "n_lsst_det": len(lsst),
        "n_nights": len({math.floor(p["mjd"]) for p in dets if p.get("mjd") is not None}),
        "span_days": (max(p["mjd"] for p in dets) - min(p["mjd"] for p in dets)) if dets else None,
        "frac_negative": sum(p.get("is_negative", False) for p in lsst) / len(lsst) if lsst else None,
        "frac_pixel_flagged": sum(bool(p.get("pixel_flags")) for p in lsst) / len(lsst) if lsst else None,
        "pos_rms_arcsec": _rms_offset(lsst),
        "sso_associated": bool(ev["sso_associations"]["lsst_ssObjectId"] or ev["sso_associations"]["ztf_ssnamenr"]),
        "gaia_star_sep": _nearest(xm.get("gaia"), lambda r: r.get("likely_star")),
        "gaia_sep": _nearest(xm.get("gaia")),
        "milliquas_sep": _nearest(xm.get("milliquas")),
        "host_d_dlr": top_host.get("d_dlr"),
        "abs_gal_b": abs((xm.get("context") or {}).get("gal_b")) if (xm.get("context") or {}).get("gal_b") is not None else None,
        "abs_ecl_lat": abs((xm.get("context") or {}).get("ecl_lat")) if (xm.get("context") or {}).get("ecl_lat") is not None else None,
        "lsst_reliability_max": max(rel["lsst_per_alert"], default=None),
        "lsst_reliability_mean": rel["lsst_mean"],
        "ztf_drb_mean": rel["ztf_drb_mean"],
        "alerce_stamp_top": stamp.get("top"),
        "alerce_stamp_probs": stamp.get("probs") or {},
    }


@dataclass
class Rule:
    name: str
    description: str
    defaults: dict
    test: Callable  # (features, **params) -> True reject / False keep / None unknown
    broker: bool = False


def _below(value, threshold):
    return None if value is None else value < threshold


def _within(sep, max_sep):
    return sep is not None and sep <= max_sep


RULES = {r.name: r for r in [
    Rule("sso_association", "Rubin ssObjectId or ZTF ssnamenr on any detection", {},
         lambda f: f["sso_associated"]),
    Rule("gaia_star", "Gaia DR3 source with significant parallax or proper motion within max_sep arcsec",
         {"max_sep": 1.0}, lambda f, max_sep: _within(f["gaia_star_sep"], max_sep)),
    Rule("milliquas_agn", "Milliquas QSO/AGN within max_sep arcsec", {"max_sep": 1.0},
         lambda f, max_sep: _within(f["milliquas_sep"], max_sep)),
    Rule("min_nights", "detected on fewer than n distinct nights", {"n": 2},
         lambda f, n: f["n_nights"] < n),
    Rule("min_detections", "fewer than n detections (LSST + ZTF, no upper limits)", {"n": 2},
         lambda f, n: f["n_det"] < n),
    Rule("min_lsst_detections", "fewer than n LSST detections", {"n": 2},
         lambda f, n: f["n_lsst_det"] < n),
    Rule("min_span", "first to last detection spans less than days", {"days": 1.0},
         lambda f, days: None if f["span_days"] is None else f["span_days"] < days),
    Rule("galactic_plane", "within min_b degrees of the Galactic plane", {"min_b": 10.0},
         lambda f, min_b: _below(f["abs_gal_b"], min_b)),
    Rule("ecliptic", "within min_beta degrees of the ecliptic, where most asteroids are", {"min_beta": 10.0},
         lambda f, min_beta: _below(f["abs_ecl_lat"], min_beta)),
    Rule("negative_flux", "more than max_frac of LSST detections are negative difference flux",
         {"max_frac": 0.5}, lambda f, max_frac: None if f["frac_negative"] is None else f["frac_negative"] > max_frac),
    Rule("position_scatter", "LSST detection positions scatter by more than max_rms arcsec",
         {"max_rms": 0.5}, lambda f, max_rms: None if f["pos_rms_arcsec"] is None else f["pos_rms_arcsec"] > max_rms),
    Rule("lsst_reliability", "highest per-alert LSST reliability below min_rel", {"min_rel": 0.5},
         lambda f, min_rel: _below(f["lsst_reliability_max"], min_rel), broker=True),
    Rule("alerce_stamp_bogus", "ALeRCE LSST stamp classifier P(bogus) above max_p", {"max_p": 0.5},
         lambda f, max_p: None if "bogus" not in f["alerce_stamp_probs"] else f["alerce_stamp_probs"]["bogus"] > max_p,
         broker=True),
]}

DEFAULT_PIPELINE = [
    {"rule": "sso_association"},
    {"rule": "gaia_star"},
    {"rule": "milliquas_agn"},
    {"rule": "negative_flux"},
    {"rule": "position_scatter"},
    {"rule": "min_nights"},
    {"rule": "lsst_reliability"},
]


def resolve(pipeline):
    """Fill in default params; checks every rule and param name."""
    out = []
    for step in pipeline:
        rule = RULES[step["rule"]]
        params = {k: v for k, v in step.items() if k != "rule"}
        unknown = set(params) - set(rule.defaults)
        if unknown:
            raise ValueError(f"{rule.name}: unknown params {sorted(unknown)}")
        out.append({"rule": rule.name, **rule.defaults, **params})
    return out


def version(pipeline):
    return hashlib.sha1(json.dumps(resolve(pipeline), sort_keys=True).encode()).hexdigest()[:10]


def step_key(step):
    """Unique name for a step, so the same rule at two thresholds stays distinct."""
    params = {k: v for k, v in step.items() if k != "rule"}
    return step["rule"] + "".join(f"[{k}={v}]" for k, v in sorted(params.items()))


def apply(feats, pipeline):
    """{step_key: decision} for every step (not just up to the first rejection),
    so dropping or reordering a rule can be evaluated without re-running."""
    out = {}
    for step in resolve(pipeline):
        params = {k: v for k, v in step.items() if k != "rule"}
        out[step_key(step)] = RULES[step["rule"]].test(feats, **params)
    return out


def first_rejection(decisions, keys=None):
    """The step that rejects the object in pipeline order, or None if it survives."""
    for k in keys or decisions:
        if decisions[k]:
            return k
    return None


_features_memo = {}  # object_id -> (newest cache fetched_at, features)


def features_for(object_ids):
    """features() for many objects, recomputed only when an object's cache has changed."""
    stamps = {r["object_id"]: r["t"] for r in db.query("SELECT object_id, MAX(fetched_at) t FROM cache GROUP BY object_id")}
    out = {}
    for oid in object_ids:
        hit = _features_memo.get(oid)
        if hit is None or hit[0] != stamps.get(oid):
            hit = _features_memo[oid] = (stamps.get(oid), features(oid))
        out[oid] = hit[1]
    return out


def run(object_ids, pipeline=DEFAULT_PIPELINE, save=True):
    """Apply a pipeline; with save, store every decision under its version."""
    pipeline = resolve(pipeline)
    ver = version(pipeline)
    if save:
        db.save_pipeline(ver, pipeline)
    results = {}
    for oid, feats in features_for(object_ids).items():
        if feats is None:
            continue
        decisions = apply(feats, pipeline)
        if save:
            db.save_rejections(oid, ver, decisions)
        results[oid] = {"features": feats, "decisions": decisions}
    return ver, results
