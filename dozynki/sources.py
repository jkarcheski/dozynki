"""Ways to fill the labeling queue. Each records its selection function in
queue.source / queue.source_params so the training set's biases are known.

User-supplied object lists live in lists.py; Babamul Kafka topics are a planned addition.
pittgoogle_random_lsst is a prototype: it needs a Google Cloud project (see brokers/pittgoogle.py).
ztf_random samples ZTF objects, queued under their ZTF object id, for when LSST isn't observing.
"""

import dataclasses
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests

from . import db
from .brokers import alerce, antares, pittgoogle
from .filters import MJD_UNIX_EPOCH, Filters

ES_WINDOW = 10000  # ANTARES counts and pages only this many matches of a query
ZTF_DEFAULT_DAYS = 3  # ZTF's archive goes back to 2018: without a date range, sample objects first seen this recently
ZTF_PAGE = 100
ZTF_MIN_STRATUM = 1 / 96  # days: don't split a window ALeRCE can't count below 15 minutes


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


def pittgoogle_random_lsst(n=50, filters=None, seed=None, screen=None):
    """Like antares_random_lsst, over the diaObjects in Pitt-Google's alert archive instead of ANTARES's loci.

    The ANTARES locus is looked up when the object is first assembled, so vetting still needs ANTARES.
    """
    filters = filters or Filters()
    seed = seed if seed is not None else random.randrange(2**31)
    sql, sql_params, objects = pittgoogle.random_lsst_objects(n, seed, filters)
    params = {"seed": seed, "filters": filters.as_params(), "n_requested": n, "sql": sql, "sql_params": sql_params,
              "unit": "object (LSST diaObject in Pitt-Google's archive), uniform within the filters; "
                      "min_det counts LSST detections only"}
    source = "pittgoogle_random_lsst"
    if screen:
        params["screen"], source = screen, "pittgoogle_random_lsst_screened"
    added = 0
    for obj in objects:
        added += db.add_to_queue(obj["dia_object_id"], None, obj["ra"], obj["dec"],
                                 source, params, summary=obj, screen=screen and screen["name"])
    return {"source": source, "seed": seed, "returned": len(objects), "added": added}


def ztf_random(n=50, filters=None, seed=None, screen=None):
    """Uniform random ZTF objects, from ANTARES's loci, or from ALeRCE's ZTF objects when ANTARES's search fails.

    Without a date range in the filter bar, only objects first detected in the last ZTF_DEFAULT_DAYS days.
    The ALeRCE fallback always selects by first detection (date_mode "first").
    """
    filters = filters or Filters()
    if filters.date_from is None and filters.date_to is None:
        today = datetime.now(timezone.utc).date()
        filters = dataclasses.replace(filters, date_from=(today - timedelta(days=ZTF_DEFAULT_DAYS)).isoformat(),
                                      date_to=today.isoformat(), date_mode="first")
    seed = seed if seed is not None else random.randrange(2**31)
    params = {"seed": seed, "filters": filters.as_params(), "n_requested": n}
    try:
        es_query, loci = antares.random_ztf_loci(n, seed, extra_filters=filters.es_filters())
        params |= {"es_query": es_query, "unit": "object (ANTARES locus with a ZTF object), uniform within the filters"}
        objects = [{**loc, "object_id": loc["ztf_object_id"]} for loc in loci if loc["ztf_object_id"]]
        params["broker"] = "antares"
    except requests.RequestException as e:
        objects, capped = _alerce_ztf_sample(n, seed, filters)
        params |= {"broker": "alerce", "antares_error": str(e), "capped": capped,
                   "unit": "object (ALeRCE ZTF object), uniform over first detection in the date range and min_det"}
    source = "ztf_random"
    if screen:
        params["screen"], source = screen, "ztf_random_screened"
    added = 0
    for obj in objects:
        added += db.add_to_queue(obj["object_id"], obj.get("locus_id"), obj["ra"], obj["dec"],
                                 source, params, summary=obj, screen=screen and screen["name"])
    return {"source": source, "seed": seed, "returned": len(objects), "added": added, "broker": params["broker"],
            "capped": params.get("capped", False)}


def _alerce_ztf_sample(n, seed, filters):
    """n ZTF objects drawn uniformly from ALeRCE's, by first detection in the filters' date range.

    ALeRCE stops counting at COUNT_CAP, so the window is split until each piece can be counted; a piece
    still at the cap after ZTF_MIN_STRATUM is sampled as if it held only COUNT_CAP (capped=True).
    """
    lo, hi = filters.mjd_range()
    strata, capped = [], False
    todo = [(lo + (hi - lo) * i / 8, lo + (hi - lo) * (i + 1) / 8) for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        while todo:
            counts = list(pool.map(lambda w: alerce.ztf_count(*w, filters.min_det), todo))
            split = []
            for (a, b), count in zip(todo, counts):
                if count >= alerce.COUNT_CAP and b - a > ZTF_MIN_STRATUM:
                    split += [(a, (a + b) / 2), ((a + b) / 2, b)]
                    continue
                capped |= count >= alerce.COUNT_CAP
                if count:
                    strata.append((a, b, count))
            todo = split
    total = sum(c for *_, c in strata)
    picks = sorted(random.Random(seed).sample(range(total), min(n, total)))
    pages, start = {}, 0
    for a, b, count in sorted(strata):
        for i in picks:
            if start <= i < start + count:
                pages.setdefault((a, b, (i - start) // ZTF_PAGE + 1), []).append((i - start) % ZTF_PAGE)
        start += count
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = pool.map(lambda key: alerce.ztf_page(key[0], key[1], filters.min_det, key[2], ZTF_PAGE), pages)
    out = [{**items[r], "object_id": items[r]["ztf_object_id"]}
           for items, rows in zip(fetched, pages.values()) for r in rows if r < len(items)]
    random.Random(seed).shuffle(out)
    return out, capped


SOURCES = {"antares_random_lsst": antares_random_lsst, "pittgoogle_random_lsst": pittgoogle_random_lsst,
           "ztf_random": ztf_random}
