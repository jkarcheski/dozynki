"""Objects that look alike, for the similar-objects page (/similar), with no trained model.

Each object becomes a vector of the features rejection.features() already computes from cached
payloads, plus a few light-curve shape numbers from the cached ANTARES points. Every feature
is transformed by hand (log for heavy tails), then scaled robustly (median and IQR) and clipped,
so no feature dominates by its units. Nothing is weighted or fit to labels: "similar" means
close in these features, not "labeled the same".

- projection(): a 2D PCA projection (numpy SVD) of every object, missing values at the median.
- neighbors(): the k nearest objects to one, by rms difference over the features both have,
  so an object without Rubin details is compared on what it does have.

Like evaluate.py, only the uniform random sample is used unless all_sources or object_list is set.
"""

import json
import math

import numpy as np

from . import db, rejection
from .evaluate import population_ids, truth_labels

CLIP = 5.0  # scaled values beyond this many robust sigmas are clipped
MIN_SHARED = 0.5  # a neighbor must share at least this fraction of the query's features

GROUPS = {
    "lightcurve": "Light curve",
    "morphology": "Rubin pipeline measurements",
    "context": "Catalog context",
    "sky": "Sky position",
    "broker": "Broker scores",
}
DEFAULT_GROUPS = ["lightcurve", "morphology", "context"]

log10 = lambda x: math.log10(x) if x > 0 else None
log1p = lambda x: math.log1p(x) if x >= 0 else None
same = float

# (feature, group, transform, label); feature is a rejection.features() key or one of lightcurve()'s
FEATURES = [
    ("n_det", "lightcurve", log1p, "detections"),
    ("n_nights", "lightcurve", log1p, "nights with a detection"),
    ("span_days", "lightcurve", log1p, "span (days)"),
    ("snr_max", "lightcurve", log10, "peak S/N"),
    ("frac_negative", "lightcurve", same, "fraction negative"),
    ("lc_peak_flux", "lightcurve", log10, "peak |difference flux| (nJy)"),
    ("lc_rise_days", "lightcurve", log1p, "first detection to peak (days)"),
    ("lc_fade_days", "lightcurve", log1p, "peak to last detection (days)"),
    ("lc_last_over_peak", "lightcurve", same, "last / peak flux"),
    ("lc_n_bands", "lightcurve", same, "bands detected"),
    ("lc_mag_minus_sci", "lightcurve", same, "difference − science mag (median)"),
    ("extendedness_median", "morphology", same, "extendedness"),
    ("frac_dipole", "morphology", same, "fraction dipole"),
    ("any_glint", "morphology", same, "glint trail"),
    ("psf_chi2_red_median", "morphology", log10, "PSF χ²/N"),
    ("size_ratio_median", "morphology", log10, "size / PSF"),
    ("frac_measurement_flagged", "morphology", same, "fraction measurement-flagged"),
    ("frac_pixel_flagged", "morphology", same, "fraction pixel-flagged"),
    ("pos_rms_arcsec", "morphology", log1p, "position rms (″)"),
    ("trail_max_arcsec", "morphology", log1p, "trail length (″)"),
    ("gaia_star_sep", "context", log1p, "Gaia star sep (″)"),
    ("gaia_sep", "context", log1p, "Gaia sep (″)"),
    ("milliquas_sep", "context", log1p, "Milliquas sep (″)"),
    ("ztf_ps1_sgscore", "context", same, "PS1 star/galaxy score"),
    ("ztf_ps1_dist", "context", log1p, "PS1 distance (″)"),
    ("host_d_dlr", "context", log1p, "host d_DLR"),
    ("antares_bright_star", "context", same, "bright star (ANTARES)"),
    ("antares_gaia_variable", "context", same, "Gaia variable (ANTARES)"),
    ("sso_associated", "context", same, "solar-system association"),
    ("abs_gal_b", "sky", same, "|Galactic b| (°)"),
    ("abs_ecl_lat", "sky", same, "|ecliptic β| (°)"),
    ("lsst_reliability_mean", "broker", same, "LSST reliability (mean)"),
    ("lsst_reliability_max", "broker", same, "LSST reliability (max)"),
    ("ztf_drb_mean", "broker", same, "ZTF drb (mean)"),
    ("scimma_quality", "broker", same, "SCIMMA quality tag"),
]
LABELS = {name: label for name, _, _, label in FEATURES}


def lightcurve(points):
    """Shape numbers from an object's LSST detections (difference flux in nJy)."""
    dets = sorted((p for p in points if p["survey"] == "lsst" and p["kind"] == "detection"
                   and p.get("diff_flux") is not None and p.get("mjd") is not None), key=lambda p: p["mjd"])
    if not dets:
        return {}
    peak = max(dets, key=lambda p: abs(p["diff_flux"]))
    contrast = sorted(p["mag"] - p["sci_mag"] for p in dets if p.get("mag") is not None and p.get("sci_mag") is not None)
    return {
        "lc_peak_flux": abs(peak["diff_flux"]),
        "lc_rise_days": peak["mjd"] - dets[0]["mjd"],
        "lc_fade_days": dets[-1]["mjd"] - peak["mjd"],
        "lc_last_over_peak": dets[-1]["diff_flux"] / peak["diff_flux"] if peak["diff_flux"] else None,
        "lc_n_bands": len({p.get("band") for p in dets}),
        "lc_mag_minus_sci": contrast[len(contrast) // 2] if contrast else None,
    }


_lc_memo = {}  # object_id -> (antares fetched_at, lightcurve())


def _raw(object_ids):
    """{object_id: rejection.features() + lightcurve()} for the objects with a cached ANTARES payload."""
    feats = rejection.features_for(object_ids)
    stamps = {r["object_id"]: r["fetched_at"] for r in db.query("SELECT object_id, fetched_at FROM cache WHERE kind = 'antares'")}
    out = {}
    for oid, f in feats.items():
        if f is None:
            continue
        hit = _lc_memo.get(oid)
        if hit is None or hit[0] != stamps.get(oid):
            hit = _lc_memo[oid] = (stamps.get(oid), lightcurve(db.cache_get(oid, "antares")["data"]["points"]))
        out[oid] = {**f, **hit[1]}
    return out


def _value(raw, name, transform):
    v = raw.get(name)
    if v is None:
        return None
    try:
        v = transform(float(v))
    except (TypeError, ValueError):
        return None
    return v if v is not None and math.isfinite(v) else None


def build(truth="blind", all_sources=False, groups=DEFAULT_GROUPS, object_list=None):
    """Scaled matrix (NaN where missing) over the population and the features with any spread."""
    unknown = set(groups) - set(GROUPS)
    if unknown:
        raise ValueError(f"unknown feature groups {sorted(unknown)}")
    ids = sorted(population_ids(all_sources, object_list))
    raw = _raw(ids)
    ids = sorted(raw)
    feats = [f for f in FEATURES if f[1] in groups]
    X = np.array([[_value(raw[oid], name, t) for name, _, t, _ in feats] for oid in ids], dtype=float).reshape(len(ids), len(feats))
    keep, columns = [], []
    for j, (name, group, _, label) in enumerate(feats):
        col = X[:, j]
        present = col[~np.isnan(col)]
        if len(present) < 2:
            continue
        med = np.median(present)
        q1, q3 = np.percentile(present, [25, 75])
        scale = (q3 - q1) / 1.349 or present.std()
        if not scale:
            continue
        X[:, j] = np.clip((col - med) / scale, -CLIP, CLIP)
        keep.append(j)
        columns.append({"name": name, "group": group, "label": label, "coverage": len(present) / len(ids)})
    return {"ids": ids, "X": X[:, keep], "columns": columns, "raw": raw, "labels": truth_labels(truth)}


def _label(labels, oid):
    lab = labels.get(oid)
    return {"klass": lab["klass"] if lab else None, "subclass": lab["subclass"] if lab else None,
            "alt_classes": lab["alt_classes"] if lab else []}


def projection(truth="blind", all_sources=False, groups=DEFAULT_GROUPS, object_list=None):
    """Every object's first two principal components, with the loadings that explain the axes."""
    m = build(truth, all_sources, groups, object_list)
    ids, X, cols = m["ids"], m["X"], m["columns"]
    if len(ids) < 3 or X.shape[1] < 2:
        return {"objects": [], "columns": cols, "explained": [], "loadings": []}
    Z = np.nan_to_num(X)  # missing at the median
    Z = Z - Z.mean(axis=0)
    _, s, vt = np.linalg.svd(Z, full_matrices=False)
    pcs = Z @ vt[:2].T
    explained = (s ** 2 / (s ** 2).sum())[:2]
    loadings = [sorted(({"feature": c["name"], "label": c["label"], "weight": float(w)} for c, w in zip(cols, vt[i])),
                       key=lambda d: -abs(d["weight"])) for i in range(2)]
    return {
        "objects": [{"object_id": oid, "x": float(pcs[i, 0]), "y": float(pcs[i, 1]),
                     "n_features": int((~np.isnan(X[i])).sum()), **_label(m["labels"], oid)} for i, oid in enumerate(ids)],
        "columns": cols, "explained": [float(e) for e in explained], "loadings": loadings,
    }


def _thumbs(oid):
    """The cutouts ANTARES gave for the object's latest detection (cached with the locus)."""
    hit = db.cache_get(oid, "antares")
    thumbs = (hit["data"].get("thumbs") or {}) if hit else {}
    lsst = [v for k, v in thumbs.items() if k.startswith("lsst:")]
    return (lsst or list(thumbs.values()) or [{}])[0]


def _display(raw):
    return {name: raw.get(name) for name, *_ in FEATURES}


def neighbors(object_id, k=8, truth="blind", all_sources=False, groups=DEFAULT_GROUPS, pool="labeled", object_list=None):
    """The k nearest objects to one: rms of scaled differences over the features both objects have."""
    m = build(truth, all_sources, groups, object_list)
    ids, X, labels = m["ids"], m["X"], m["labels"]
    if object_id not in ids:
        raise KeyError(object_id)
    i = ids.index(object_id)
    q = X[i]
    shared = ~np.isnan(X) & ~np.isnan(q)
    n_shared = shared.sum(axis=1)
    d2 = np.where(shared, (X - q) ** 2, 0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        dist = np.sqrt(d2 / n_shared)
    ok = n_shared >= max(1, MIN_SHARED * (~np.isnan(q)).sum())
    ok[i] = False
    if pool == "labeled":
        ok &= np.array([oid in labels for oid in ids])
    order = [j for j in np.argsort(np.where(ok, dist, np.inf)) if ok[j]][:k]

    def entry(j):
        oid = ids[j]
        # the features this object differs most on from the query, in robust sigmas
        diffs = sorted(((m["columns"][c]["name"], float(X[j, c] - q[c])) for c in np.flatnonzero(shared[j])),
                       key=lambda d: -abs(d[1]))
        return {"object_id": oid, **_label(labels, oid), "thumbs": _thumbs(oid), "values": _display(m["raw"][oid]),
                "distance": float(dist[j]), "shared": int(n_shared[j]), "differs": diffs[:3]}

    return {
        "query": {"object_id": object_id, **_label(labels, object_id), "thumbs": _thumbs(object_id),
                  "values": _display(m["raw"][object_id]), "n_features": int((~np.isnan(q)).sum())},
        "neighbors": [entry(j) for j in order],
        "columns": m["columns"],
        "pool_size": int(ok.sum()),
    }
