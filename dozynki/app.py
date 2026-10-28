import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, achievements, assemble, compare, db, details, evaluate, export, farm, rejection, sky, sources
from . import screen as screening  # `screen` is also the filtered-mode parameter name
from .brokers import antares
from .config import BABAMUL_ENABLED, BLIND_DEFAULT, LABELER, XMATCH_MAX_RADIUS_ARCSEC, XMATCH_RADIUS_ARCSEC
from .filters import DATE_MODES, Filters
from .labels import CLASS_BY_KEY, CLASSES, CONFIDENCE, FLAGS, TRANSIENT_SUBCLASSES, WATCH_FLAG

STATIC = Path(__file__).parent / "static"
PREFETCH = 6

app = FastAPI(title="DOZYNKI alert vetter")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
state = {"blind": BLIND_DEFAULT}


# (path, page file, tab label); the order is the order of the tabs.
PAGES = [
    ("/", "home.html", "Home"),
    ("/vet", "index.html", "Vetter"),
    ("/rejection", "rejection.html", "Filter lab"),
    ("/sky", "sky.html", "Sky map"),
    ("/compare", "compare.html", "Compare"),
]


@app.middleware("http")
async def revalidate_static(request, call_next):
    """Without Cache-Control, browsers heuristically cache app.js/style.css and
    keep running old code after an edit; no-cache makes them check the ETag."""
    response = await call_next(request)
    if request.url.path in [path for path, _, _ in PAGES] or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


STARTED = time.time()


def _code_changed():
    """Python is loaded once at startup (static files aren't), so an edit to it needs a restart."""
    return any(p.stat().st_mtime > STARTED for p in Path(__file__).parent.rglob("*.py"))


def _nav(active):
    on = ' class="on" aria-current="page"'
    tabs = "".join(f'<a href="{path}"{on if path == active else ""}>{label}</a>' for path, _, label in PAGES[1:])
    stale = ('<span class="stale" title="The server is still running the code it started with, so pages may '
             'call features it doesn\'t have yet. Stop it and run python -m dozynki again.">'
             'Code changed: restart the server</span>') if _code_changed() else ""
    return f'<nav class="tabs"><a href="/" class="brand">DOZYNKI</a>{tabs}{stale}</nav>'


def _page(path):
    """An HTML page whose /static/ links carry the file's mtime, so an edited
    app.js or style.css is a new URL that no browser has cached. <!--nav-->
    becomes the tab bar shared by every page."""
    name = next(name for p, name, _ in PAGES if p == path)
    html = (STATIC / name).read_text().replace("<!--nav-->", _nav(path))
    html = re.sub(r'"/static/([\w.-]+)"', lambda m: f'"/static/{m[1]}?v={int((STATIC / m[1]).stat().st_mtime)}"', html)
    return HTMLResponse(html)


@app.get("/")
def home(object: str | None = None):
    # Older links opened objects with /?object=<id>, from when the vetter was the root page.
    if object:
        return RedirectResponse(f"/vet?object={quote(object)}")
    return _page("/")


@app.get("/vet")
def vet_page():
    return _page("/vet")


@app.get("/rejection")
def rejection_page():
    return _page("/rejection")


@app.get("/compare")
def compare_page():
    return _page("/compare")


@app.get("/sky")
def sky_page():
    return _page("/sky")


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
        "date_modes": list(DATE_MODES),
        "xmatch_radius": XMATCH_RADIUS_ARCSEC,
        "version": __version__,
    }


class BlindToggle(BaseModel):
    blind: bool


@app.post("/api/config/blind")
def set_blind(body: BlindToggle):
    state["blind"] = body.blind
    return {"blind": state["blind"]}


class FilterParams(BaseModel):
    min_det: int = 1
    date_from: str | None = None
    date_to: str | None = None
    date_mode: str = "first"

    def build(self):
        try:
            return Filters(self.min_det, self.date_from, self.date_to, self.date_mode)
        except ValueError as e:
            raise HTTPException(400, str(e))


class Pull(FilterParams):
    n: int = 50
    screen: str | None = None  # filtered mode: a saved filter's name

    def resolve_screen(self):
        if not self.screen:
            return None
        try:
            return screening.resolve(self.screen)
        except KeyError:
            raise HTTPException(404, f"no saved filter {self.screen!r}")


def _after_pull(body):
    if body.screen:
        screening.start()
    else:
        assemble.prefetch([r["object_id"] for r in db.next_pending(PREFETCH, where="screen IS NULL")])


class Fill(Pull):
    source: str = "antares_random_lsst"


@app.post("/api/queue/fill")
def fill(body: Fill):
    if body.source not in sources.SOURCES:
        raise HTTPException(400, f"unknown source {body.source}")
    result = sources.SOURCES[body.source](n=body.n, filters=body.build(), screen=body.resolve_screen())
    _after_pull(body)
    return result


class NightPull(Pull):
    night: str


@app.post("/api/queue/night")
def pull_night(body: NightPull):
    try:
        result = sources.antares_night(body.night, body.n, body.build(), screen=body.resolve_screen())
    except ValueError as e:
        raise HTTPException(400, str(e))
    _after_pull(body)
    return result


@app.get("/api/nights")
def nights():
    """Each sampled night (per detection minimum): objects taken so far, how many the
    current labeler has vetted, and the night's size at the latest batch."""
    return {"nights": achievements.nights(LABELER)}


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
        added += db.add_to_queue(loc["dia_object_id"], loc["locus_id"], loc["ra"], loc["dec"], "manual_list", {}, summary=loc)
    return {"added": added, "not_in_antares": missing}


def _backfill_summaries():
    """Fetch the ANTARES locus summary for pending objects queued before filters existed."""
    missing = db.pending_missing_summary()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for row, summary in zip(missing, pool.map(lambda r: _safe_summary(r["antares_locus"]), missing)):
            if summary:
                db.set_summary(row["object_id"], summary)


def _safe_summary(locus_id):
    try:
        return antares.locus_summary(locus_id)
    except Exception:
        return None


def _queue_filter(min_det, date_from, date_to, date_mode, screen=""):
    """WHERE over the filter bar and the mode: random (screen "") or one saved filter's screened pulls."""
    filters = FilterParams(min_det=min_det, date_from=date_from, date_to=date_to, date_mode=date_mode).build()
    if filters.active:
        _backfill_summaries()
    where, params = filters.sql()
    mode, mode_params = ("screen = ?", [screen]) if screen else ("screen IS NULL", [])
    return " AND ".join(w for w in (mode, where) if w), mode_params + params


def _mode(screen):
    """Queue counts in this mode; in filtered mode, also how many wait for screening and were thrown away."""
    counts = db.mode_counts(screen or None)
    out = {"n_pending": counts.get("pending", 0)}
    if screen:
        out |= {"n_screening": counts.get("screening", 0), "n_filtered": counts.get("filtered", 0)}
        if out["n_screening"]:
            screening.start()  # resumes after a restart
    return out


@app.get("/api/queue/matching")
def queue_matching(min_det: int = 1, date_from: str = "", date_to: str = "", date_mode: str = "first", screen: str = ""):
    where, params = _queue_filter(min_det, date_from, date_to, date_mode, screen)
    return {"n_matching": db.count_pending(where, params), **_mode(screen)}


@app.get("/api/next")
def next_object(exclude: str = "", min_det: int = 1, date_from: str = "", date_to: str = "", date_mode: str = "first",
                screen: str = ""):
    skip = tuple(x for x in exclude.split(",") if x)
    where, params = _queue_filter(min_det, date_from, date_to, date_mode, screen)
    rows = db.next_pending(PREFETCH + 1, exclude=skip, where=where, params=params)
    out = {"n_matching": db.count_pending(where, params), **_mode(screen)}
    if not rows:
        return {"object_id": None, **out}
    assemble.prefetch([r["object_id"] for r in rows[1:]])
    return {"object_id": rows[0]["object_id"], **out}


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
    alt_classes: list[str] = []  # "could also be" classes, besides the primary klass
    subclass: str | None = None
    confidence: str | None = None
    flags: list[str] = []
    host: dict | None = None
    notes: str | None = None


@app.post("/api/label")
def post_label(body: Label):
    unknown = [k for k in [body.klass, *body.alt_classes] if k not in CLASS_BY_KEY]
    if unknown:
        raise HTTPException(400, f"unknown class {unknown}")
    alts = sorted(set(body.alt_classes) - {body.klass})
    if not db.get_queue_item(body.object_id):
        raise HTTPException(404, "not in queue")
    previous = db.labels_for(body.object_id)
    if previous and WATCH_FLAG in json.loads(previous[-1]["flags"] or "[]"):
        phase = "followup"  # second look at a watched object, possibly with new data
    elif _has_blind_label(body.object_id):
        phase = "revealed"
    else:
        phase = "blind" if state["blind"] else "unblinded"
    before = achievements.summary(LABELER)
    raw = assemble.load(body.object_id)
    snap = assemble.snapshot(assemble.evidence(body.object_id, raw)) | {"blind_mode": state["blind"]}
    label_id = db.add_label(
        {
            "object_id": body.object_id,
            "phase": phase,
            "klass": body.klass,
            "alt_classes": json.dumps(alts),
            "subclass": body.subclass if "transient" in (body.klass, *alts) else None,
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
    # A transient label on a known SN / TNS object (or a TDE or nova) is a needle in the haystack.
    found = None
    if body.klass == "transient":
        try:
            tns = achievements.tns_for(body.object_id, raw["antares"]["data"]["locus_id"])
        except Exception:
            tns = []  # the next "Check TNS" picks it up
        found = achievements.needle((raw["xmatch"] or {}).get("data") or {}, tns)
    return {"id": label_id, "phase": phase, "needle": found,
            "achievements": achievements.newly_earned(before, achievements.summary(LABELER))}


@app.post("/api/watch/recheck")
def watch_recheck():
    """Re-poll ANTARES for watched objects; any with new alerts go back to the queue."""
    updated, unchanged, errors = [], 0, {}
    for item in db.query("SELECT * FROM queue WHERE status = 'watch'"):
        oid = item["object_id"]
        try:
            old = db.cache_get(oid, "antares")
            n_old = (old["data"].get("num_alerts") or len(old["data"]["points"])) if old else 0
            summary = antares.locus_summary(item["antares_locus"])
            db.set_summary(oid, summary)
            n_new = summary["num_alerts"] or 0
            if n_new > n_old:
                assemble.refresh_brokers(oid)
                db.set_status(oid, "pending")
                updated.append({"object_id": oid, "new_alerts": n_new - n_old})
            else:
                unchanged += 1
        except Exception as e:
            errors[oid] = f"{type(e).__name__}: {e}"
    return {"requeued": updated, "unchanged": unchanged, "errors": errors}


@app.post("/api/xmatch/fill-missing")
def xmatch_fill_missing():
    """Retry catalogs that failed (e.g. timed out) in cached crossmatches. Labeled objects
    are left alone, so their crossmatch stays the evidence they were labeled with."""
    rows = db.query(
        "SELECT c.object_id FROM cache c JOIN queue q USING (object_id) WHERE c.kind = 'xmatch'"
        " AND q.status != 'labeled' AND json_extract(c.payload, '$.errors') != '{}'"
    )

    def fill(oid):
        try:
            return assemble.fill_missing_xmatch(oid), None
        except Exception as e:
            return [], f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(zip([r["object_id"] for r in rows], pool.map(fill, [r["object_id"] for r in rows])))
    filled = {oid: cats for oid, (cats, _) in results.items() if cats}
    errors = {oid: err for oid, (_, err) in results.items() if err}
    return {"checked": len(rows), "filled": filled, "errors": errors}


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


@app.get("/api/leaderboard")
def leaderboard(days: int = 7):
    return {"days": days, "labelers": db.leaderboard(days)}


@app.get("/api/achievements")
def get_achievements(labeler: str | None = None):
    who = labeler or LABELER
    return {"labeler": who, "categories": achievements.CATEGORIES, "achievements": achievements.summary(who),
            "tns_checked_at": achievements.tns_checked_at()}


@app.post("/api/tns/check")
def tns_check():
    """Re-check TNS (through ANTARES) for every object labeled transient."""
    return achievements.check_tns()


@app.get("/api/farm")
def get_farm():
    """Coins earned, spent and left, the shop and the achievement unlocks, for the current labeler."""
    return farm.state(LABELER)


class Purchase(BaseModel):
    item: str


@app.post("/api/farm/buy")
def buy(body: Purchase):
    try:
        return farm.buy(LABELER, body.item)
    except ValueError as e:
        raise HTTPException(400, str(e))


class Wear(BaseModel):
    slot: str
    item: str | None = None


@app.post("/api/farm/wear")
def wear(body: Wear):
    try:
        return farm.wear(LABELER, body.slot, body.item)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/rejection/config")
def rejection_config():
    return {
        "rules": [{"name": r.name, "description": r.description, "defaults": r.defaults, "broker": r.broker}
                  for r in rejection.RULES.values()],
        "default_pipeline": rejection.resolve(rejection.DEFAULT_PIPELINE),
        "classes": CLASSES,
        "transient_subclasses": TRANSIENT_SUBCLASSES,
    }


class Evaluation(BaseModel):
    pipeline: list[dict]
    truth: str = "blind"
    target: list[str] = ["transient"]
    include_alt: bool = False
    all_sources: bool = False


def _evaluate(body):
    try:
        report, df = evaluate.evaluate(body.pipeline, body.truth, body.target, body.include_alt, body.all_sources, save=False)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    return report, df


@app.post("/api/rejection/evaluate")
def rejection_evaluate(body: Evaluation):
    """Metrics for a pipeline, plus each scored object's outcome. Nothing is stored."""
    report, df = _evaluate(body)
    # step:* columns are each step's own decision (1 reject, 0 keep, null no data), for the decision tree
    objects = df[["object_id", "truth", "subclass", "alt_classes", "target", "kept", "rejected_by",
                  *[c for c in df.columns if c.startswith("step:")]]]
    return {**report, "objects": json.loads(objects.to_json(orient="records"))}


class Sweep(Evaluation):
    step: int
    param: str
    values: list[float]


@app.post("/api/rejection/sweep")
def rejection_sweep(body: Sweep):
    if not 0 <= body.step < len(body.pipeline) or body.param == "rule":
        raise HTTPException(400, "bad step or param")
    try:
        return evaluate.sweep(body.pipeline, body.step, body.param, body.values[:60], truth=body.truth,
                              target=body.target, include_alt=body.include_alt, all_sources=body.all_sources)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/rejection/details")
def details_status():
    """How many cached objects lack Rubin detection details, and the backfill's progress."""
    return details.status()


@app.post("/api/rejection/details")
def details_backfill():
    details.backfill()
    return details.status()


class SavedPipeline(BaseModel):
    name: str
    pipeline: list[dict]
    note: str | None = None


@app.get("/api/rejection/saved")
def saved_pipelines():
    return db.saved_pipelines()


@app.post("/api/rejection/saved")
def save_pipeline(body: SavedPipeline):
    """Store a named pipeline (overwrites one of the same name). Only enabled steps, in order."""
    name = body.name.strip()
    if not name or len(name) > 80:
        raise HTTPException(400, "name must be 1-80 characters")
    try:
        rejection.resolve(body.pipeline)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, f"bad pipeline: {e}")
    db.put_saved_pipeline(name, body.pipeline, (body.note or "").strip() or None)
    return {"ok": True, "name": name}


@app.delete("/api/rejection/saved/{name}")
def delete_saved_pipeline(name: str):
    if not db.delete_saved_pipeline(name):
        raise HTTPException(404, "no saved pipeline by that name")
    return {"ok": True}


@app.get("/api/compare/config")
def compare_config():
    return {"sources": compare.sources(), "views": list(compare.VIEWS), "alerce_classes": compare.ALERCE_CLASSES,
            "classes": CLASSES}


class Comparison(BaseModel):
    ref: str
    comp: str
    view: str = "class"
    min_prob: float = 0.0
    include_alt: bool = False
    all_sources: bool = False


@app.post("/api/compare")
def compare_sources(body: Comparison):
    """Confusion matrix of two label sources. Nothing is stored."""
    try:
        return compare.compare(body.ref, body.comp, body.view, body.min_prob, body.include_alt, body.all_sources)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/compare/summary")
def compare_summary(body: Comparison):
    try:
        return compare.summary(body.ref, view=body.view, min_prob=body.min_prob, include_alt=body.include_alt,
                               all_sources=body.all_sources)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/sky")
def sky_objects(truth: str = "blind", all_sources: bool = False):
    """Every queued object's position in three frames, with its truth label (None if unlabeled)."""
    if truth not in ("blind", "final"):
        raise HTTPException(400, "truth must be blind or final")
    return {"objects": sky.objects(truth, all_sources), "classes": CLASSES, "transient_subclasses": TRANSIENT_SUBCLASSES}


@app.post("/api/export")
def do_export():
    return export.run()
