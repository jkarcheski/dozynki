"""Ways to fill the labeling queue. Each records its selection function in
queue.source / queue.source_params so the training set's biases are known.

Babamul Kafka topics and user-supplied object lists are planned additions.
"""

import random

from . import db
from .brokers import antares
from .filters import Filters


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


SOURCES = {"antares_random_lsst": antares_random_lsst}
