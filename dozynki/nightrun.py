"""Filtered mode: run a whole night through a saved filter, like ANTARES's per-night counts.

A night is every LSST object (ANTARES locus with a diaObject) whose first or latest alert
came during it. The listing has no other alert times, so for a past night this misses
objects that alerted on it and again later; for the night just gone it is every object
that alerted. (Asking only for "first before the night ends, latest after it starts" takes
in everything active around it: 435,552 objects for 2026-07-11, against 50,052.) Two stages:

1. The rules the locus listing answers (rejection.LISTING_ES / LISTING_LOCAL) run over the
   whole night at once: ANTARES applies some in its query, the rest run here on each listing.
   That gives the night's "N of M pass" right away.
2. Everything left is queued as 'screening', in a random order, and screen.py fetches each
   object and applies the whole pipeline, so the slow rules remove objects before you get to them.

ANTARES counts and pages only 10,000 matches of a query, so the night is cut into RA slices
small enough to page through.
"""

import random
import threading
from concurrent.futures import ThreadPoolExecutor

from . import db, rejection, screen, sources
from .brokers import antares

PAGE = 1000
WORKERS = 4
SOURCE = "antares_night_filtered"

_lock = threading.Lock()
_running = None  # run id while one is going

# a run the server stopped in the middle of never finishes
db.execute("UPDATE night_runs SET state = 'error', error = 'interrupted: the server stopped during the run', "
           "finished_at = ? WHERE state NOT IN ('done', 'error')", (db.now(),))


def night_filters(night):
    lo, hi = sources.night_mjd_range(night)
    in_night = {"gte": lo, "lt": hi}
    return [{"exists": {"field": "properties.survey.lsst.dia_object_id"}},
            {"bool": {"should": [{"range": {"properties.oldest_alert_observation_time": in_night}},
                                 {"range": {"properties.newest_alert_observation_time": in_night}}],
                      "minimum_should_match": 1}}]


def _ra(lo, hi):
    return {"range": {"ra": {"gte": lo, "lt": hi}}}


def _slices(filters, lo=0.0, hi=360.0):
    """[(ra_lo, ra_hi, count)] covering the sky, each slice under ANTARES's 10,000 window."""
    n = antares.count_loci(filters + [_ra(lo, hi)])
    if n < sources.ES_WINDOW or hi - lo < 1e-4:
        return [(lo, hi, n)] if n else []
    mid = (lo + hi) / 2
    return _slices(filters, lo, mid) + _slices(filters, mid, hi)


def _list(filters, slices):
    """Every locus in the slices with an LSST diaObject, once each."""
    pages = [(lo, hi, off) for lo, hi, n in slices for off in range(0, min(n, sources.ES_WINDOW), PAGE)]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = pool.map(lambda p: antares.list_loci(filters + [_ra(p[0], p[1])], p[2], PAGE), pages)
        seen = {}
        for page in results:
            for loc in page:
                if loc["dia_object_id"]:
                    seen.setdefault(loc["locus_id"], loc)
    return list(seen.values())


def _run(run_id, night, scr, seed):
    global _running
    try:
        base = night_filters(night)
        whole = _slices(base)
        db.update_night_run(run_id, n_night=sum(n for *_, n in whole), state="listing")
        es, local, _ = rejection.listing_split(scr["pipeline"])
        filters = base + es
        slices = _slices(filters) if es else whole
        db.update_night_run(run_id, n_listed=sum(n for *_, n in slices))
        loci = _list(filters, slices)
        feats = rejection.listing_features(loci)
        passed = [loc for loc, f in zip(loci, feats)
                  if not any(rejection.RULES[s["rule"]].test(f, **{k: v for k, v in s.items() if k != "rule"}) for s in local)]
        random.Random(seed).shuffle(passed)
        lo, hi = sources.night_mjd_range(night)
        params = {"night": night, "mjd_range": [lo, hi], "run": run_id, "screen": scr, "seed": seed,
                  "unit": "object (ANTARES locus) alerting that night that passed the filter's listing rules"}
        db.add_many_screening([(loc["dia_object_id"], loc["locus_id"], loc["ra"], loc["dec"], SOURCE, params, loc)
                               for loc in passed], scr["name"])
        db.update_night_run(run_id, n_passed=len(passed), object_ids=[str(loc["dia_object_id"]) for loc in passed],
                            state="done")
        screen.start()
    except Exception as e:
        db.update_night_run(run_id, state="error", error=f"{type(e).__name__}: {e}")
    finally:
        with _lock:
            _running = None


def start(night, scr):
    """Run a night through a screen (screen.resolve) in the background; returns the run id."""
    global _running
    sources.night_mjd_range(night)  # ValueError on a bad date
    with _lock:
        if _running is not None:
            raise RuntimeError("a night is already being filtered")
        seed = random.randrange(2**31)
        _running = run_id = db.add_night_run(night, scr["name"], scr["version"], scr["pipeline"], seed)
    threading.Thread(target=_run, args=(run_id, night, scr, seed), daemon=True).start()
    return run_id
