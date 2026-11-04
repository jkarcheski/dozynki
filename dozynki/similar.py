"""Objects that look alike, for the similar-objects page (/similar), with no trained model.

Each object becomes a vector of the features rejection.features() already computes from cached
payloads, plus a few light-curve shape numbers from the cached ANTARES points. Every feature
is transformed by hand (log for heavy tails), then scaled robustly (median and IQR) and clipped,
so no feature dominates by its units. Nothing is weighted or fit to labels: "similar" means
close in these features, not "labeled the same".

- projection(): a 2D PCA projection (numpy SVD) of every object, fit around missing values (pca()), with how
  much of each axis an object's own features support, so the page can fade positions that are mostly guesswork.
- neighbors(): the k nearest objects to one, by rms difference over the features both have,
  so an object without Rubin details is compared on what it does have.

The math is in plain functions over a matrix (scale, pca, distances), which tests/test_similar.py checks
without a database.

Each object also carries its TNS match (tns.py, ANTARES's match, when cached), for the page to show
where TNS objects fall; it plays no part in the features or distances.

Like evaluate.py, only the uniform random sample is used unless all_sources, object_list or vetted is set.
"""

import json
import math

import numpy as np

from . import db, rejection, tns
from .evaluate import population_ids, truth_labels

CLIP = 5.0  # scaled values beyond this many robust sigmas are clipped
MIN_SHARED = 0.5  # a neighbor must share at least this fraction of the query's features
MIN_COVERAGE = 0.05  # a feature fewer objects than this have is left out: too few to place anything by
MIN_PAIRS = 20  # two features are only compared over at least this many objects that have both

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


def scale(X):
    """Robust scaling of each column, in place: (x - median) / (IQR / 1.349), clipped to +-CLIP; NaN stays NaN.
    Returns the indices of the columns kept (on at least MIN_COVERAGE of the rows, and some spread) and each
    one's coverage."""
    keep, coverage = [], []
    for j in range(X.shape[1]):
        col = X[:, j]
        present = col[~np.isnan(col)]
        if len(present) < max(2, MIN_COVERAGE * len(X)):
            continue
        med = np.median(present)
        q1, q3 = np.percentile(present, [25, 75])
        spread = (q3 - q1) / 1.349 or present.std()
        if not spread:
            continue
        X[:, j] = np.clip((col - med) / spread, -CLIP, CLIP)
        keep.append(j)
        coverage.append(len(present) / len(X))
    return keep, coverage


def pca(X, n=2):
    """The first n principal components of a scaled matrix with missing values (NaN), without filling them in.

    The axes come from the covariance of what is there: each pair of features is compared over the objects
    that have both, if at least MIN_PAIRS do (for complete data, exactly plain PCA). Each object is then placed from the features it
    has: a least-squares fit to the axes, regularized as in probabilistic PCA so that an object with little
    on an axis sits near that axis's center instead of being thrown far out on a guess, and scaled so a
    complete object lands exactly where plain PCA puts it.

    Each axis is turned so its largest loading is positive: an SVD's signs are arbitrary, and without this the
    map could mirror itself whenever the features change.

    Returns (scores, explained fractions, loadings, support): support[i, a] is the share of axis a's loading
    weight on features object i has, 1 for a complete object and near 0 when its position on a is a guess."""
    has = ~np.isnan(X)
    n_obj, d = X.shape
    k = min(n, d)
    with np.errstate(invalid="ignore", divide="ignore"):
        mu = np.nan_to_num(np.nanmean(X, axis=0))
        C = np.where(has, X - mu, 0.0)
        pairs = has.T.astype(float) @ has  # objects having both features
        cov = np.where(pairs >= min(MIN_PAIRS, n_obj), np.nan_to_num((C.T @ C) / pairs), 0.0)
    var, V = np.linalg.eigh(cov)
    var, V = np.clip(var[::-1], 0, None), V[:, ::-1]  # largest first; pairwise covariances can dip below 0
    vt = V[:, :k].T
    vt = vt * np.where(vt[np.arange(k), np.abs(vt).argmax(axis=1)] < 0, -1, 1)[:, None]
    total = var.sum()
    explained = var[:k] / total if total else np.zeros(k)
    noise = var[k:].mean() if d > k else 0.0  # probabilistic PCA's noise: the variance the n axes leave
    with np.errstate(divide="ignore", invalid="ignore"):
        ridge = np.where(var[:k] > 0, noise / var[:k], 0.0)

    scores = np.zeros((n_obj, k))
    support = has.astype(float) @ (vt ** 2).T
    patterns, which = np.unique(has, axis=0, return_inverse=True)
    for p, pattern in enumerate(patterns):  # objects missing the same features share one solve
        if not pattern.any():
            continue
        rows = np.flatnonzero(which.ravel() == p)
        A = vt[:, pattern] @ vt[:, pattern].T + np.diag(ridge)
        scores[rows] = np.linalg.lstsq(A, vt[:, pattern] @ C[np.ix_(rows, np.flatnonzero(pattern))].T, rcond=None)[0].T * (1 + ridge)
    return scores, explained, vt, support


def distances(X, i):
    """Each row's distance from row i: rms of the differences over the features both have (NaN where none).
    Returns (distance, number of shared features, shared mask)."""
    q = X[i]
    shared = ~np.isnan(X) & ~np.isnan(q)
    n_shared = shared.sum(axis=1)
    d2 = np.where(shared, (X - q) ** 2, 0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.sqrt(d2 / n_shared), n_shared, shared


def build(truth="blind", all_sources=False, groups=DEFAULT_GROUPS, object_list=None, vetted=None, survey="lsst"):
    """Scaled matrix (NaN where missing) over the population and the features with any spread."""
    unknown = set(groups) - set(GROUPS)
    if unknown:
        raise ValueError(f"unknown feature groups {sorted(unknown)}")
    ids = sorted(population_ids(all_sources, object_list, vetted, survey))
    raw = _raw(ids)
    ids = sorted(raw)
    feats = [f for f in FEATURES if f[1] in groups]
    X = np.array([[_value(raw[oid], name, t) for name, _, t, _ in feats] for oid in ids], dtype=float).reshape(len(ids), len(feats))
    keep, coverage = scale(X)
    columns = [{"name": feats[j][0], "group": feats[j][1], "label": feats[j][3], "coverage": c} for j, c in zip(keep, coverage)]
    return {"ids": ids, "X": X[:, keep], "columns": columns, "raw": raw, "labels": truth_labels(truth)}


def _tns(matched, oid):
    m = matched.get(oid)
    return tns.summary(m) if m else None


def _label(labels, oid):
    lab = labels.get(oid)
    return {"klass": lab["klass"] if lab else None, "subclass": lab["subclass"] if lab else None,
            "alt_classes": lab["alt_classes"] if lab else []}


def projection(truth="blind", all_sources=False, groups=DEFAULT_GROUPS, object_list=None, vetted=None, survey="lsst"):
    """Every object's first two principal components, with the loadings that explain the axes."""
    m = build(truth, all_sources, groups, object_list, vetted, survey)
    ids, X, cols = m["ids"], m["X"], m["columns"]
    if len(ids) < 3 or X.shape[1] < 2:
        return {"objects": [], "columns": cols, "explained": [], "loadings": []}
    pcs, explained, vt, support = pca(X)
    matched = tns.cached()
    loadings = [sorted(({"feature": c["name"], "label": c["label"], "weight": float(w)} for c, w in zip(cols, vt[i])),
                       key=lambda d: -abs(d["weight"])) for i in range(2)]
    return {
        "objects": [{"object_id": oid, "x": float(pcs[i, 0]), "y": float(pcs[i, 1]), "n_features": int((~np.isnan(X[i])).sum()),
                     "support": [round(float(v), 3) for v in support[i]],
                     **_label(m["labels"], oid), "tns": _tns(matched, oid)} for i, oid in enumerate(ids)],
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


def neighbors(object_id, k=8, truth="blind", all_sources=False, groups=DEFAULT_GROUPS, pool="labeled", object_list=None, vetted=None,
              survey="lsst"):
    """The k nearest objects to one: rms of scaled differences over the features both objects have."""
    m = build(truth, all_sources, groups, object_list, vetted, survey)
    ids, X, labels = m["ids"], m["X"], m["labels"]
    if object_id not in ids:
        raise KeyError(object_id)
    i = ids.index(object_id)
    q = X[i]
    dist, n_shared, shared = distances(X, i)
    ok = n_shared >= max(1, MIN_SHARED * (~np.isnan(q)).sum())
    ok[i] = False
    if pool == "labeled":
        ok &= np.array([oid in labels for oid in ids])
    order = [j for j in np.argsort(np.where(ok, dist, np.inf)) if ok[j]][:k]
    matched = tns.cached()

    def entry(j):
        oid = ids[j]
        # the features this object differs most on from the query, in robust sigmas
        diffs = sorted(((m["columns"][c]["name"], float(X[j, c] - q[c])) for c in np.flatnonzero(shared[j])),
                       key=lambda d: -abs(d[1]))
        return {"object_id": oid, **_label(labels, oid), "tns": _tns(matched, oid), "thumbs": _thumbs(oid), "values": _display(m["raw"][oid]),
                "distance": float(dist[j]), "shared": int(n_shared[j]), "differs": diffs[:3]}

    return {
        "query": {"object_id": object_id, **_label(labels, object_id), "tns": _tns(matched, object_id), "thumbs": _thumbs(object_id),
                  "values": _display(m["raw"][object_id]), "n_features": int((~np.isnan(q)).sum())},
        "neighbors": [entry(j) for j in order],
        "columns": m["columns"],
        "pool_size": int(ok.sum()),
    }
