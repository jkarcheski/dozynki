"""Checks for the similar-objects page (similar.py): the scaling, PCA and distance math on matrices with
known answers, then projection() and neighbors() end to end on made-up objects, under every combination of
feature groups the page can toggle.

Run with: .venv/bin/python -m pytest tests
"""

from itertools import combinations

import numpy as np
import pytest

from dozynki import similar

rng = lambda seed=0: np.random.default_rng(seed)


# ---------- scale() ----------

def test_scale_is_robust_z():
    x = rng().normal(10, 3, 5000)
    X = x[:, None].copy()
    similar.scale(X)
    assert abs(np.median(X)) < 1e-9
    # for normal data IQR / 1.349 is the standard deviation
    assert abs(X.std() - 1) < 0.05


def test_scale_ignores_units():
    """Changing a feature's units (or offset) changes nothing after scaling."""
    x = rng().lognormal(0, 1, (300, 3))
    a, b = x.copy(), x * [1, 1000, 1e-6] + [0, 5, -2]
    similar.scale(a)
    similar.scale(b)
    assert np.allclose(a, b)


def test_scale_clips():
    X = np.r_[rng().normal(0, 1, 999), 1e6][:, None]
    similar.scale(X)
    assert X.max() == similar.CLIP


def test_scale_keeps_nan_and_reports_coverage():
    X = rng().normal(0, 1, (10, 1))
    X[:4] = np.nan
    keep, coverage = similar.scale(X)
    assert keep == [0] and coverage == [0.6]
    assert np.isnan(X[:4]).all() and not np.isnan(X[4:]).any()


def test_scale_drops_near_empty_features():
    X = np.full((200, 2), np.nan)
    X[:, 1] = rng().normal(0, 1, 200)
    X[:5, 0] = [1, 2, 3, 4, 5]  # 2.5% of rows, under MIN_COVERAGE
    keep, _ = similar.scale(X)
    assert keep == [1]


def test_scale_drops_columns_without_spread():
    X = np.c_[np.ones(10), np.r_[1.0, [np.nan] * 9], np.arange(10.0)]
    keep, _ = similar.scale(X)
    assert keep == [2]


def test_scale_mostly_constant_flag_falls_back_to_std():
    """A 0/1 flag set on a few objects has an IQR of 0; it is scaled by its standard deviation, not dropped."""
    X = np.r_[np.zeros(95), np.ones(5)][:, None]
    keep, _ = similar.scale(X)
    assert keep == [0] and X.max() > 0


# ---------- pca() ----------

def test_pca_finds_the_long_axis():
    """Points spread along (1, 1, 0) with a little noise: PC1 is that direction and holds the variance."""
    t = rng().normal(0, 3, 2000)
    X = np.c_[t, t, np.zeros_like(t)] + rng(1).normal(0, 0.1, (2000, 3))
    scores, explained, vt, _ = similar.pca(X)
    assert abs(vt[0] @ np.array([1, 1, 0]) / np.sqrt(2)) > 0.999
    assert explained[0] > 0.99


def test_complete_data_is_plain_pca():
    """With nothing missing, the result is textbook PCA: an SVD of the centered matrix."""
    X = rng().normal(0, 1, (400, 6)) @ rng(1).normal(0, 1, (6, 6))
    scores, explained, vt, support = similar.pca(X)
    Z = X - X.mean(axis=0)
    _, s, ref_vt = np.linalg.svd(Z, full_matrices=False)
    ref = Z @ ref_vt[:2].T
    sign = np.sign((ref * scores).sum(axis=0))
    assert np.allclose(ref * sign, scores)
    assert np.allclose((s ** 2 / (s ** 2).sum())[:2], explained)
    assert np.allclose(support, 1)


def test_missing_value_recovered_from_related_features():
    """Hide one of three related features for half the objects: they land about where they would with it,
    not on a line where the median would have put them."""
    r = rng()
    t = r.normal(0, 1, 1000)
    full = np.c_[t, t, t] + r.normal(0, 0.3, (1000, 3))
    X = full.copy()
    hidden = r.random(1000) < 0.5
    X[hidden, 0] = np.nan
    want, _, _, _ = similar.pca(full)
    got, _, _, support = similar.pca(X)
    assert np.corrcoef(want[hidden, 0], got[hidden, 0])[0, 1] > 0.97
    assert np.abs(want[hidden, 0] - got[hidden, 0]).mean() < 0.2 * want[:, 0].std()
    assert np.allclose(support[~hidden], 1) and (support[hidden, 0] < 1).all()


def test_axis_an_object_has_nothing_for_puts_it_at_the_center():
    """An object with none of the features an axis is made of sits at that axis's center, with ~0 support,
    instead of being thrown out on a guess."""
    r = rng()
    a, b = r.normal(0, 3, 500), r.normal(0, 1, 500)  # two unrelated directions
    X = np.c_[a, a + r.normal(0, 0.1, 500), b, b + r.normal(0, 0.1, 500)]
    X[:50, :2] = np.nan  # these objects lack everything PC1 (the a-direction) is made of
    scores, _, vt, support = similar.pca(X)
    assert np.abs(vt[0, :2]).min() > 0.6  # PC1 is the a-pair
    assert np.abs(scores[:50, 0]).max() < 0.1 * scores[50:, 0].std()
    assert (support[:50, 0] < 0.05).all() and (support[:50, 1] > 0.95).all()


def test_pca_properties():
    X = rng().normal(0, 1, (300, 5)) * [5, 3, 2, 1, 0.5]
    scores, explained, vt, _ = similar.pca(X)
    assert np.allclose(scores.mean(axis=0), 0)
    assert abs(np.corrcoef(scores.T)[0, 1]) < 1e-9  # the two axes are uncorrelated
    assert np.allclose(vt @ vt.T, np.eye(2))  # loadings are unit length and perpendicular
    assert explained[0] >= explained[1] and explained.sum() <= 1


def test_pca_sign_is_fixed():
    """Each axis points the way its largest loading is positive, so the map doesn't mirror at random."""
    X = rng().normal(0, 1, (200, 4)) * [4, 2, 1, 1]
    _, _, vt, _ = similar.pca(X)
    assert all(row[np.abs(row).argmax()] > 0 for row in vt)
    perm = rng(1).permutation(len(X))
    scores, _, vt2, _ = similar.pca(X)
    scores_p, _, vt_p, _ = similar.pca(X[perm])
    assert np.allclose(vt2, vt_p) and np.allclose(scores[perm], scores_p)


def test_pca_of_nothing_varying_is_zero_not_nan():
    scores, explained, _, _ = similar.pca(np.zeros((5, 3)))
    assert not np.isnan(scores).any() and not np.isnan(explained).any()


# ---------- distances() ----------

def test_distances_known_values():
    X = np.array([[0.0, 0.0], [3.0, 4.0], [1.0, np.nan], [np.nan, np.nan]])
    d, n, _ = similar.distances(X, 0)
    assert d[0] == 0
    assert np.isclose(d[1], np.sqrt((9 + 16) / 2))
    assert d[2] == 1 and n[2] == 1  # only the feature both have
    assert np.isnan(d[3]) and n[3] == 0  # nothing shared


def test_distances_symmetric():
    X = rng().normal(0, 1, (50, 4))
    X[rng(1).random(X.shape) < 0.3] = np.nan
    D = np.array([similar.distances(X, i)[0] for i in range(len(X))])
    assert np.allclose(D, D.T, equal_nan=True)


# ---------- projection() and neighbors() on made-up objects ----------

FLAGS = {"any_glint", "antares_bright_star", "antares_gaia_variable", "sso_associated", "lc_n_bands"}
SHARES = {"frac_negative", "frac_dipole", "frac_measurement_flagged", "frac_pixel_flagged", "extendedness_median",
          "ztf_ps1_sgscore", "lsst_reliability_mean", "lsst_reliability_max", "ztf_drb_mean", "scimma_quality", "lc_last_over_peak"}


def fake_objects(n=120, seed=0):
    """Raw features for n objects, in the same domains as the real ones; catalog matches present for about half."""
    r = rng(seed)
    out = {}
    for i in range(n):
        f = {}
        for name, group, *_ in similar.FEATURES:
            if name in FLAGS:
                f[name] = int(r.random() < 0.2)
            elif name in SHARES:
                f[name] = float(r.random())
            elif name == "lc_mag_minus_sci":
                f[name] = float(r.normal(0, 1))
            else:
                f[name] = float(r.lognormal(1, 1))
            if group == "context" and name not in FLAGS and r.random() < 0.5:
                f[name] = None
        out[f"obj{i:03d}"] = f
    return out


@pytest.fixture
def objects(monkeypatch):
    raw = fake_objects()
    labeled = {oid: {"klass": "star", "subclass": None, "alt_classes": []} for oid in list(raw)[::2]}
    monkeypatch.setattr(similar, "population_ids", lambda *a, **k: list(raw))
    monkeypatch.setattr(similar, "_raw", lambda ids: {oid: dict(raw[oid]) for oid in ids})
    monkeypatch.setattr(similar, "truth_labels", lambda truth="blind": labeled)
    monkeypatch.setattr(similar.tns, "cached", lambda: {})
    monkeypatch.setattr(similar, "_thumbs", lambda oid: {})
    return raw, labeled


GROUP_SETS = [list(c) for n in range(1, len(similar.GROUPS) + 1) for c in combinations(similar.GROUPS, n)]


@pytest.mark.parametrize("groups", GROUP_SETS, ids=lambda g: "+".join(g))
def test_projection_every_group_combination(objects, groups):
    raw, _ = objects
    p = similar.projection(groups=groups)
    assert {c["group"] for c in p["columns"]} <= set(groups)
    if len(p["columns"]) < 2:  # a group can have too few features to project; the page then shows nothing
        assert p["objects"] == []
        return
    assert sorted(o["object_id"] for o in p["objects"]) == sorted(raw)
    xy = np.array([[o["x"], o["y"]] for o in p["objects"]])
    assert np.isfinite(xy).all()
    support = np.array([o["support"] for o in p["objects"]])
    assert ((support >= 0) & (support <= 1.001)).all()
    for axis in p["loadings"]:
        assert {d["feature"] for d in axis} == {c["name"] for c in p["columns"]}
        assert np.isclose(sum(d["weight"] ** 2 for d in axis), 1)


@pytest.mark.parametrize("groups", GROUP_SETS, ids=lambda g: "+".join(g))
def test_neighbors_every_group_combination(objects, groups):
    raw, labeled = objects
    query = "obj001"
    out = similar.neighbors(query, k=8, groups=groups)
    found = out["neighbors"]
    assert query not in [n["object_id"] for n in found]
    assert all(n["object_id"] in labeled for n in found)  # pool="labeled"
    dists = [n["distance"] for n in found]
    assert dists == sorted(dists) and all(np.isfinite(dists))
    need = max(1, similar.MIN_SHARED * out["query"]["n_features"])
    assert all(n["shared"] >= need for n in found)
    # nothing closer was left out
    m = similar.build(groups=groups)
    d, n_shared, _ = similar.distances(m["X"], m["ids"].index(query))
    ok = [j for j, oid in enumerate(m["ids"]) if oid in labeled and oid != query and n_shared[j] >= need]
    if found:
        assert np.isclose(dists[-1], np.sort(d[ok])[len(found) - 1])


def test_toggling_groups_leaves_no_trace(objects):
    """Switching feature groups off and back on gives exactly the first map and neighbors again."""
    first = similar.projection(groups=similar.DEFAULT_GROUPS)
    first_nn = similar.neighbors("obj001", groups=similar.DEFAULT_GROUPS)
    for groups in GROUP_SETS:
        similar.projection(groups=groups)
        similar.neighbors("obj001", groups=groups)
    assert similar.projection(groups=similar.DEFAULT_GROUPS) == first
    assert similar.neighbors("obj001", groups=similar.DEFAULT_GROUPS) == first_nn


def test_group_order_does_not_matter(objects):
    a = similar.projection(groups=["context", "lightcurve"])
    b = similar.projection(groups=["lightcurve", "context"])
    assert a == b


def test_unknown_group_is_refused(objects):
    with pytest.raises(ValueError):
        similar.projection(groups=["lightcurve", "nope"])


def test_object_not_in_population(objects):
    with pytest.raises(KeyError):
        similar.neighbors("not-an-object")


# ---------- known artifact: fails until "no catalog match" counts as far away ----------

def gaia_like():
    """Like the Catalog context group: two related separations (Gaia sep and Gaia star sep), both missing for
    the half with no Gaia match, beside two rarely-set flags. Returns (PCA scores, loadings, no-match mask)."""
    r = rng()
    n = 1000
    sep = r.lognormal(0, 1.5, n)
    X = np.c_[np.log1p(sep), np.log1p(sep * r.lognormal(0, 0.1, n)), r.random(n) < 0.1, r.random(n) < 0.05].astype(float)
    no_match = r.random(n) < 0.5
    X[no_match, :2] = np.nan
    similar.scale(X)
    scores, _, vt, _ = similar.pca(X)
    return scores, vt, no_match


def test_gaia_like_separations_drive_pc1():
    _, vt, _ = gaia_like()
    assert np.abs(vt[0, :2]).min() > 0.6


@pytest.mark.xfail(strict=True, reason="nothing else says where an object with no Gaia match belongs on a "
                   "Gaia axis, so they all sit at its center (faded on the page); counting no match as far would place them")
def test_objects_missing_a_feature_do_not_collapse():
    scores, _, no_match = gaia_like()
    # objects without a Gaia match should still spread along PC1 somewhat, not sit on one line
    assert scores[no_match, 0].std() > 0.25 * scores[~no_match, 0].std()
