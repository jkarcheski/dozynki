"""Ways to fill the labeling queue. Each records its selection function in
queue.source / queue.source_params so the training set's biases are known.

Babamul Kafka topics and user-supplied object lists are planned additions.
"""

import random
from collections import defaultdict
from datetime import date

from . import db
from .brokers import antares
from .filters import MJD_UNIX_EPOCH, Filters

ES_WINDOW = 10000  # ANTARES counts and pages only this many matches of a query


def antares_random_lsst(n=50, filters=None, seed=None):
    filters = filters or Filters()
    seed = seed if seed is not None else random.randrange(2**31)
    es_query, loci = antares.random_lsst_loci(n, seed, extra_filters=filters.es_filters())
    params = {"seed": seed, "filters": filters.as_params(), "n_requested": n, "es_query": es_query,
              "unit": "object (ANTARES locus), uniform within the filters"}
    added = 0
    for loc in loci:
        if loc["dia_object_id"]:
            added += db.add_to_queue(loc["dia_object_id"], loc["locus_id"], loc["ra"], loc["dec"],
                                     "antares_random_lsst", params, summary=loc)
    return {"source": "antares_random_lsst", "seed": seed, "returned": len(loci), "added": added}


def night_mjd_range(night):
    """A Rubin night by its dayObs: the date in UTC-12, so noon UTC to noon UTC."""
    lo = (date.fromisoformat(night) - date(1970, 1, 1)).days + MJD_UNIX_EPOCH + 0.5
    return lo, lo + 1


def night_batches(night, min_det):
    """Earlier batches of one night at one detection minimum, oldest first."""
    return [p for p in db.night_pulls() if p["night"] == night and (p["filters"].get("min_det") or 1) == min_det]


def antares_night(night, n=50, filters=None):
    """The next n objects of one night, in a random order fixed on the first batch.

    The night is every LSST object whose first ANTARES alert came on it, within the
    filter bar's detection minimum (its date range is replaced by the night). Later
    batches at the same minimum continue the same order, so they never repeat and
    eventually cover the whole night when it has fewer than ES_WINDOW objects.
    """
    filters = Filters(min_det=(filters or Filters()).min_det)
    earlier = night_batches(night, filters.min_det)
    seed = earlier[0]["seed"] if earlier else random.randrange(2**31)
    start = earlier[-1]["start"] + len(earlier[-1]["object_ids"]) if earlier else 0
    lo, hi = night_mjd_range(night)
    n = max(0, min(n, ES_WINDOW - start))
    es_query, n_matching, loci = antares.night_lsst_loci(lo, hi, seed, start, n, extra_filters=filters.es_filters())
    params = {"night": night, "mjd_range": [lo, hi], "seed": seed, "start": start, "filters": filters.as_params(),
              "es_query": es_query, "unit": "object (ANTARES locus), uniform within the night and filters"}
    added, ids = 0, []
    for loc in loci:
        ids.append(str(loc["dia_object_id"]))
        added += db.add_to_queue(loc["dia_object_id"], loc["locus_id"], loc["ra"], loc["dec"],
                                 "antares_night", params, summary=loc)
    if ids:
        db.add_night_pull(night, filters.as_params(), ids, seed, start, n_matching)
    return {"source": "antares_night", "night": night, "returned": len(ids), "added": added,
            "taken": start + len(ids), "n_matching": n_matching, "capped": n_matching >= ES_WINDOW}


SOURCES = {"antares_random_lsst": antares_random_lsst}


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
                    "capped": size >= ES_WINDOW,
                    "done": 0 < size < ES_WINDOW and len(ids) >= size and n_vetted == len(ids)})
    return out
