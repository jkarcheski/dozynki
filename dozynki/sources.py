"""Ways to fill the labeling queue. Each records its selection function in
queue.source / queue.source_params so the training set's biases are known.

Babamul Kafka topics and user-supplied object lists are planned additions.
"""

import random
from datetime import date

from . import db
from .brokers import antares
from .filters import MJD_UNIX_EPOCH, Filters

NIGHT_CAP = 2000  # most objects one night pull may queue; tighten the filters for busier nights


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


def antares_night(night, filters=None, cap=NIGHT_CAP):
    """Every LSST object whose first ANTARES alert came on one night, within the
    detection filter (the filter bar's date range is replaced by the night).
    The pull is recorded in night_pulls so the night can be finished later."""
    filters = Filters(min_det=(filters or Filters()).min_det)
    lo, hi = night_mjd_range(night)
    es_query, n, loci = antares.night_lsst_loci(lo, hi, cap, extra_filters=filters.es_filters())
    if n > cap:
        raise ValueError(f"{'over ' if n >= 10000 else ''}{n} objects first alerted on {night} with ≥ {filters.min_det} "
                         f"detections; raise the detection filter to bring it under {cap}")
    params = {"night": night, "mjd_range": [lo, hi], "filters": filters.as_params(), "es_query": es_query,
              "unit": "object (ANTARES locus), every one first alerting that night within the filters"}
    added, ids = 0, []
    for loc in loci:
        if loc["dia_object_id"]:
            ids.append(str(loc["dia_object_id"]))
            added += db.add_to_queue(loc["dia_object_id"], loc["locus_id"], loc["ra"], loc["dec"],
                                     "antares_night", params, summary=loc)
    db.add_night_pull(night, filters.as_params(), ids)
    return {"source": "antares_night", "night": night, "returned": len(ids), "added": added}


SOURCES = {"antares_random_lsst": antares_random_lsst}
