"""Haystack order by neighborhood: how an object's nearest labeled objects were labeled, with no trained model.

Uses similar.py's feature space and distance (rms of robustly scaled differences over the features both objects
have, similar.DEFAULT_GROUPS) over every queued object with features. Each object's K nearest labeled objects
(never itself; anyone's latest label) give three numbers:

- transient: how many of the K are labeled transient (primary or "could also be"); lift is that share over the
  labeled pool's, so 2 means twice as transient-heavy as labeled objects overall.
- far: how the mean distance to the K compares with labeled objects' own (each without itself): the share of
  labeled objects whose K nearest labeled are closer. Near 1, few labeled objects look like it: where something
  new would sit.
- mixed: the entropy of the K's primary classes over the most it could be. Near 1, the neighbors disagree, the
  way a confused classifier would.

order() sorts a haystack by one of them. Sorting by transient gives every EXPLORE_EVERY-th place to the farthest
object not yet placed, so what has no labeled lookalikes still comes up.

The labels only rank; nothing is fit to them, and K and EXPLORE_EVERY are round numbers, not tuned.
"""

import math
import threading
from collections import Counter

import numpy as np

from . import db, similar, tns
from .evaluate import is_target

K = 10
EXPLORE_EVERY = 10
SORTS = ("transient", "far", "mixed")
CHUNK = 1000  # query rows per distance block, to keep memory flat

_lock = threading.Lock()
_memo = {}


def distance_block(Q, R):
    """Each row of Q's distance to each row of R: rms of the differences over the features both have, as
    similar.distances, with inf where they share fewer than similar.MIN_SHARED of Q's row's features."""
    qm, rm = ~np.isnan(Q), ~np.isnan(R)
    q0, r0 = np.where(qm, Q, 0.0), np.where(rm, R, 0.0)
    shared = qm.astype(float) @ rm.T
    d2 = (q0 ** 2) @ rm.T - 2 * q0 @ r0.T + qm @ (r0 ** 2).T
    with np.errstate(invalid="ignore", divide="ignore"):
        d = np.sqrt(np.clip(d2, 0, None) / shared)
    enough = shared >= np.maximum(1, similar.MIN_SHARED * qm.sum(axis=1))[:, None]
    return np.where(enough, d, np.inf)


def nearest(Q, R, self_cols=None, k=K):
    """(indices into R, distances) of each Q row's k nearest R rows, nearest first; self_cols[i] is a column of R
    to leave out for row i (itself), or -1. Fewer than k valid neighbors leave inf distances."""
    idx, dist = [], []
    for lo in range(0, len(Q), CHUNK):
        d = distance_block(Q[lo:lo + CHUNK], R)
        if self_cols is not None:
            rows = np.flatnonzero(self_cols[lo:lo + CHUNK] >= 0)
            d[rows, self_cols[lo:lo + CHUNK][rows]] = np.inf
        kk = min(k, d.shape[1])
        part = np.argpartition(d, kk - 1, axis=1)[:, :kk]
        pd = np.take_along_axis(d, part, axis=1)
        order = np.argsort(pd, axis=1)
        idx.append(np.take_along_axis(part, order, axis=1))
        dist.append(np.take_along_axis(pd, order, axis=1))
    return (np.vstack(idx), np.vstack(dist)) if idx else (np.zeros((0, 0), int), np.zeros((0, 0)))


def entropy(classes):
    """Shannon entropy of a list of class keys (nats)."""
    n = len(classes)
    return -sum(c / n * math.log(c / n) for c in Counter(classes).values()) if n else 0.0


def _state():
    """What the memo depends on: the labels and the cached ANTARES payloads features come from."""
    lab = db.query("SELECT COUNT(*) n, MAX(id) m FROM labels")[0]
    ant = db.query("SELECT COUNT(*) n, MAX(fetched_at) m FROM cache WHERE kind = 'antares'")[0]
    return lab["n"], lab["m"], ant["n"], ant["m"]


def _model():
    """The feature matrix, the labeled pool, and labeled objects' own mean distance to their K nearest (sorted),
    rebuilt when labels or cached payloads change."""
    key = _state()
    with _lock:
        if _memo.get("key") == key:
            return _memo
        m = similar.build(truth="final", all_sources=True, survey="both")
        ids, X, labels = m["ids"], m["X"], m["labels"]
        ref = np.array([i for i, oid in enumerate(ids) if oid in labels], dtype=int)
        ref_labels = [labels[ids[i]] for i in ref]
        transient = np.array([is_target(l, {"transient"}, include_alt=True) for l in ref_labels])
        klass = [l["klass"] for l in ref_labels]
        _, d = nearest(X[ref], X[ref], self_cols=np.arange(len(ref))) if len(ref) else (None, np.zeros((0, 0)))
        own = _mean_finite(d)
        own = np.sort(own[np.isfinite(own)])
        _memo.clear()
        _memo.update(key=key, ids=ids, row={oid: i for i, oid in enumerate(ids)}, X=X, ref=ref, transient=transient,
                     klass=klass, base=float(transient.mean()) if len(ref) else 0.0, own=own,
                     n_classes=len(set(klass)))
        return _memo


def warm():
    """Build the model in the background, so the first sorted haystack doesn't wait for every object's features."""
    threading.Thread(target=_model, daemon=True).start()


def _mean_finite(d):
    with np.errstate(invalid="ignore"):
        return np.array([row[np.isfinite(row)].mean() if np.isfinite(row).any() else np.inf for row in d])


def scores(object_ids):
    """{object_id: {"k", "n_transient", "lift", "far", "mixed", "classes", "neighbors"}} for the objects with
    features; neighbors are (pool position, distance) pairs, nearest first. Objects without any labeled
    neighbor sharing enough features are left out."""
    m = _model()
    rows = [m["row"][oid] for oid in object_ids if oid in m["row"]]
    if not rows or not len(m["ref"]):
        return {}
    pos = {r: p for p, r in enumerate(m["ref"])}
    idx, dist = nearest(m["X"][rows], m["X"][m["ref"]], self_cols=np.array([pos.get(r, -1) for r in rows]))
    max_h = math.log(min(K, m["n_classes"])) if min(K, m["n_classes"]) > 1 else 1.0
    out = {}
    for r, ii, dd in zip(rows, idx, dist):
        ok = np.isfinite(dd)
        if not ok.any():
            continue
        ii, dd = ii[ok], dd[ok]
        n_t = int(m["transient"][ii].sum())
        classes = [m["klass"][j] for j in ii]
        share = n_t / len(ii)
        out[m["ids"][r]] = {
            "k": len(ii), "n_transient": n_t,
            "lift": share / m["base"] if m["base"] else None,
            "far": float(np.searchsorted(m["own"], dd.mean()) / len(m["own"])) if len(m["own"]) else None,
            "mixed": abs(entropy(classes)) / max_h,
            "classes": dict(Counter(classes).most_common()),
            "neighbors": list(zip(ii.tolist(), dd.tolist())),
            "mean_distance": float(dd.mean()),
        }
    return out


def _key(sort):
    if sort == "transient":
        return lambda s: (-s["n_transient"] / s["k"], s["mean_distance"])
    if sort == "far":
        return lambda s: -s["far"]
    if sort == "mixed":
        return lambda s: (-s["mixed"], -s["far"])
    raise ValueError(f"sort must be one of {SORTS}")


def order(object_ids, sort):
    """object_ids sorted by sort (one of SORTS); objects without a score keep their order, after the rest."""
    s = scores(object_ids)
    scored = sorted((oid for oid in object_ids if oid in s), key=lambda oid: _key(sort)(s[oid]))
    if sort == "transient" and EXPLORE_EVERY:
        far = sorted(scored, key=lambda oid: _key("far")(s[oid]))
        placed, out, a, b = set(), [], iter(scored), iter(far)
        while len(out) < len(scored):
            src = b if (len(out) + 1) % EXPLORE_EVERY == 0 else a
            for oid in src:
                if oid not in placed:
                    placed.add(oid)
                    out.append(oid)
                    break
        scored = out
    return scored + [oid for oid in object_ids if oid not in s]


def explain(object_id):
    """One object's neighborhood for the vetting page: the three numbers, its K nearest labeled objects with their
    labels and TNS names, and the pool it was compared with. None without features or labeled neighbors."""
    s = scores([object_id]).get(object_id)
    if not s:
        return None
    m = _model()
    matched = tns.cached()
    neighbors = []
    for j, d in s["neighbors"]:
        oid = m["ids"][m["ref"][j]]
        t = matched.get(oid)
        neighbors.append({"object_id": oid, "klass": m["klass"][j], "transient": bool(m["transient"][j]),
                          "distance": round(d, 3), "tns": tns.summary(t) if t else None})
    return {**{k: s[k] for k in ("k", "n_transient", "lift", "far", "mixed", "classes")},
            "neighbors": neighbors, "pool": len(m["ref"]), "base": m["base"]}
