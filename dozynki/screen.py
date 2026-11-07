"""Filtered mode: screen pulled objects with a saved filter before they reach the vetter.

A filtered-mode pull queues objects as 'screening', with the pipeline stored in their
source_params (so later edits to the saved filter don't change what a pull meant). A
background thread fetches what the rules read, all of it stored by brokers (ANTARES's object,
alerts and catalog matches; ALeRCE, and Legacy Survey hosts, only for a rule that reads them;
not the rest of our crossmatch, which waits until the object is opened), applies the pipeline, and moves it
to 'pending' (kept) or 'filtered' (rejected; screen_result names the step, and what was fetched
is dropped: db.forget_rejected). An object that can't be fetched goes to 'pending' with screen_result 'error',
so a broker outage hides nothing. Random mode never serves screened objects.
Objects from a whole-night run (nightrun.py) are also filtered out when none of their
detections fall on the night. The archival search (archival.py, 5-30 s an object) runs only for a
filter with an archival rule, and only once every other rule has kept the object.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from . import archival, assemble, db, details, rejection
from .brokers import alerce
from .config import LABELER

WORKERS = 8
FETCH_FOR = {"alerce_stamp_bogus": "alerce", "alerce_stamp_agn": "alerce", "nuclear": "ls_hosts", "not_nuclear": "ls_hosts"}  # fetched only for these rules
ARCHIVAL_RULES = {"ps1_persistent", "no_brighter_than_ps1", "ztf_archival_variable"}  # fetched last, only if still kept
_lock = threading.Lock()
_running = False


def resolve(name):
    """The screen recorded with a pull: saved filter name, its pipeline then, its version, and whose
    labels its label rules read (the filter's owner)."""
    config = db.get_saved_pipeline(name)
    if config is None:
        raise KeyError(name)
    pipeline = rejection.resolve(config)
    _, owner = db.saved_pipeline_owner(name)
    return {"name": name, "pipeline": pipeline, "version": rejection.version(pipeline), "labeler": owner or LABELER}


def _latest_label(object_id, labeler):
    """What rejection.apply's label rules read: {} if unlabeled, None for a screen recorded without a labeler."""
    if labeler is None:
        return None
    labels = db.labels_for(object_id, labeler)
    return labels[-1] if labels else {}


def _fetch(object_id, pipeline):
    """Cache what the pipeline's rules read; the vetter's assemble.load fetches the rest later."""
    item = db.get_queue_item(object_id)
    ant = assemble._cached(object_id, "antares", lambda: assemble._fetch_antares(item))["data"]
    needs = {FETCH_FOR.get(s["rule"]) for s in pipeline}
    details.ensure(object_id, with_hosts="ls_hosts" in needs)
    if "alerce" in needs:
        assemble._cached(object_id, "alerce", lambda: alerce.fetch(object_id, ant.get("ztf_object_id")))
    return assemble._gather(object_id)


def _screen_one(row):
    oid = row["object_id"]
    params = json.loads(row["source_params"] or "{}")
    screen = params["screen"]
    try:
        raw = _fetch(oid, screen["pipeline"])
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
    label = _latest_label(oid, screen.get("labeler"))
    decisions = rejection.apply(feats, screen["pipeline"], label)
    if raw["archival"] is None and any(s["rule"] in ARCHIVAL_RULES for s in screen["pipeline"]) \
            and not rejection.first_rejection(decisions):
        ant = raw["antares"]["data"]
        try:
            assemble._cached(oid, "archival", lambda: archival.run(ant["ra"], ant["dec"]))
            decisions = rejection.apply(rejection.features(oid), screen["pipeline"], label)
        except Exception:
            pass  # no archival data: those rules see none, and keep it
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
