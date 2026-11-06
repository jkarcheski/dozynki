"""How long the vetter takes to open an object (/api/object -> assemble.load + with_yse + evidence), piece by piece.

Offline (always run): every network fetcher is replaced by a sleep of known length, so the timings show the
pipeline's shape (what waits on what) rather than the network's mood, and the cached path is timed on a
realistically large payload.

Live (opt-in): each real service is timed on one ANTARES locus, so you can see which one is slow today.

Run with: .venv/bin/python -m pytest tests/test_vetting_speed.py -s
Live:     DOZYNKI_LIVE=1 .venv/bin/python -m pytest tests/test_vetting_speed.py -s -k live
          (DOZYNKI_LIVE_LOCUS=<locus id> to time a particular object instead of a random LSST one)
"""

import os
import threading
import time
from contextlib import contextmanager

import pytest

from dozynki import archival, assemble, crossmatch, db
from dozynki.brokers import alerce, antares, babamul_client, fink, yse

# seconds each stubbed service takes; distinct so the totals say which ones were waited on in turn
DELAY = {"locus": 0.30, "thumbs": 0.10, "alerce": 0.15, "fink": 0.15, "babamul": 0.20, "xmatch": 0.40,
         "archival_ztf": 0.25, "archival_ps1": 0.25, "yse": 0.20}
SLACK = 0.12  # thread start-up, sqlite, json; well under the smallest gap between the shapes tested


class Timer:
    """Wall time per component, and a table of them on exit."""

    def __init__(self, title):
        self.title, self.rows, self._lock = title, [], threading.Lock()

    @contextmanager
    def __call__(self, name):
        t = time.perf_counter()
        try:
            yield
        finally:
            with self._lock:
                self.rows.append((name, time.perf_counter() - t))

    def wrap(self, name, fn):
        def timed(*a, **kw):
            with self(name):
                return fn(*a, **kw)
        return timed

    def total(self, name):
        return sum(s for n, s in self.rows if n == name)

    def print(self):
        width = max(len(n) for n, _ in self.rows)
        print(f"\n{self.title}")
        for name, s in sorted(self.rows, key=lambda r: -r[1]):
            print(f"  {name:<{width}}  {s:7.3f} s")


def _points(n):
    return [{"survey": "lsst" if i % 3 else "ztf", "kind": "detection" if i % 4 else "forced", "mjd": 60000 + i * 0.1,
             "band": "griz"[i % 4], "alert_id": f"lsst:{i}", "ra": 150.0, "dec": 2.0, "diff_flux": 1000.0 + i,
             "diff_flux_err": 50.0, "mag": 21.0, "mag_err": 0.1, "reliability": 0.9, "magnr": 19.0, "distnr": 0.5}
            for i in range(n)]


def _antares_payload(locus_id, n_points=500):
    return {"locus_id": locus_id, "ra": 150.0, "dec": 2.0, "survey": {}, "ztf_object_id": "ZTF26aaaaaaa", "tags": [],
            "catalogs": [], "catalog_matches": [], "num_alerts": n_points, "points": _points(n_points), "lsst_details": {}}


@pytest.fixture
def stubbed(monkeypatch):
    """Every network call the vetter makes, replaced by a sleep of DELAY[...] seconds."""
    def after(key, value):
        def fn(*a, **kw):
            time.sleep(DELAY[key])
            return value(*a, **kw) if callable(value) else value
        return fn

    monkeypatch.setattr(antares, "get_locus", after("locus", lambda locus_id: _antares_payload(locus_id)))
    monkeypatch.setattr(antares, "thumbnails", after("thumbs", {"science": "x", "template": "x", "difference": "x"}))
    monkeypatch.setattr(alerce, "fetch", after("alerce", {"lsst": None, "ztf": None, "errors": {}}))
    monkeypatch.setattr(fink, "fetch", after("fink", {"lsst": None, "errors": {}}))
    monkeypatch.setattr(babamul_client, "fetch", after("babamul", {"enabled": False}))
    monkeypatch.setattr(crossmatch, "run", after("xmatch", {"errors": {}, "host_candidates": []}))
    monkeypatch.setitem(archival.SURVEYS, "ztf_dr", after("archival_ztf", []))
    monkeypatch.setitem(archival.SURVEYS, "ps1", after("archival_ps1", []))
    monkeypatch.setattr(archival, "_down", {})
    monkeypatch.setattr(yse, "login_for", lambda labeler: ("user", "pw"))
    monkeypatch.setattr(yse, "fetch", after("yse", {"enabled": True, "found": False}))
    monkeypatch.setattr(assemble, "_tns_names", lambda object_id, ant: [])


_n = iter(range(10**9))


def _queued():
    """A fresh queued object, so nothing about it is cached yet."""
    oid = f"9{next(_n):08d}"
    db.add_to_queue(oid, f"ANT_{oid}", 150.0, 2.0, "test", {})
    return oid


def _open(oid, labeler="tester"):
    """What /api/object does for the vetter (app.get_object), minus the HTTP."""
    raw = assemble.with_yse(oid, assemble.load(oid), labeler)
    return assemble.evidence(oid, raw), assemble.opinions(raw)


# ---------- offline: the pipeline's shape ----------

def test_cold_open_breakdown(stubbed, monkeypatch):
    """A first look at an object: time each component as the vetter's load runs it, and the whole."""
    t = Timer("cold open, stubbed services (critical path is what adds up to 'total')")
    for mod, name in [(antares, "get_locus"), (antares, "thumbnails"), (alerce, "fetch"), (babamul_client, "fetch"),
                      (crossmatch, "run"), (archival, "run"), (yse, "fetch"), (assemble, "evidence"), (assemble, "opinions")]:
        monkeypatch.setattr(mod, name, t.wrap(f"{mod.__name__.split('.')[-1]}.{name}", getattr(mod, name)))
    oid = _queued()
    with t("total"):
        _open(oid)
    t.print()

    # each component took what it was given; local work (evidence, opinions) is negligible
    assert t.total("antares.get_locus") == pytest.approx(DELAY["locus"], abs=SLACK)
    assert t.total("archival.run") == pytest.approx(max(DELAY["archival_ztf"], DELAY["archival_ps1"]), abs=SLACK)
    assert t.total("assemble.evidence") + t.total("assemble.opinions") < 0.05


def test_cold_open_waits_in_this_order(stubbed):
    """The current shape: ANTARES (locus, then one thumbnail call per survey with detections), then ALeRCE,
    Fink, Babamul, crossmatch and archival (its ZTF and PS1 at once) together, then YSE PZ.

    If this starts failing because the total dropped, the pipeline got more parallel: update the expected sum.
    """
    oid = _queued()
    t0 = time.perf_counter()
    _open(oid)
    took = time.perf_counter() - t0
    # the stub light curve has LSST and ZTF detections, so two thumbnail calls
    antares_part = DELAY["locus"] + 2 * DELAY["thumbs"]
    parallel_part = max(DELAY["alerce"], DELAY["fink"], DELAY["babamul"], DELAY["xmatch"], DELAY["archival_ztf"], DELAY["archival_ps1"])
    expected = antares_part + parallel_part + DELAY["yse"]
    print(f"\ncold open {took:.3f} s; expected {antares_part:.2f} ANTARES + {parallel_part:.2f} parallel + {DELAY['yse']:.2f} YSE PZ")
    assert took == pytest.approx(expected, abs=SLACK)


def test_the_parallel_group_overlaps(stubbed):
    """ALeRCE, Fink, Babamul, crossmatch and archival run side by side: the group costs its slowest member, not the sum."""
    oid = _queued()
    t0 = time.perf_counter()
    assemble.load(oid)
    took = time.perf_counter() - t0 - DELAY["locus"] - 2 * DELAY["thumbs"]
    serial = DELAY["alerce"] + DELAY["fink"] + DELAY["babamul"] + DELAY["xmatch"] + DELAY["archival_ztf"] + DELAY["archival_ps1"]
    assert took < serial - 0.3


def test_cached_open_is_fast(stubbed):
    """A second look at the same object costs no network at all; what's left is sqlite + json + evidence()."""
    oid = _queued()
    _open(oid)
    t0 = time.perf_counter()
    _open(oid)
    took = time.perf_counter() - t0
    print(f"\ncached open {took * 1000:.1f} ms")
    assert took < min(DELAY.values())


def test_cached_open_with_a_long_light_curve():
    """The cached path on a big payload (5000 points), timed step by step: cache read, evidence(), opinions()."""
    oid = _queued()
    db.cache_put(oid, "antares", _antares_payload(f"ANT_{oid}", 5000))
    for kind in ("alerce", "fink", "babamul", "xmatch"):
        db.cache_put(oid, kind, {"errors": {}})
    db.cache_put(oid, "archival", {"points": [], "errors": {}})
    t = Timer("cached open, 5000-point light curve")
    with t("assemble.load (sqlite + json)"):
        raw = assemble.load(oid)
    raw |= {"yse": {"data": {"enabled": False}, "fetched_at": None}}
    with t("assemble.evidence"):
        assemble.evidence(oid, raw)
    with t("assemble.opinions"):
        assemble.opinions(raw)
    t.print()
    assert sum(s for _, s in t.rows) < 0.5


def test_open_waits_for_a_prefetch_of_the_same_object(stubbed):
    """Opening an object that is being prefetched waits for that fetch rather than starting a second one,
    so it costs what is left of the prefetch, not a full fetch more."""
    oid = _queued()
    prefetch = threading.Thread(target=assemble.load, args=(oid,))
    prefetch.start()
    time.sleep(0.05)
    t0 = time.perf_counter()
    assemble.load(oid)
    took = time.perf_counter() - t0
    prefetch.join()
    full = DELAY["locus"] + 2 * DELAY["thumbs"] + DELAY["xmatch"]
    assert took == pytest.approx(full - 0.05, abs=SLACK)


def test_a_dead_catalog_host_costs_the_probe_not_the_query_timeout(monkeypatch):
    """CDS down (connects hang): the crossmatch gives up on its catalogs after PROBE_TIMEOUT, not QUERY_TIMEOUT,
    reports them as failed, keeps the others, and skips the dead ones for a while."""
    def connect(addr, timeout):
        if addr[0].endswith("cds.unistra.fr"):
            time.sleep(timeout)
            raise TimeoutError
        return type("Conn", (), {"close": lambda self: None})()

    monkeypatch.setattr(crossmatch.socket, "create_connection", connect)
    monkeypatch.setattr(crossmatch, "PROBE_TIMEOUT", 0.2)
    monkeypatch.setattr(crossmatch, "_down", {})
    hung = lambda *a: time.sleep(crossmatch.QUERY_TIMEOUT)  # what a query to a dead host does
    monkeypatch.setattr(crossmatch, "_jobs", lambda ra, dec, radius: {
        "gaia": (hung, ()), "simbad": (hung, ()), "ned": (lambda: [], ()), "legacy_survey": (lambda: [], ())})
    t0 = time.perf_counter()
    out = crossmatch.run(150.0, 2.0)
    took = time.perf_counter() - t0
    print(f"\ncrossmatch with CDS down {took:.3f} s")
    assert took < 0.2 + SLACK
    assert set(out["errors"]) == {"gaia", "simbad"} and "unreachable" in out["errors"]["gaia"]
    assert out["ned"] == [] and out["legacy_survey"] == []
    assert set(crossmatch._down) == {"gaia", "simbad"}


def test_a_cached_open_doesnt_retry_a_recent_archival_failure(stubbed, monkeypatch):
    """A survey that failed (IRSA answering everything with an error) is retried once the failure is older than
    archival.DOWN_SECONDS, not on every open of an already-cached object."""
    calls = []

    def broken(ra, dec):
        calls.append(1)
        raise RuntimeError("UsageFault")

    monkeypatch.setitem(archival.SURVEYS, "ztf_dr", broken)
    oid = _queued()
    _open(oid)
    assert len(calls) == 1
    t0 = time.perf_counter()
    _open(oid)
    _open(oid)
    assert len(calls) == 1
    assert time.perf_counter() - t0 < min(DELAY.values())

    db.execute("UPDATE cache SET fetched_at = '2000-01-01T00:00:00+00:00' WHERE object_id = ? AND kind = 'archival'", (oid,))
    archival._down.clear()  # DOWN_SECONDS have passed for the survey too
    _open(oid)
    assert len(calls) == 2


def test_archival_surveys_run_at_once_and_a_failed_one_is_skipped(monkeypatch):
    """ZTF DR and PS1 are searched side by side; one that fails in any way (IRSA's outages come back as 400s)
    is skipped on the next objects instead of being waited on again, and the other still answers."""
    calls = []

    def failing(ra, dec):
        calls.append("ztf_dr")
        time.sleep(0.2)
        raise RuntimeError("400 UsageFault")

    def slow(ra, dec):
        time.sleep(0.2)
        return [{"survey": "ps1", "mjd": 56000.0, "band": "r", "mag": 20.0, "mag_err": 0.1}]

    monkeypatch.setitem(archival.SURVEYS, "ztf_dr", failing)
    monkeypatch.setitem(archival.SURVEYS, "ps1", slow)
    monkeypatch.setattr(archival, "_down", {})
    t0 = time.perf_counter()
    out = archival.run(150.0, 2.0)
    assert time.perf_counter() - t0 == pytest.approx(0.2, abs=SLACK)
    assert len(out["points"]) == 1 and "UsageFault" in out["errors"]["ztf_dr"]

    out = archival.run(150.0, 2.0)
    assert calls == ["ztf_dr"]
    assert out["errors"]["ztf_dr"].startswith("skipped") and len(out["points"]) == 1


def test_quick_shows_the_light_curve_before_the_rest(stubbed):
    """The vetter's first request (/api/object?quick=true) costs ANTARES only and says what's still coming;
    once load() has run, nothing is pending and quick is as complete as the full response."""
    oid = _queued()
    t0 = time.perf_counter()
    raw, pending = assemble.quick(oid, "tester")
    took = time.perf_counter() - t0
    print(f"\nquick open {took:.3f} s")
    assert took == pytest.approx(DELAY["locus"] + 2 * DELAY["thumbs"], abs=SLACK)
    assert pending == ["ALeRCE", "Fink", "Babamul", "crossmatch", "archival light curves", "YSE PZ"]
    ev = assemble.evidence(oid, raw)
    assert len(ev["points"]) == 500 and ev["thumbs"] and ev["xmatch"] is None
    assemble.opinions(raw)  # blind mode off shows opinions straight away: they must build from the partial data

    _open(oid)
    t0 = time.perf_counter()
    raw, pending = assemble.quick(oid, "tester")
    assert pending == [] and raw["xmatch"] is not None
    assert time.perf_counter() - t0 < min(DELAY.values())


def test_sources_show_up_one_by_one_while_loading_in_the_background(stubbed):
    """What the vetter's polling sees: after quick(), load_in_background caches each source as it lands, so a
    fast one (ALeRCE) is there while a slow one (crossmatch) is still pending, and nothing is once it's done."""
    oid = _queued()
    assemble.quick(oid, "tester")
    assemble.load_in_background(oid, "tester")
    assert assemble.loading(oid)
    time.sleep(DELAY["alerce"] + 0.08)
    _, pending = assemble.quick(oid, "tester")
    assert "ALeRCE" not in pending and "crossmatch" in pending
    for _ in range(50):
        if not assemble.loading(oid):
            break
        time.sleep(0.05)
    _, pending = assemble.quick(oid, "tester")
    assert not assemble.loading(oid) and pending == []


# ---------- live: the real services, one at a time ----------

live = pytest.mark.skipif(not os.environ.get("DOZYNKI_LIVE"), reason="set DOZYNKI_LIVE=1 to time the real services")


def _live_locus():
    """DOZYNKI_LIVE_LOCUS, else a random LSST locus, else (ANTARES's LSST search down) a random ZTF one."""
    if os.environ.get("DOZYNKI_LIVE_LOCUS"):
        return os.environ["DOZYNKI_LIVE_LOCUS"]
    for sample in (antares.random_lsst_loci, antares.random_ztf_loci):
        try:
            return sample(1, int(time.time()))[1][0]["locus_id"]
        except Exception as e:
            print(f"\n{sample.__name__} failed: {type(e).__name__}: {e}"[:160])
    pytest.fail("couldn't pick a locus from ANTARES")


@live
def test_live_component_times():
    """Each service the vetter calls for one object, timed on its own; failures are reported, not raised,
    so one service being down doesn't hide the others' times."""
    t, errors = Timer("live, each service on its own"), {}

    def timed(name, fn, *a):
        try:
            with t(name):
                return fn(*a)
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {e}"[:120]

    locus_id = _live_locus()
    print(f"\nlocus {locus_id}")

    ant = timed("antares.get_locus (locus + all alerts)", antares.get_locus, locus_id)
    assert ant, errors
    ra, dec = ant["ra"], ant["dec"]
    dets = [p for p in ant["points"] if p["kind"] == "detection"]
    for survey in ("lsst", "ztf"):
        latest = max((p for p in dets if p["survey"] == survey), key=lambda p: p["mjd"], default=None)
        if latest:
            timed(f"antares.thumbnails ({survey})", antares.thumbnails, latest["alert_id"])

    dia = (ant["survey"].get("lsst") or {}).get("dia_object_id") or [None]
    timed("alerce.fetch", alerce.fetch, dia[0], ant.get("ztf_object_id"))
    timed("babamul.fetch" + ("" if babamul_client.BABAMUL_ENABLED else " (no token: skipped)"), babamul_client.fetch,
          dia[0] or ant.get("ztf_object_id"))
    for name, (fn, args) in crossmatch._jobs(ra, dec, crossmatch.XMATCH_RADIUS_ARCSEC).items():
        timed(f"crossmatch.{name}", fn, *args)
    for name, fn in archival.SURVEYS.items():
        timed(f"archival.{name}", fn, ra, dec)

    t.print()
    print("  (YSE PZ is not timed: it needs a labeler's stored login)")
    for name, e in errors.items():
        print(f"  FAILED {name}: {e}")


@live
def test_live_open_end_to_end():
    """The whole cold open against the real services, as the vetter does it (no YSE PZ login here)."""
    locus_id = _live_locus()
    s = antares.locus_summary(locus_id)
    oid = str(s["dia_object_id"] or s["ztf_object_id"])
    db.add_to_queue(oid, locus_id, s["ra"], s["dec"], "test", {})
    t = Timer(f"live cold open of {oid}")
    with t("cold open"):
        _open(oid)
    with t("cached open"):
        _open(oid)
    t.print()
