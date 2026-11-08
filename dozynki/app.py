import csv
import html
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, accounts, achievements, assemble, compare, coverage, db, details, evaluate, export, farm, lists, neighborhood, rejection, search, similar, sky, sources, tns
from . import daily, nightrun
from . import screen as screening  # `screen` is also the filtered-mode parameter name
from .brokers import antares, lasair, pittgoogle, yse
from .config import (ACCOUNTS, BABAMUL_ENABLED, BLIND_DEFAULT, LABELER, SECURE_COOKIES, XMATCH_MAX_RADIUS_ARCSEC,
                     XMATCH_RADIUS_ARCSEC)
from .filters import DATE_MODES, Filters
from .labels import CLASS_BY_KEY, CLASSES, CONFIDENCE, FLAGS, TRANSIENT_SUBCLASSES, WATCH_FLAG

STATIC = Path(__file__).parent / "static"
PREFETCH = 6

app = FastAPI(title="DOZYNKI alert vetter")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
daily.start_scheduler()
neighborhood.warm()
blind_mode = {}  # labeler -> blind on/off; a restart goes back to BLIND_DEFAULT


# (path, page file, tab label); the order is the order of the tabs.
PAGES = [
    ("/", "home.html", "Home"),
    ("/vet", "index.html", "Vetter"),
    ("/rejection", "rejection.html", "Filter lab"),
    ("/sky", "sky.html", "Sky map"),
    ("/similar", "similar.html", "Similar"),
    ("/compare", "compare.html", "Compare"),
    ("/watchlist", "watchlist.html", "Watchlist"),
    ("/daily", "daily.html", "Daily"),
]
OTHER_PAGES = [("/account", "account.html")]  # no tab


@app.middleware("http")
async def revalidate_static(request, call_next):
    """Without Cache-Control, browsers heuristically cache app.js/style.css and
    keep running old code after an edit; no-cache makes them check the ETag."""
    response = await call_next(request)
    if request.url.path in [path for path, _, _ in PAGES] or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def identify(request, call_next):
    """request.state.labeler: who labels with this request. Without accounts, LABELER; with them, the
    signed-in profile's handle, else this browser's guest id (a cookie, made on the first visit)."""
    request.state.user = None
    new_guest = None
    if request.url.path.startswith("/static/"):
        return await call_next(request)
    if not ACCOUNTS:
        request.state.labeler = LABELER
    elif user := accounts.session_user(request.cookies.get(accounts.SESSION_COOKIE)):
        request.state.user, request.state.labeler = user, user["handle"]
    else:
        guest = request.cookies.get(accounts.GUEST_COOKIE)
        if not accounts.valid_guest(guest):
            guest = new_guest = accounts.new_guest()
        request.state.labeler = guest
    response = await call_next(request)
    if new_guest:
        _set_cookie(response, accounts.GUEST_COOKIE, new_guest, 365)
    return response


@app.exception_handler(requests.RequestException)
def upstream_failed(request, e):
    """A broker or catalog that errors or times out is a 502 the page can show, not a crash."""
    host = urlparse(e.request.url).hostname if e.request is not None and e.request.url else "an upstream service"
    status = f" (HTTP {e.response.status_code})" if e.response is not None else ""
    return JSONResponse({"detail": f"{host} failed{status}; it may be down, try again later"}, status_code=502)


def _set_cookie(response, name, value, days):
    response.set_cookie(name, value, max_age=days * 86400, httponly=True, samesite="lax", secure=SECURE_COOKIES)


def _who(request):
    return request.state.labeler


def _blind(request):
    return blind_mode.get(_who(request), BLIND_DEFAULT)


def _display(request):
    """The name to show: a handle, "guest", or the install's labeler."""
    who = _who(request)
    return "guest" if who.startswith("guest:") else who


STARTED = time.time()


def _code_changed():
    """Python is loaded once at startup (static files aren't), so an edit to it needs a restart."""
    return any(p.stat().st_mtime > STARTED for p in Path(__file__).parent.rglob("*.py"))


def _nav(active, request):
    on = ' class="on" aria-current="page"'
    unread = daily.unread(_who(request))
    badge = {"/daily": f' <span class="badge" title="{unread} unread report{"s" * (unread != 1)}">{unread}</span>' if unread else ""}
    tabs = "".join(f'<a href="{path}"{on if path == active else ""}>{label}{badge.get(path, "")}</a>' for path, _, label in PAGES[1:])
    stale = ('<span class="stale" title="The server is still running the code it started with, so pages may '
             'call features it doesn\'t have yet. Stop it and run python -m dozynki again.">'
             'Code changed: restart the server</span>') if _code_changed() else ""
    who = ""
    if ACCOUNTS:
        name = html.escape(_display(request))
        who = (f'<a href="/account" class="who{" on" if active == "/account" else ""}" title="Your profile">{name}</a>'
               if request.state.user else
               f'<a href="/account" class="who{" on" if active == "/account" else ""}" '
               'title="You are vetting as a guest. Make a profile to be on the leaderboard and share filters.">guest · sign in</a>')
    # the profile link sits outside the tab bar, so CSS can pin it to the header's right-hand end
    # daily-alert.js pops up on every page when the latest daily report has jobs that failed
    # the search box (find.js) marks what it finds on the page, or opens it in the vetter; without JS, /vet?find=...
    find = ('<form class="find" action="/vet" method="get" role="search">'
            '<input type="search" name="find" placeholder="TNS name or LSST ID" aria-label="Find an object by TNS name or LSST ID" '
            'title="SN 2025abc, AT2025abc, a diaObjectId, lsst:&lt;diaSourceId&gt;, an ANTARES locus or RA Dec"></form>')
    return (f'<nav class="tabs"><a href="/" class="brand">DOZYNKI</a>{tabs}{stale}</nav>{find}{who}'
            '<script src="/static/daily-alert.js" defer></script><script src="/static/find.js" defer></script>')


def _page(path, request):
    """An HTML page whose /static/ links carry the file's mtime, so an edited
    app.js or style.css is a new URL that no browser has cached. <!--nav-->
    becomes the tab bar shared by every page."""
    name = next(name for p, name, *_ in PAGES + OTHER_PAGES if p == path)
    html = (STATIC / name).read_text().replace("<!--nav-->", _nav(path, request))
    html = re.sub(r'"/static/([\w.-]+)"', lambda m: f'"/static/{m[1]}?v={int((STATIC / m[1]).stat().st_mtime)}"', html)
    return HTMLResponse(html)


@app.get("/")
def home(request: Request, object: str | None = None):
    # Older links opened objects with /?object=<id>, from when the vetter was the root page.
    if object:
        return RedirectResponse(f"/vet?object={quote(object)}")
    return _page("/", request)


@app.get("/vet")
def vet_page(request: Request):
    return _page("/vet", request)


@app.get("/rejection")
def rejection_page(request: Request):
    return _page("/rejection", request)


@app.get("/compare")
def compare_page(request: Request):
    return _page("/compare", request)


@app.get("/sky")
def sky_page(request: Request):
    return _page("/sky", request)


@app.get("/similar")
def similar_page(request: Request):
    return _page("/similar", request)


@app.get("/watchlist")
def watchlist_page(request: Request):
    return _page("/watchlist", request)


@app.get("/daily")
def daily_page(request: Request):
    return _page("/daily", request)


@app.get("/account")
def account_page(request: Request):
    return _page("/account", request)


@app.get("/api/config")
def get_config(request: Request):
    return {
        "classes": CLASSES,
        "transient_subclasses": TRANSIENT_SUBCLASSES,
        "flags": FLAGS,
        "confidence": CONFIDENCE,
        "blind": _blind(request),
        "labeler": _display(request),
        "accounts": ACCOUNTS,
        "signed_in": bool(request.state.user),
        "babamul_enabled": BABAMUL_ENABLED,
        "sources": list(sources.SOURCES),
        "date_modes": list(DATE_MODES),
        "xmatch_radius": XMATCH_RADIUS_ARCSEC,
        "version": __version__,
    }


class BlindToggle(BaseModel):
    blind: bool


@app.post("/api/config/blind")
def set_blind(body: BlindToggle, request: Request):
    blind_mode[_who(request)] = body.blind
    return {"blind": body.blind}


def _accounts_on():
    if not ACCOUNTS:
        raise HTTPException(404, "profiles are off on this install (DOZYNKI_ACCOUNTS)")


def _signed_in(response, user):
    _set_cookie(response, accounts.SESSION_COOKIE, accounts.start_session(user["id"]), accounts.SESSION_DAYS)
    return {"ok": True, "handle": user["handle"]}


class SignUp(BaseModel):
    handle: str
    password: str
    email: str
    claim: bool = True  # move this browser's guest labels to the new profile


@app.post("/api/account/signup")
def signup(body: SignUp, request: Request, response: Response):
    _accounts_on()
    try:
        user = accounts.create(body.handle, body.password, body.email)
    except accounts.AccountError as e:
        raise HTTPException(400, str(e))
    if body.claim:
        accounts.claim_guest(_who(request), user["handle"])
    return _signed_in(response, user)


class SignIn(BaseModel):
    handle: str
    password: str


@app.post("/api/account/login")
def login(body: SignIn, request: Request, response: Response):
    _accounts_on()
    try:
        user = accounts.authenticate(body.handle, body.password, request.client.host if request.client else "")
    except accounts.AccountError as e:
        raise HTTPException(400, str(e))
    return _signed_in(response, user)


@app.post("/api/account/logout")
def logout(request: Request, response: Response):
    accounts.end_session(request.cookies.get(accounts.SESSION_COOKIE))
    response.delete_cookie(accounts.SESSION_COOKIE)
    return {"ok": True}


class ResetRequest(BaseModel):
    handle: str
    email: str


@app.post("/api/account/reset-request")
def reset_request(body: ResetRequest, request: Request):
    """Always the same answer, whether or not the handle and email match."""
    _accounts_on()
    try:
        accounts.request_reset(body.handle, body.email, str(request.base_url))
    except OSError as e:  # the mail server; worth telling, since it says nothing about the profile
        raise HTTPException(502, f"couldn't send the email: {e}")
    return {"ok": True}


class Reset(BaseModel):
    token: str
    password: str


@app.post("/api/account/reset")
def reset(body: Reset, response: Response):
    _accounts_on()
    try:
        user = accounts.finish_reset(body.token, body.password)
    except accounts.AccountError as e:
        raise HTTPException(400, str(e))
    return _signed_in(response, user)


def _yse_labeler(request):
    """Who a YSE PZ login is for: a signed-in profile, or the install's labeler without profiles. Not guests,
    whose labeler name is only a cookie."""
    if ACCOUNTS and not request.state.user:
        raise HTTPException(403, "sign in to add a YSE PZ login")
    return _who(request)


@app.get("/api/account/yse")
def yse_login(request: Request):
    can = not ACCOUNTS or bool(request.state.user)
    return {"can_add": can, "username": yse.username_for(_who(request)) if can else None}


class YseLogin(BaseModel):
    username: str
    password: str


@app.post("/api/account/yse")
def save_yse_login(body: YseLogin, request: Request):
    who = _yse_labeler(request)
    if accounts._limited(("yse", who), 10, 900):  # each try is a login attempt at YSE PZ
        raise HTTPException(429, "too many attempts; try again in 15 minutes")
    try:
        yse.save_login(who, body.username, body.password)
    except yse.LoginError as e:
        raise HTTPException(400, str(e))
    return {"username": yse.username_for(who)}


@app.post("/api/account/yse/check")
def check_yse_login(request: Request):
    """Try the saved login with YSE PZ now (e.g. after changing the password there)."""
    who = _yse_labeler(request)
    login = yse.login_for(who)
    if not login:
        raise HTTPException(404, "no YSE PZ login saved")
    if accounts._limited(("yse", who), 10, 900):
        raise HTTPException(429, "too many attempts; try again in 15 minutes")
    try:
        yse.check_login(login)
    except yse.LoginError as e:
        raise HTTPException(400, str(e))
    return {"username": login[0]}


@app.delete("/api/account/yse")
def forget_yse_login(request: Request):
    yse.forget_login(_yse_labeler(request))
    return {"username": None}


@app.get("/api/account/lasair")
def lasair_token(request: Request):
    can = not ACCOUNTS or bool(request.state.user)
    return {"can_add": can, "saved_at": lasair.saved_at(_who(request)) if can else None}


class LasairToken(BaseModel):
    token: str


def _lasair_labeler(request):
    if ACCOUNTS and not request.state.user:
        raise HTTPException(403, "sign in to add a Lasair token")
    who = _who(request)
    if accounts._limited(("lasair", who), 10, 900):  # each check is one of the token's 100 Lasair calls an hour
        raise HTTPException(429, "too many attempts; try again in 15 minutes")
    return who


@app.post("/api/account/lasair")
def save_lasair_token(body: LasairToken, request: Request):
    who = _lasair_labeler(request)
    try:
        lasair.save_token(who, body.token)
    except lasair.TokenError as e:
        raise HTTPException(400, str(e))
    return {"saved_at": lasair.saved_at(who)}


@app.post("/api/account/lasair/check")
def check_lasair_token(request: Request):
    """Try the saved token with Lasair now."""
    who = _lasair_labeler(request)
    token = lasair.token_for(who)
    if not token:
        raise HTTPException(404, "no Lasair token saved")
    try:
        lasair.check_token(token)
    except lasair.TokenError as e:
        raise HTTPException(400, str(e))
    return {"saved_at": lasair.saved_at(who)}


@app.delete("/api/account/lasair")
def forget_lasair_token(request: Request):
    if ACCOUNTS and not request.state.user:
        raise HTTPException(403, "sign in to remove a Lasair token")
    lasair.forget_token(_who(request))
    return {"saved_at": None}


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


def _after_pull(body, request):
    if body.screen:
        screening.start()
    else:
        assemble.prefetch([r["object_id"] for r in db.next_pending(_who(request), PREFETCH, where="screen IS NULL")], _who(request))


class Fill(Pull):
    source: str = "antares_random_lsst"


@app.post("/api/queue/fill")
def fill(body: Fill, request: Request):
    if body.source not in sources.SOURCES:
        raise HTTPException(400, f"unknown source {body.source}")
    try:
        result = sources.SOURCES[body.source](n=body.n, filters=body.build(), screen=body.resolve_screen())
    except pittgoogle.Unavailable as e:
        raise HTTPException(503, str(e))
    _after_pull(body, request)
    return result


class NightPull(Pull):
    night: str


@app.post("/api/queue/night")
def pull_night(body: NightPull, request: Request):
    try:
        result = sources.antares_night(body.night, body.n, body.build(), screen=body.resolve_screen())
    except ValueError as e:
        raise HTTPException(400, str(e))
    _after_pull(body, request)
    return result


@app.get("/api/nights")
def nights(request: Request):
    """Each sampled night (per detection minimum): objects taken so far, how many the
    current labeler has vetted, and the night's size at the latest batch; and the whole
    nights run through saved filters."""
    return {"nights": achievements.nights(_who(request)), "runs": achievements.night_runs(_who(request))}


class NightRun(BaseModel):
    night: str
    screen: str


@app.post("/api/nights/run")
def run_night(body: NightRun):
    """Filtered mode: run a whole night through a saved filter (nightrun.py)."""
    scr = Pull(screen=body.screen).resolve_screen()
    try:
        run_id = nightrun.start(body.night, scr)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"id": run_id}


@app.get("/api/nights/runs")
def night_runs(request: Request, screen: str = ""):
    runs = achievements.night_runs(_who(request), screen or None)
    if any(r["screening"] for r in runs):
        screening.start()  # resumes after a restart
    return {"runs": runs, "running": nightrun._running}


@app.get("/api/lists")
def object_lists(request: Request):
    """Every object list, whoever loaded it (anyone may vet or score one); mine: this request may add to or delete it."""
    who = _who(request)
    return [l | {"mine": l["owner"] in (None, who)} for l in db.object_lists()]


class ObjectList(BaseModel):
    name: str
    text: str  # one object per line: alert ID, diaObjectId, ANTARES locus, or RA Dec (lists.py)
    radius: float = lists.DEFAULT_RADIUS_ARCSEC
    note: str | None = None


def _may_edit_list(request, name):
    exists, owner = db.object_list_owner(name)
    if owner not in (None, _who(request)):
        raise HTTPException(403, f'"{name}" is {owner}\'s list; load yours under another name')
    return exists


@app.post("/api/lists")
def load_list(body: ObjectList, request: Request):
    """Resolve each line to an LSST object and add it to the named list (made if new) and the queue."""
    name = body.name.strip()
    if not name or len(name) > 80:
        raise HTTPException(400, "name must be 1-80 characters")
    if not 0 < body.radius <= lists.MAX_RADIUS_ARCSEC:
        raise HTTPException(400, f"radius must be in (0, {lists.MAX_RADIUS_ARCSEC:g}] arcsec")
    _may_edit_list(request, name)
    try:
        out = lists.load(name, body.text, body.radius, _who(request), (body.note or "").strip() or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    assemble.prefetch(out["object_ids"][:PREFETCH], _who(request))
    return out


@app.delete("/api/lists/{name}")
def delete_list(name: str, request: Request):
    """Forget the list; its objects stay queued, with their labels."""
    if not _may_edit_list(request, name) or not db.delete_object_list(name):
        raise HTTPException(404, "no list by that name")
    return {"ok": True}


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


IN_LIST = "object_id IN (SELECT object_id FROM list_members WHERE list = ?)"


def _queue_filter(min_det, date_from, date_to, date_mode, screen="", object_list=""):
    """(WHERE, params, which statuses are ready) over the filter bar and the mode: random (screen ""), one
    saved filter's screened pulls, or one object list's objects (every one, however it was queued)."""
    if object_list:
        return IN_LIST, [object_list], db.ANY_STATUS
    filters = FilterParams(min_det=min_det, date_from=date_from, date_to=date_to, date_mode=date_mode).build()
    if filters.active:
        _backfill_summaries()
    where, params = filters.sql()
    mode, mode_params = ("screen = ?", [screen]) if screen else ("screen IS NULL", [])
    return " AND ".join(w for w in (mode, where) if w), mode_params + params, db.READY


def _mode(screen, labeler, object_list=""):
    """Queue counts in this mode; in filtered mode, also how many wait for screening and were thrown away."""
    if object_list:
        return {"n_pending": db.count_pending(labeler, IN_LIST, [object_list], ready=db.ANY_STATUS),
                "n_list": len(db.list_object_ids(object_list))}
    counts = db.mode_counts(screen or None, labeler)
    out = {"n_pending": counts.get("pending", 0)}
    if screen:
        out |= {"n_screening": counts.get("screening", 0), "n_filtered": counts.get("filtered", 0)}
        if out["n_screening"]:
            screening.start()  # resumes after a restart
    return out


@app.get("/api/queue/matching")
def queue_matching(request: Request, min_det: int = 1, date_from: str = "", date_to: str = "", date_mode: str = "first", screen: str = "",
                   object_list: str = ""):
    where, params, ready = _queue_filter(min_det, date_from, date_to, date_mode, screen, object_list)
    return {"n_matching": db.count_pending(_who(request), where, params, ready), **_mode(screen, _who(request), object_list)}


@app.post("/api/queue/clear")
def clear_queue(request: Request, min_det: int = 1, date_from: str = "", date_to: str = "", date_mode: str = "first",
                screen: str = "", object_list: str = ""):
    """Empty your current haystack: what's waiting in this mode, and in filtered mode what's still being screened."""
    where, params, ready = _queue_filter(min_det, date_from, date_to, date_mode, screen, object_list)
    if ready == db.READY:
        ready = "status IN ('pending', 'screening')"
    return {"cleared": db.clear_pending(_who(request), where, params, ready)}


@app.get("/api/next")
def next_object(request: Request, exclude: str = "", min_det: int = 1, date_from: str = "", date_to: str = "", date_mode: str = "first",
                screen: str = "", object_list: str = "", sort: str = ""):
    """The next object to vet: the oldest queued, or in filtered mode with sort, the first by neighborhood.py's
    order (random mode always goes in order, so the labeled sample stays uniform for the filter lab)."""
    skip = tuple(x for x in exclude.split(",") if x)
    where, params, ready = _queue_filter(min_det, date_from, date_to, date_mode, screen, object_list)
    who = _who(request)
    if sort and sort not in neighborhood.SORTS:
        raise HTTPException(400, f"sort must be one of {neighborhood.SORTS}")
    if sort and screen and not object_list:
        rows = db.next_pending(who, -1, exclude=skip, where=where, params=params, ready=ready)
        first = neighborhood.order([r["object_id"] for r in rows], sort)[:PREFETCH + 1]
        rows = [{"object_id": oid} for oid in first]
    else:
        rows = db.next_pending(who, PREFETCH + 1, exclude=skip, where=where, params=params, ready=ready)
    out = {"n_matching": db.count_pending(who, where, params, ready), **_mode(screen, who, object_list)}
    if not rows:
        return {"object_id": None, **out}
    assemble.prefetch([r["object_id"] for r in rows[1:]], who)
    return {"object_id": rows[0]["object_id"], **out}


def _has_blind_label(object_id, labeler):
    return any(l["phase"] == "blind" for l in db.labels_for(object_id, labeler))


@app.get("/api/object/{object_id}")
def get_object(object_id: str, request: Request, refresh: bool = False, quick: bool = False, start: bool = False):
    """With quick, only what is ready at once (assemble.quick), and in "pending" what is still to come; with start
    too, the rest starts loading in the background ("loading" says whether it still is, so the vetter can poll)."""
    item = db.get_queue_item(object_id)
    if not item:
        raise HTTPException(404, "not in queue")
    loading = False
    if quick and not refresh:
        loading = assemble.loading(object_id)  # before quick(), so loading false with pending left means it failed
        raw, pending = assemble.quick(object_id, _who(request))
        if pending and start:
            assemble.load_in_background(object_id, _who(request))
            loading = True
    else:
        raw, pending = assemble.with_profile(object_id, assemble.load(object_id, refresh=refresh), _who(request), refresh), []
    ev = assemble.evidence(object_id, raw)
    labels = db.labels_for(object_id, _who(request))
    revealed = not _blind(request) or _has_blind_label(object_id, _who(request))
    return {
        "queue": item,
        "evidence": ev,
        "labels": labels,
        "opinions": _opinions(object_id, raw) if revealed else None,
        "revealed": revealed,
        "pending": pending,
        "loading": loading,
    }


@app.get("/api/reveal/{object_id}")
def reveal(object_id: str, request: Request):
    if _blind(request) and not _has_blind_label(object_id, _who(request)):
        raise HTTPException(403, "commit a blind label first")
    return _opinions(object_id, assemble.with_profile(object_id, assemble.load(object_id), _who(request)))


def _opinions(object_id, raw):
    """The brokers' opinions, and how the object's nearest labeled objects were labeled (neighborhood.py)."""
    return {**assemble.opinions(raw), "neighborhood": neighborhood.explain(object_id)}


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
def post_label(body: Label, request: Request):
    unknown = [k for k in [body.klass, *body.alt_classes] if k not in CLASS_BY_KEY]
    if unknown:
        raise HTTPException(400, f"unknown class {unknown}")
    alts = sorted(set(body.alt_classes) - {body.klass})
    if not db.get_queue_item(body.object_id):
        raise HTTPException(404, "not in queue")
    who = _who(request)
    previous = db.labels_for(body.object_id, who)
    if previous and WATCH_FLAG in json.loads(previous[-1]["flags"] or "[]"):
        phase = "followup"  # second look at a watched object, possibly with new data
    elif _has_blind_label(body.object_id, who):
        phase = "revealed"
    else:
        phase = "blind" if _blind(request) else "unblinded"
    before = achievements.summary(who)
    raw = assemble.load(body.object_id)
    snap = assemble.snapshot(assemble.evidence(body.object_id, raw)) | {"blind_mode": _blind(request)}
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
            "labeler": who,
            "created_at": db.now(),
            "snapshot": json.dumps(snap),
            "app_version": __version__,
        }
    )
    db.set_status(body.object_id, who, "watch" if WATCH_FLAG in body.flags else "labeled")
    # A transient label on a known SN / TNS object (or a TDE or nova) is a needle in the haystack.
    # Any other label on an object ANTARES matched to TNS caches the match too, for the compare page's TNS source.
    found = None
    first_transient = body.klass == "transient" and not any(l["klass"] == "transient" for l in previous)  # once per object
    if first_transient or (tns.listed(raw["antares"]["data"]) and db.cache_get(body.object_id, "tns") is None):
        try:
            matches = achievements.tns_for(body.object_id, raw["antares"]["data"]["locus_id"])
        except Exception:
            matches = []  # the next "Check TNS" picks it up
        if first_transient:
            found = achievements.needle((raw["xmatch"] or {}).get("data") or {}, matches)
    return {"id": label_id, "phase": phase, "needle": found,
            "achievements": achievements.newly_earned(before, achievements.summary(who))}


@app.post("/api/watch/recheck")
def watch_recheck(request: Request):
    """Re-poll ANTARES for your watched objects; any with new alerts go back to your queue."""
    return daily.recheck_watchlist(_who(request))


WATCHLIST_COLUMNS = ["object_id", "antares_locus", "ra", "dec", "n_det", "last_mag", "last_band", "last_mjd", "klass",
                     "subclass", "confidence", "flags", "notes", "phase", "flagged_at"]


def _watchlist(request, since):
    """Your watched objects, each with its detection count and latest detected magnitude from the cached light curve."""
    return _with_last_detection(db.watchlist(_who(request), _since(since)))


def _with_last_detection(rows):
    for r in rows:
        cached = db.cache_get(r["object_id"], "antares")
        dets = [p for p in (cached["data"]["points"] if cached else []) if p["kind"] == "detection" and p.get("mag") is not None]
        last = max(dets, key=lambda p: p["mjd"]) if dets else {}
        r.update(n_det=len(dets) if cached else None, last_mag=last.get("mag"), last_band=last.get("band"), last_mjd=last.get("mjd"))
    return rows


def _since(since):
    if since and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        raise HTTPException(400, "since must be YYYY-MM-DD")
    return since or None


@app.get("/api/watchlist")
def watchlist(request: Request, since: str = ""):
    """Your watched objects, with the label that put each on the list."""
    return {"objects": _watchlist(request, since)}


@app.get("/api/watchlist.csv")
def watchlist_csv(request: Request, since: str = ""):
    """The same list as a CSV download; built per request, nothing is written to disk."""
    rows = _watchlist(request, since)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, WATCHLIST_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    name = f"watchlist{'_since_' + since if since else ''}.csv"
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


TRANSIENT_COLUMNS = ["object_id", "antares_locus", "ra", "dec", "n_det", "last_mag", "last_band", "last_mjd", "klass",
                     "alt_classes", "subclass", "confidence", "flags", "notes", "phase", "labeled_at"]


@app.get("/api/transients.csv")
def transients_csv(request: Request, also: bool = False):
    """Your transient candidates by your latest label on each object: transient as the primary label, or with
    also=true, another primary label with transient as a "could also be". Built per request, like the watchlist's."""
    rows = _with_last_detection(db.transients(_who(request), also))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, TRANSIENT_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    name = "transients_also.csv" if also else "transients.csv"
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _daily_labeler(request):
    who = _who(request)
    if who.startswith("guest:"):
        raise HTTPException(403, "make a profile to get daily reports")
    return who


@app.get("/api/daily")
def daily_status(request: Request):
    """Your daily jobs' settings and recent reports, and what the page needs to edit them."""
    who = _daily_labeler(request)
    return {"labeler": who, "settings": daily.settings(who), "reports": daily.reports(who), "emails": daily.emails(who),
            "slack": daily.posts(who),
            "saved_filters": [p["name"] for p in db.saved_pipelines()], "last_night": daily.last_night(),
            "sn_list": daily.sn_list_name(who), "sn_classifier": daily.SN_CLASSIFIER}


@app.post("/api/daily/settings")
def daily_settings(body: dict, request: Request):
    try:
        return daily.save_settings(_daily_labeler(request), body)
    except ValueError as e:
        raise HTTPException(400, str(e))


class DailyRun(BaseModel):
    night: str | None = None


@app.post("/api/daily/run")
def daily_run(body: DailyRun, request: Request):
    """Run your daily jobs now, in the background (for last night unless a night is given)."""
    try:
        return {"id": daily.run_in_background(_daily_labeler(request), body.night or None)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/api/daily/alert")
def daily_alert(request: Request):
    """The pop-up on every page: your latest report's failed jobs, or that they're being rerun. Nothing for guests."""
    who = _who(request)
    return {"alert": None if who.startswith("guest:") else daily.alert(who)}


@app.post("/api/daily/rerun/{report_id}")
def daily_rerun(report_id: int, request: Request):
    """Run a report's failed jobs again, now, into the same report."""
    try:
        daily.rerun(_daily_labeler(request), report_id)
    except KeyError:
        raise HTTPException(404, "no such report")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"id": report_id}


@app.post("/api/daily/slack/{report_id}")
def daily_slack(report_id: int, request: Request):
    """Post one of your reports to your Slack channel now."""
    try:
        result = daily.post_report(_daily_labeler(request), report_id)
    except KeyError:
        raise HTTPException(404, "no such finished report")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result != "sent":
        raise HTTPException(502, result)
    return {"slacked": result}


@app.post("/api/daily/dismiss/{report_id}")
def daily_dismiss(report_id: int, request: Request):
    daily.dismiss(_daily_labeler(request), report_id)
    return {"ok": True}


@app.post("/api/daily/read")
def daily_read(request: Request):
    daily.mark_read(_daily_labeler(request))
    return {"ok": True}


@app.post("/api/xmatch/fill-missing")
def xmatch_fill_missing():
    """Retry catalogs that failed (e.g. timed out) in cached crossmatches. Labeled objects
    are left alone, so their crossmatch stays the evidence they were labeled with."""
    rows = db.query(
        "SELECT c.object_id FROM cache c JOIN queue q USING (object_id) WHERE c.kind = 'xmatch'"
        " AND q.object_id NOT IN (SELECT object_id FROM labels) AND json_extract(c.payload, '$.errors') != '{}'"
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
def skip(object_id: str, request: Request):
    db.set_status(object_id, _who(request), "skipped")
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
def stats(request: Request):
    return db.stats(_who(request))


@app.get("/api/leaderboard")
def leaderboard(days: int = 7):
    return {"days": days, "labelers": db.leaderboard(days)}


@app.get("/api/coverage")
def get_coverage(request: Request, survey: str = "lsst"):
    """Per night: LSST (or ZTF) objects first alerting then (counted in the background) and how many are vetted."""
    if survey not in coverage.SURVEYS:
        raise HTTPException(400, f"survey must be one of {', '.join(coverage.SURVEYS)}")
    return coverage.summary(_who(request), survey)


@app.get("/api/achievements")
def get_achievements(request: Request, labeler: str | None = None):
    who = labeler or _who(request)
    return {"labeler": who, "categories": achievements.CATEGORIES, "achievements": achievements.summary(who),
            "tns_checked_at": achievements.tns_checked_at()}


@app.post("/api/tns/check")
def tns_check():
    """Re-check TNS (through ANTARES) for every object labeled transient or matched to TNS."""
    return achievements.check_tns()


class TnsList(BaseModel):
    classified_only: bool = True


@app.post("/api/tns/list")
def tns_list_build(body: TnsList):
    """Build, or add to, the object list of LSST objects ANTARES matched to TNS, in the background (tns.py)."""
    tns.start(body.classified_only)
    return tns.status()


@app.get("/api/tns/list")
def tns_list_status():
    return tns.status()


@app.get("/api/search")
def search_object(q: str):
    """The object a TNS name, LSST ID, locus or position names, queued if new (search.py)."""
    try:
        return search.find(q)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except search.Unavailable as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # a broker or TNS that errors or times out
        raise HTTPException(502, f"lookup failed ({type(e).__name__})")


@app.get("/api/farm")
def get_farm(request: Request):
    """The achievement unlocks for the current labeler."""
    return farm.state(_who(request))


@app.get("/api/rejection/config")
def rejection_config():
    return {
        "rules": [{"name": r.name, "description": r.description, "defaults": r.defaults, "broker": r.broker,
                   "labels": r.labels}
                  for r in rejection.RULES.values()],
        "default_pipeline": rejection.resolve(rejection.DEFAULT_PIPELINE),
        "classes": CLASSES,
        "transient_subclasses": TRANSIENT_SUBCLASSES,
    }


def _vetted(request, vetted):
    """The vetted-by-me population, as evaluate.population_ids takes it, from "FROM..TO" (UTC dates, either may be
    empty), or None when vetted is empty."""
    if not vetted:
        return None
    lo, sep, hi = vetted.partition("..")
    try:
        for d in (lo, hi):
            if d:
                date.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, "vetted must be FROM..TO, dates as YYYY-MM-DD")
    return _who(request), lo, (hi if sep else lo)  # one date alone is that day


class Evaluation(BaseModel):
    pipeline: list[dict]
    truth: str = "blind"
    target: list[str] = ["transient"]
    include_alt: bool = False
    all_sources: bool = False
    object_list: str | None = None  # score one object list's objects instead
    vetted: str | None = None  # or the objects I first vetted on these dates, "FROM..TO"
    survey: str = "lsst"  # lsst | ztf | both


def _evaluate(body, request):
    try:
        report, df = evaluate.evaluate(body.pipeline, body.truth, body.target, body.include_alt, body.all_sources, save=False,
                                       object_list=body.object_list, vetted=_vetted(request, body.vetted), survey=body.survey)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    return report, df


@app.post("/api/rejection/evaluate")
def rejection_evaluate(body: Evaluation, request: Request):
    """Metrics for a pipeline, plus each scored object's outcome. Nothing is stored."""
    report, df = _evaluate(body, request)
    # step:* columns are each step's own decision (1 reject, 0 keep, null no data), for the decision tree
    objects = df[["object_id", "truth", "subclass", "alt_classes", "target", "kept", "rejected_by",
                  *[c for c in df.columns if c.startswith("step:")]]]
    return {**report, "objects": json.loads(objects.to_json(orient="records"))}


class Sweep(Evaluation):
    step: int
    param: str
    values: list[float]


@app.post("/api/rejection/sweep")
def rejection_sweep(body: Sweep, request: Request):
    if not 0 <= body.step < len(body.pipeline) or body.param == "rule":
        raise HTTPException(400, "bad step or param")
    try:
        return evaluate.sweep(body.pipeline, body.step, body.param, body.values[:60], truth=body.truth,
                              target=body.target, include_alt=body.include_alt, all_sources=body.all_sources,
                              object_list=body.object_list, vetted=_vetted(request, body.vetted), survey=body.survey)
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
def saved_pipelines(request: Request):
    """Every saved filter, whoever saved it (anyone may use one); mine: this request may overwrite or delete it."""
    who = _who(request)
    return [p | {"mine": p["owner"] in (None, who)} for p in db.saved_pipelines()]


def _may_edit_filter(request, name):
    """Saving needs a profile when profiles are on, and only a filter's owner may change it."""
    if ACCOUNTS and not request.state.user:
        raise HTTPException(403, "sign in to save filters: they're kept under your profile")
    exists, owner = db.saved_pipeline_owner(name)
    if owner not in (None, _who(request)):
        raise HTTPException(403, f'"{name}" is {owner}\'s filter; save yours under another name')
    return exists


@app.post("/api/rejection/saved")
def save_pipeline(body: SavedPipeline, request: Request):
    """Store a named pipeline (overwrites one of the same name). Only enabled steps, in order."""
    name = body.name.strip()
    if not name or len(name) > 80:
        raise HTTPException(400, "name must be 1-80 characters")
    try:
        rejection.resolve(body.pipeline)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, f"bad pipeline: {e}")
    _may_edit_filter(request, name)
    db.put_saved_pipeline(name, body.pipeline, (body.note or "").strip() or None, _who(request))
    return {"ok": True, "name": name}


@app.delete("/api/rejection/saved/{name}")
def delete_saved_pipeline(name: str, request: Request):
    if not _may_edit_filter(request, name) or not db.delete_saved_pipeline(name):
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
    object_list: str | None = None
    vetted: str | None = None
    survey: str = "lsst"


@app.post("/api/compare")
def compare_sources(body: Comparison, request: Request):
    """Confusion matrix of two label sources. Nothing is stored."""
    try:
        return compare.compare(body.ref, body.comp, body.view, body.min_prob, body.include_alt, body.all_sources,
                               body.object_list, _vetted(request, body.vetted), body.survey)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/compare/summary")
def compare_summary(body: Comparison, request: Request):
    try:
        return compare.summary(body.ref, view=body.view, min_prob=body.min_prob, include_alt=body.include_alt,
                               all_sources=body.all_sources, object_list=body.object_list, vetted=_vetted(request, body.vetted),
                               survey=body.survey)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/sky")
def sky_objects(request: Request, truth: str = "blind", all_sources: bool = False, object_list: str = "", vetted: str = "",
                survey: str = "lsst"):
    """Every queued object's position in three frames, with its truth label (None if unlabeled)."""
    if truth not in ("blind", "final"):
        raise HTTPException(400, "truth must be blind or final")
    if survey not in evaluate.SURVEYS:
        raise HTTPException(400, f"survey must be one of {evaluate.SURVEYS}")
    return {"objects": sky.objects(truth, all_sources, object_list or None, _vetted(request, vetted), survey), "classes": CLASSES, "transient_subclasses": TRANSIENT_SUBCLASSES}


def _groups(groups):
    return [g for g in groups.split(",") if g] if groups else similar.DEFAULT_GROUPS


def _off(off):
    return [f for f in off.split(",") if f]


@app.get("/api/similar")
def similar_map(request: Request, truth: str = "blind", all_sources: bool = False, groups: str = "", object_list: str = "",
                vetted: str = "", survey: str = "lsst", off: str = ""):
    """Every object's position on the first two principal components of its features, with its truth label."""
    try:
        out = similar.projection(truth, all_sources, _groups(groups), object_list or None, _vetted(request, vetted), survey, _off(off))
    except ValueError as e:
        raise HTTPException(400, str(e))
    features = [{"name": name, "group": group, "label": label} for name, group, _, label in similar.FEATURES]
    return {**out, "groups": similar.GROUPS, "default_groups": similar.DEFAULT_GROUPS, "features": features, "classes": CLASSES}


@app.get("/api/similar/{object_id}")
def similar_neighbors(object_id: str, request: Request, k: int = 8, truth: str = "blind", all_sources: bool = False,
                      groups: str = "", pool: str = "labeled", object_list: str = "", vetted: str = "",
                      survey: str = "lsst", off: str = ""):
    """The k objects closest to one in feature space. Nothing is stored."""
    if pool not in ("labeled", "all"):
        raise HTTPException(400, "pool must be labeled or all")
    try:
        return similar.neighbors(object_id, max(1, min(k, 24)), truth, all_sources, _groups(groups), pool, object_list or None,
                                 _vetted(request, vetted), survey, _off(off))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "not in the population (not fetched, or not in the random sample)")


@app.post("/api/export")
def do_export():
    return export.run()
