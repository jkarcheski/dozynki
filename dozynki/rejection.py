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

from . import assemble, db, details


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


def _median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def _frac(values):
    vals = [bool(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


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
    ant = raw["antares"]["data"]
    det = details.get(object_id, ant)
    lsst_details = [det[p["alert_id"]] for p in lsst if p.get("alert_id") in det] if det is not None else []
    ztf_ps1 = [p for p in dets if p["survey"] == "ztf" and p.get("distpsnr1") is not None and p["distpsnr1"] >= 0]
    nearest_ps1 = min(ztf_ps1, key=lambda p: p["distpsnr1"]) if ztf_ps1 else {}
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
        # Rubin pipeline measurements per LSST detection (details.py); None before they're fetched
        "extendedness_median": _median(d["extendedness"] for d in lsst_details),
        "frac_dipole": _frac(d["is_dipole"] for d in lsst_details),
        "any_glint": any(d["glint_trail"] for d in lsst_details) if lsst_details else None,
        "psf_chi2_red_median": _median(d["psf_chi2_red"] for d in lsst_details),
        "size_ratio_median": _median(d["size_ratio"] for d in lsst_details),
        "frac_measurement_flagged": _frac(d["measurement_flagged"] for d in lsst_details),
        "snr_max": max((p["snr"] for p in lsst if p.get("snr") is not None), default=None),
        "trail_max_arcsec": max((p["trail_length"] for p in lsst if p.get("trail_length") is not None), default=None),
        # ZTF's nearest Pan-STARRS1 source and its star/galaxy score (1 star, 0 galaxy)
        "ztf_ps1_dist": nearest_ps1.get("distpsnr1"),
        "ztf_ps1_sgscore": nearest_ps1.get("sgscore1"),
        # ANTARES's own catalog matches and filter tags on the locus
        "antares_bright_star": "bright_guide_star_cat" in (ant.get("catalogs") or []),
        "antares_gaia_variable": "gaia_dr3_variability" in (ant.get("catalogs") or []),
        "scimma_quality": "lsst_scimma_quality_transient" in (ant.get("tags") or []),
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
    Rule("extended_diff", "median extendedness of the LSST difference-image detections above max_ext",
         {"max_ext": 0.9}, lambda f, max_ext: None if f["extendedness_median"] is None else f["extendedness_median"] > max_ext),
    Rule("dipole", "more than max_frac of LSST detections flagged as dipoles", {"max_frac": 0.5},
         lambda f, max_frac: None if f["frac_dipole"] is None else f["frac_dipole"] > max_frac),
    Rule("glint", "an LSST detection flagged as a satellite glint trail", {},
         lambda f: f["any_glint"]),
    Rule("bad_psf_fit", "median reduced chi2 of the LSST PSF fits above max_chi2", {"max_chi2": 5.0},
         lambda f, max_chi2: None if f["psf_chi2_red_median"] is None else f["psf_chi2_red_median"] > max_chi2),
    Rule("sharp", "LSST detections smaller than the PSF (median second-moment size ratio below min_ratio): cosmic rays, hot pixels",
         {"min_ratio": 0.25}, lambda f, min_ratio: _below(f["size_ratio_median"], min_ratio)),
    Rule("oversized", "LSST detections larger than the PSF (median second-moment size ratio above max_ratio)",
         {"max_ratio": 2.0}, lambda f, max_ratio: None if f["size_ratio_median"] is None else f["size_ratio_median"] > max_ratio),
    Rule("measurement_flags", "more than max_frac of LSST detections have a failed PSF, centroid, shape or aperture measurement",
         {"max_frac": 0.5}, lambda f, max_frac: None if f["frac_measurement_flagged"] is None else f["frac_measurement_flagged"] > max_frac),
    Rule("low_snr", "brightest LSST detection has SNR below min_snr", {"min_snr": 7.0},
         lambda f, min_snr: _below(f["snr_max"], min_snr)),
    Rule("trailed", "an LSST detection trailed longer than max_len arcsec (a fast mover)", {"max_len": 2.0},
         lambda f, max_len: None if f["trail_max_arcsec"] is None else f["trail_max_arcsec"] > max_len),
    Rule("ztf_ps1_star", "ZTF's nearest PS1 source is within max_dist arcsec with star/galaxy score at least min_sg",
         {"max_dist": 1.0, "min_sg": 0.5},
         lambda f, max_dist, min_sg: None if f["ztf_ps1_dist"] is None or f["ztf_ps1_sgscore"] is None
         else f["ztf_ps1_dist"] <= max_dist and f["ztf_ps1_sgscore"] >= min_sg),
    Rule("antares_bright_star", "ANTARES matched the locus to its bright guide star catalog", {},
         lambda f: f["antares_bright_star"]),
    Rule("antares_gaia_variable", "ANTARES matched the locus to the Gaia DR3 variability catalog", {},
         lambda f: f["antares_gaia_variable"]),
    Rule("scimma_quality", "ANTARES's SCIMMA quality-transient filter did not tag the locus", {},
         lambda f: not f["scimma_quality"], broker=True),
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


# Rules ANTARES's locus listing can decide, so a whole night can be filtered without
# fetching every object (nightrun.py). Some become ES clauses that keep what the rule
# keeps; the rest run here on listing_features(). Every other rule waits for the full fetch.
LISTING_ES = {
    "min_detections": lambda n: [{"range": {"properties.num_mag_values": {"gte": n}}}],
    "sso_association": lambda: [{"bool": {"must_not": [{"exists": {"field": "properties.survey.lsst.ss_object_id"}},
                                                       {"exists": {"field": "properties.survey.ztf.ssnamenr"}}]}}],
    "antares_bright_star": lambda: [{"bool": {"must_not": {"term": {"catalogs": "bright_guide_star_cat"}}}}],
    "antares_gaia_variable": lambda: [{"bool": {"must_not": {"term": {"catalogs": "gaia_dr3_variability"}}}}],
    "scimma_quality": lambda: [{"term": {"tags": "lsst_scimma_quality_transient"}}],
}
LISTING_LOCAL = {"min_span", "galactic_plane", "ecliptic"}


def listing_split(pipeline):
    """(ES clauses, steps to run on listing_features, steps that need the full fetch)."""
    es, local, later = [], [], []
    for step in resolve(pipeline):
        params = {k: v for k, v in step.items() if k != "rule"}
        if step["rule"] in LISTING_ES:
            es += LISTING_ES[step["rule"]](**params)
        elif step["rule"] in LISTING_LOCAL:
            local.append(step)
        else:
            later.append(step)
    return es, local, later


def listing_features(summaries):
    """The features LISTING_LOCAL rules read, for many ANTARES listing summaries at once."""
    from astropy.coordinates import SkyCoord

    if not summaries:
        return []
    c = SkyCoord([s["ra"] for s in summaries], [s["dec"] for s in summaries], unit="deg")
    gal_b, ecl_lat = c.galactic.b.deg, c.barycentrictrueecliptic.lat.deg
    return [{"n_det": s["n_det"],
             "span_days": s["last_mjd"] - s["first_mjd"] if s["first_mjd"] is not None and s["last_mjd"] is not None else None,
             "abs_gal_b": abs(float(b)), "abs_ecl_lat": abs(float(e))}
            for s, b, e in zip(summaries, gal_b, ecl_lat)]


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
