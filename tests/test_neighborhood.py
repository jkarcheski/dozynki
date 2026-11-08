"""Checks for the haystack's neighborhood order (neighborhood.py): the block distances against similar.distances,
leaving an object out of its own neighbors, the entropy, and order()'s interleaving, on matrices and scores with
known answers.

Run with: .venv/bin/python -m pytest tests
"""

import math

import numpy as np
import pytest

from dozynki import neighborhood, similar

rng = lambda seed=0: np.random.default_rng(seed)


def with_holes(seed=0, shape=(60, 8), frac=0.3):
    X = rng(seed).normal(0, 1, shape)
    X[rng(seed + 1).random(shape) < frac] = np.nan
    return X


def test_block_matches_similar_distances():
    """Every row's distances are similar.distances' for that row, where enough features are shared."""
    X = with_holes()
    D = neighborhood.distance_block(X, X)
    for i in range(len(X)):
        d, n_shared, _ = similar.distances(X, i)
        enough = n_shared >= max(1, similar.MIN_SHARED * (~np.isnan(X[i])).sum())
        # the expanded square leaves rounding of ~1e-8 where the true distance is 0 (itself)
        assert np.allclose(D[i, enough], d[enough], atol=1e-6)
        assert np.isinf(D[i, ~enough]).all()


def test_nearest_leaves_itself_out():
    X = with_holes(seed=3, frac=0)
    idx, dist = neighborhood.nearest(X, X, self_cols=np.arange(len(X)), k=5)
    assert not (idx == np.arange(len(X))[:, None]).any()
    assert (np.diff(dist, axis=1) >= 0).all()
    # the same as brute force
    D = neighborhood.distance_block(X, X)
    np.fill_diagonal(D, np.inf)
    assert np.allclose(dist, np.sort(D, axis=1)[:, :5])


def test_nearest_across_chunks(monkeypatch):
    monkeypatch.setattr(neighborhood, "CHUNK", 7)
    X = with_holes(seed=5, frac=0)
    a = neighborhood.nearest(X, X, k=4)[1]
    monkeypatch.setattr(neighborhood, "CHUNK", 1000)
    assert np.allclose(a, neighborhood.nearest(X, X, k=4)[1])


def test_entropy():
    assert neighborhood.entropy(["agn"] * 10) == 0
    assert neighborhood.entropy(["agn", "star"] * 5) == pytest.approx(math.log(2))
    assert neighborhood.entropy([]) == 0


def fake_scores(monkeypatch, table):
    """table: {oid: (n_transient, far, mixed)}, all with k = 10."""
    monkeypatch.setattr(neighborhood, "scores", lambda ids: {
        oid: {"k": 10, "n_transient": t, "far": f, "mixed": m, "mean_distance": 1.0}
        for oid, (t, f, m) in table.items() if oid in ids})


def test_order_by_transient_leaves_every_tenth_to_the_farthest(monkeypatch):
    table = {f"t{i}": (10 - i % 10, 0.1, 0) for i in range(20)}  # transient-like, close to labeled
    table["odd"] = (0, 0.99, 0)
    fake_scores(monkeypatch, table)
    ids = ["unscored", *table]
    out = neighborhood.order(ids, "transient")
    assert out[9] == "odd"
    assert out[0] in ("t0", "t10") and out[-1] == "unscored"
    assert sorted(out) == sorted(ids)


def test_order_by_far_and_mixed(monkeypatch):
    fake_scores(monkeypatch, {"a": (5, 0.2, 0.9), "b": (5, 0.9, 0.1), "c": (5, 0.5, 0.9)})
    assert neighborhood.order(["a", "b", "c"], "far") == ["b", "c", "a"]
    assert neighborhood.order(["a", "b", "c"], "mixed") == ["c", "a", "b"]  # ties on mixed: farther first


def test_order_rejects_unknown_sort(monkeypatch):
    fake_scores(monkeypatch, {"a": (1, 0, 0)})
    with pytest.raises(ValueError):
        neighborhood.order(["a"], "bogus")
