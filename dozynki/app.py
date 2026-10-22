import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, assemble, db, export, sources
from .brokers import antares
from .config import BABAMUL_ENABLED, BLIND_DEFAULT, LABELER, XMATCH_MAX_RADIUS_ARCSEC, XMATCH_RADIUS_ARCSEC
from .labels import CLASS_BY_KEY, CLASSES, CONFIDENCE, FLAGS, TRANSIENT_SUBCLASSES, WATCH_FLAG

STATIC = Path(__file__).parent / "static"
PREFETCH = 3

app = FastAPI(title="DOZYNKI alert vetter")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
state = {"blind": BLIND_DEFAULT}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def get_config():
    return {
        "classes": CLASSES,
        "transient_subclasses": TRANSIENT_SUBCLASSES,
        "flags": FLAGS,
        "confidence": CONFIDENCE,
        "blind": state["blind"],
        "labeler": LABELER,
        "babamul_enabled": BABAMUL_ENABLED,
        "sources": list(sources.SOURCES),
        "xmatch_radius": XMATCH_RADIUS_ARCSEC,
        "version": __version__,
    }


class BlindToggle(BaseModel):
    blind: bool


@app.post("/api/config/blind")
def set_blind(body: BlindToggle):
    state["blind"] = body.blind
    return {"blind": state["blind"]}


class Fill(BaseModel):
    source: str = "antares_random_lsst"
    n: int = 50
    min_alerts: int = 1


@app.post("/api/queue/fill")
def fill(body: Fill):
    if body.source not in sources.SOURCES:
        raise HTTPException(400, f"unknown source {body.source}")
    result = sources.SOURCES[body.source](n=body.n, min_alerts=body.min_alerts)
    assemble.prefetch([r["object_id"] for r in db.next_pending(PREFETCH)])
    return result


class AddIds(BaseModel):
    object_ids: list[str]


@app.post("/api/queue/add")
def add_ids(body: AddIds):
    """Queue specific LSST diaObjectIds (selection source recorded as manual_list)."""
    added, missing = 0, []
    for oid in body.object_ids:
        loc = antares.locus_for_dia_object(oid.strip())
        if not loc:
            missing.append(oid)
            continue
        added += db.add_to_queue(loc["dia_object_id"], loc["locus_id"], loc["ra"], loc["dec"], "manual_list", {})
    return {"added": added, "not_in_antares": missing}


@app.get("/api/next")
def next_object(exclude: str = ""):
    skip = tuple(x for x in exclude.split(",") if x)
    rows = db.next_pending(PREFETCH + 1, exclude=skip)
    if not rows:
        return {"object_id": None}
    assemble.prefetch([r["object_id"] for r in rows[1:]])
    return {"object_id": rows[0]["object_id"]}


def _has_blind_label(object_id):
    return any(l["phase"] == "blind" for l in db.labels_for(object_id))


@app.get("/api/object/{object_id}")
def get_object(object_id: str, refresh: bool = False):
    item = db.get_queue_item(object_id)
    if not item:
        raise HTTPException(404, "not in queue")
    raw = assemble.load(object_id, refresh=refresh)
    ev = assemble.evidence(object_id, raw)
    labels = db.labels_for(object_id)
    revealed = not state["blind"] or _has_blind_label(object_id)
    return {
        "queue": item,
        "evidence": ev,
        "labels": labels,
        "opinions": assemble.opinions(raw) if revealed else None,
        "revealed": revealed,
    }


@app.get("/api/reveal/{object_id}")
def reveal(object_id: str):
    if state["blind"] and not _has_blind_label(object_id):
        raise HTTPException(403, "commit a blind label first")
    return assemble.opinions(assemble.load(object_id))


class Label(BaseModel):
    object_id: str
    klass: str
    subclass: str | None = None
    confidence: str | None = None
    flags: list[str] = []
    host: dict | None = None
    notes: str | None = None


@app.post("/api/label")
def post_label(body: Label):
    if body.klass not in CLASS_BY_KEY:
        raise HTTPException(400, f"unknown class {body.klass}")
    if not db.get_queue_item(body.object_id):
        raise HTTPException(404, "not in queue")
    previous = db.labels_for(body.object_id)
    if previous and WATCH_FLAG in json.loads(previous[-1]["flags"] or "[]"):
        phase = "followup"  # second look at a watched object, possibly with new data
    elif _has_blind_label(body.object_id):
        phase = "revealed"
    else:
        phase = "blind" if state["blind"] else "unblinded"
    raw = assemble.load(body.object_id)
    snap = assemble.snapshot(assemble.evidence(body.object_id, raw)) | {"blind_mode": state["blind"]}
    label_id = db.add_label(
        {
            "object_id": body.object_id,
            "phase": phase,
            "klass": body.klass,
            "subclass": body.subclass if body.klass == "transient" else None,
            "is_real": CLASS_BY_KEY[body.klass]["real"],
            "confidence": body.confidence,
            "flags": json.dumps(body.flags),
            "host": json.dumps(body.host) if body.host else None,
            "notes": body.notes,
            "labeler": LABELER,
            "created_at": db.now(),
            "snapshot": json.dumps(snap),
            "app_version": __version__,
        }
    )
    db.set_status(body.object_id, "watch" if WATCH_FLAG in body.flags else "labeled")
    return {"id": label_id, "phase": phase}


@app.post("/api/watch/recheck")
def watch_recheck():
    """Re-poll ANTARES for watched objects; any with new alerts go back to the queue."""
    updated, unchanged, errors = [], 0, {}
    for item in db.query("SELECT * FROM queue WHERE status = 'watch'"):
        oid = item["object_id"]
        try:
            old = db.cache_get(oid, "antares")
            n_old = (old["data"].get("num_alerts") or len(old["data"]["points"])) if old else 0
            n_new = antares.locus_num_alerts(item["antares_locus"])
            if n_new > n_old:
                assemble.refresh_brokers(oid)
                db.set_status(oid, "pending")
                updated.append({"object_id": oid, "new_alerts": n_new - n_old})
            else:
                unchanged += 1
        except Exception as e:
            errors[oid] = f"{type(e).__name__}: {e}"
    return {"requeued": updated, "unchanged": unchanged, "errors": errors}


@app.post("/api/skip/{object_id}")
def skip(object_id: str):
    db.set_status(object_id, "skipped")
    return {"ok": True}


@app.post("/api/xmatch/{object_id}")
def xmatch(object_id: str, radius: float = XMATCH_RADIUS_ARCSEC):
    if not db.get_queue_item(object_id):
        raise HTTPException(404, "not in queue")
    if not 0 < radius <= XMATCH_MAX_RADIUS_ARCSEC:
        raise HTTPException(400, f"radius must be in (0, {XMATCH_MAX_RADIUS_ARCSEC}] arcsec")
    assemble.load(object_id)
    return assemble.rerun_xmatch(object_id, radius)["data"]


@app.post("/api/skybot/{object_id}")
def skybot(object_id: str, refresh: bool = False):
    if not db.get_queue_item(object_id):
        raise HTTPException(404, "not in queue")
    assemble.load(object_id)
    return assemble.skybot(object_id, refresh=refresh)["data"]


@app.get("/api/thumbs/{alert_id}")
def thumbs(alert_id: str):
    return antares.thumbnails(alert_id)


@app.get("/api/stats")
def stats():
    return db.stats()


@app.post("/api/export")
def do_export():
    return export.run()
