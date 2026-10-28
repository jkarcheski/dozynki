"""Filtered mode: screen pulled objects with a saved filter before they reach the vetter.

A filtered-mode pull queues objects as 'screening', with the pipeline stored in their
source_params (so later edits to the saved filter don't change what a pull meant). A
background thread fetches each one (the same fetch the vetter would make), applies the
pipeline, and moves it to 'pending' (kept) or 'filtered' (rejected; screen_result names
the step). An object that can't be fetched goes to 'pending' with screen_result 'error',
so a broker outage hides nothing. Random mode never serves screened objects.
Objects from a whole-night run (nightrun.py) are also filtered out when none of their
detections fall on the night.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from . import assemble, db, details, rejection

WORKERS = 4
_lock = threading.Lock()
_running = False


def resolve(name):
    """The screen recorded with a pull: saved filter name, its pipeline then, and its version."""
    config = db.get_saved_pipeline(name)
    if config is None:
        raise KeyError(name)
    pipeline = rejection.resolve(config)
    return {"name": name, "pipeline": pipeline, "version": rejection.version(pipeline)}


def _screen_one(row):
    oid = row["object_id"]
    params = json.loads(row["source_params"] or "{}")
    screen = params["screen"]
    try:
        raw = assemble.load(oid)
        details.ensure(oid)
        feats = rejection.features(oid)
    except Exception:
        feats = None
    if feats is None:
        db.set_screened(oid, "pending", "error")
        return
    night = params.get("mjd_range")  # a whole-night run (nightrun.py): the night's query also catches
    if night and not any(p["kind"] == "detection" and night[0] <= p["mjd"] < night[1]  # objects alerting around it
                         for p in raw["antares"]["data"]["points"]):
        db.set_screened(oid, "filtered", "no_alert_that_night")
        return
    decisions = rejection.apply(feats, screen["pipeline"])
    db.save_pipeline(screen["version"], screen["pipeline"])
    db.save_rejections(oid, screen["version"], decisions)
    first = rejection.first_rejection(decisions)
    db.set_screened(oid, "filtered" if first else "pending", first or "kept")


def _work():
    global _running
    try:
        while rows := db.query("SELECT object_id, source_params FROM queue WHERE status = 'screening' "
                               "ORDER BY added_at, rowid LIMIT 20"):
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                list(pool.map(_screen_one, rows))
    finally:
        with _lock:
            _running = False


def start():
    """Screen everything waiting, in the background; a no-op while a run is going."""
    global _running
    with _lock:
        if _running:
            return
        _running = True
    threading.Thread(target=_work, daemon=True).start()
