"""Ways to fill the labeling queue. Each records its selection function in
queue.source / queue.source_params so the training set's biases are known.

Babamul Kafka topics and user-supplied object lists are planned additions.
"""

import random
from datetime import date

from . import db
from .brokers import antares
from .filters import MJD_UNIX_EPOCH, Filters

ES_WINDOW = 10000  # ANTARES counts and pages only this many matches of a query


def antares_random_lsst(n=50, filters=None, seed=None, screen=None):
    filters = filters or Filters()
    seed = seed if seed is not None else random.randrange(2**31)
    es_query, loci = antares.random_lsst_loci(n, seed, extra_filters=filters.es_filters())
    params = {"seed": seed, "filters": filters.as_params(), "n_requested": n, "es_query": es_query,
              "unit": "object (ANTARES locus), uniform within the filters"}
    # a screened sample is no longer uniform, so it gets its own source and stays out of the filter lab's population
    source = "antares_random_lsst"
    if screen:
        params["screen"], source = screen, "antares_random_lsst_screened"
    added = 0
    for loc in loci:
        if loc["dia_object_id"]:
            added += db.add_to_queue(loc["dia_object_id"], loc["locus_id"], loc["ra"], loc["dec"],
                                     source, params, summary=loc, screen=screen and screen["name"])
    return {"source": source, "seed": seed, "returned": len(loci), "added": added}


def night_mjd_range(night):
    """A Rubin night by its dayObs: the date in UTC-12, so noon UTC to noon UTC."""
    lo = (date.fromisoformat(night) - date(1970, 1, 1)).days + MJD_UNIX_EPOCH + 0.5
    return lo, lo + 1


def night_batches(night, min_det, screen=None):
    """Earlier batches of one night at one detection minimum in one mode (screen: saved filter name), oldest first."""
    return [p for p in db.night_pulls() if p["night"] == night and (p["filters"].get("min_det") or 1) == min_det
            and p["filters"].get("screen") == screen]


def antares_night(night, n=50, filters=None, screen=None):
    """The next n objects of one night, in a random order fixed on the first batch.

    The night is every LSST object whose first ANTARES alert came on it, within the
    filter bar's detection minimum (its date range is replaced by the night). Later
    batches at the same minimum continue the same order, so they never repeat and
    eventually cover the whole night when it has fewer than ES_WINDOW objects.
    Filtered mode (screen) keeps its own order, so it never takes objects from random mode's.
    """
    filters = Filters(min_det=(filters or Filters()).min_det)
    name = screen and screen["name"]
    earlier = night_batches(night, filters.min_det, name)
    seed = earlier[0]["seed"] if earlier else random.randrange(2**31)
    start = earlier[-1]["start"] + len(earlier[-1]["object_ids"]) if earlier else 0
    lo, hi = night_mjd_range(night)
    n = max(0, min(n, ES_WINDOW - start))
    es_query, n_matching, loci = antares.night_lsst_loci(lo, hi, seed, start, n, extra_filters=filters.es_filters())
    params = {"night": night, "mjd_range": [lo, hi], "seed": seed, "start": start, "filters": filters.as_params(),
              "es_query": es_query, "unit": "object (ANTARES locus), uniform within the night and filters"}
    if screen:
        params["screen"] = screen
    added, ids = 0, []
    for loc in loci:
        ids.append(str(loc["dia_object_id"]))
        added += db.add_to_queue(loc["dia_object_id"], loc["locus_id"], loc["ra"], loc["dec"],
                                 "antares_night", params, summary=loc, screen=name)
    if ids:
        db.add_night_pull(night, {**filters.as_params(), **({"screen": name} if name else {})}, ids, seed, start, n_matching)
    return {"source": "antares_night", "night": night, "returned": len(ids), "added": added,
            "taken": start + len(ids), "n_matching": n_matching, "capped": n_matching >= ES_WINDOW}


SOURCES = {"antares_random_lsst": antares_random_lsst}
