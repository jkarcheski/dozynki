"""Coverage: how much of each Rubin night's LSST objects has been vetted (the landing page's calendar).

A night's size is the number of LSST objects whose first ANTARES alert came on it, as "Sample night"
defines a night, counted in RA slices past ANTARES's 10,000 cap (nightrun._slices) and kept in
night_sizes. A vetted object counts toward the night of queue.first_mjd, the same ANTARES field
(not the light curve, whose earliest point can be weeks off it), once, whoever vetted it.

Counting runs in a background thread, newest night first. The calendar starts with the first month
holding at least START_SHARE of the queued objects' first alerts: the random sample reaches back to the
start of LSST alerts, but ANTARES's first alert includes ZTF, so a few objects date from 2018 on. (Those,
like every object with an earlier ZTF alert, belong to no LSST night here, in either count.) A night is
recounted until two days after it ends, since late alerts can still add objects.
"""

import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from . import db, nightrun, sources
from .brokers import antares
from .filters import MJD_UNIX_EPOCH

START_SHARE = 0.005
SETTLE_DAYS = 2  # after this long past a night, its count is final
RECOUNT_HOURS = 6  # how often a night that isn't final is counted again
RETRY_MINUTES = 10  # after ANTARES fails, wait this long before trying again

_lock = threading.Lock()
_state = {"running": False, "error": None, "failed_at": 0.0}


def night_of(mjd):
    """A Rubin dayObs (noon to noon UTC) for an MJD."""
    return (date(1970, 1, 1) + timedelta(days=int(mjd - 0.5 - MJD_UNIX_EPOCH))).isoformat()


def _span():
    months = db.query("SELECT month, n FROM (SELECT substr(date(first_mjd - 0.5 + 2400000.5), 1, 7) month, COUNT(*) n "
                      "FROM queue WHERE first_mjd IS NOT NULL GROUP BY month) ORDER BY month")
    total = sum(m["n"] for m in months)
    first = next((m["month"] for m in months if m["n"] >= START_SHARE * total), None)
    if first is None:
        return []
    start = date.fromisoformat(first + "-01")
    today = date.fromisoformat(night_of(time.time() / 86400 + MJD_UNIX_EPOCH))
    return [(start + timedelta(days=i)).isoformat() for i in range((today - start).days + 1)]


def _stale(night, counted_at):
    counted = datetime.fromisoformat(counted_at)
    settled = datetime.fromisoformat(night).replace(tzinfo=timezone.utc) + timedelta(days=1 + SETTLE_DAYS, hours=12)
    return counted < settled and datetime.now(timezone.utc) - counted > timedelta(hours=RECOUNT_HOURS)


def count_night(night):
    lo, hi = sources.night_mjd_range(night)
    filters = [{"exists": {"field": "properties.survey.lsst.dia_object_id"}},
               {"range": {"properties.oldest_alert_observation_time": {"gte": lo, "lt": hi}}}]
    return sum(n for _, _, n in nightrun._slices(filters))


def _to_count(nights):
    have = {r["night"]: r["counted_at"] for r in db.query("SELECT night, counted_at FROM night_sizes")}
    return [n for n in reversed(nights) if n not in have or _stale(n, have[n])]


def _run(nights):
    try:
        # vetted objects queued before first_mjd was stored don't know their night yet
        for row in db.query("SELECT object_id, antares_locus FROM queue WHERE first_mjd IS NULL "
                            "AND object_id IN (SELECT object_id FROM labels)"):
            db.set_summary(row["object_id"], antares.locus_summary(row["antares_locus"]))
        for night in _to_count(nights):
            db.execute("INSERT OR REPLACE INTO night_sizes (night, n_objects, counted_at) VALUES (?, ?, ?)",
                       (night, count_night(night), db.now()))
        _state["error"] = None
    except Exception as e:
        _state["error"], _state["failed_at"] = f"{type(e).__name__}: {e}", time.time()
    finally:
        _state["running"] = False


def start(nights):
    """Count what's missing or stale in the background, unless that's going or recently failed."""
    with _lock:
        if _state["running"] or time.time() - _state["failed_at"] < RETRY_MINUTES * 60:
            return
        if not _to_count(nights) and not db.query("SELECT 1 FROM queue WHERE first_mjd IS NULL "
                                                  "AND object_id IN (SELECT object_id FROM labels) LIMIT 1"):
            return
        _state["running"] = True
    threading.Thread(target=_run, args=(nights,), daemon=True).start()


def summary(labeler):
    """Each night's size and how many of its objects are vetted (by anyone, and by labeler), plus totals
    over the nights counted so far."""
    nights = _span()
    start(nights)
    sizes = {r["night"]: r["n_objects"] for r in db.query("SELECT night, n_objects FROM night_sizes")}
    vetted, mine = defaultdict(int), defaultdict(int)
    unknown = {"vetted": 0, "mine": 0}
    for r in db.query("SELECT q.first_mjd, MAX(COALESCE(l.labeler, 'anonymous') = ?) mine FROM labels l "
                      "JOIN queue q USING (object_id) GROUP BY l.object_id", (labeler,)):
        if r["first_mjd"] is None:
            unknown["vetted"] += 1
            unknown["mine"] += r["mine"]
            continue
        night = night_of(r["first_mjd"])
        vetted[night] += 1
        mine[night] += r["mine"]
    rows = [{"night": n, "size": sizes.get(n), "vetted": vetted[n], "mine": mine[n]} for n in nights]
    counted = [r for r in rows if r["size"] is not None]
    return {
        "nights": rows,
        "total": {"size": sum(r["size"] for r in counted), "vetted": sum(r["vetted"] for r in counted),
                  "mine": sum(r["mine"] for r in counted), "nights_counted": len(counted), "nights": len(rows)},
        "unknown_night": unknown,
        "counting": _state["running"],
        "error": _state["error"],
    }
