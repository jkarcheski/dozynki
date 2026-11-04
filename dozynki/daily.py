"""Daily jobs: each morning, the night just gone through your saved filters, a recheck of your watchlist,
and ALeRCE's new high-probability supernovae, summed up in one report ("12 new objects to vet").

The report is kept per labeler (daily_reports) for the Daily page and the tab's unread count, emailed to
the labeler's address in DOZYNKI_REPORT_EMAIL and posted to their Slack webhook in DOZYNKI_SLACK_WEBHOOK, if set. A scheduler thread in the server starts each
labeler's jobs once their time of day (server's local time) has passed and the night has no report yet;
`python -m dozynki.daily` runs them now, from a terminal or a cron job.

"Last night" is the latest Rubin night that has ended: dayObs D once it is past 11:00 UTC on D+1
(dawn at Cerro Pachón is before then all year), though its noon-to-noon window runs an hour longer.
"""

import argparse
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

from . import achievements, assemble, db, lists, mail, nightrun, screen, slack, sources
from .brokers import alerce, antares
from .config import BASE_URL, LABELER, REPORT_EMAILS, SLACK_WEBHOOKS

DEFAULTS = {
    "enabled": False,
    "time": "08:00",          # local time of day on the server
    "filters": [],            # saved filter names to run last night through
    "watchlist": True,
    "alerce_sn": True,
    "sn_min_prob": 0.7,       # ALeRCE's top class SN at least this probable
    "sn_min_det": 2,          # one detection is mostly what the stamp classifier saw on one image
    "sn_days": 3,             # first detected within this many nights, so a second detection a night later still counts
    "sn_max": 200,            # most probable new ones added to the list per day
    "email": True,
    "slack": True,            # post to the labeler's Slack webhook, if it has one
}
SN_CLASSIFIER = "stamp_classifier_rubin_beta_20260421"  # ALeRCE's newest LSST classifier with an SN class
SCREEN_WAIT = 3 * 3600  # seconds to wait for a filter's background check before reporting what's left
POLL = 30
LINK_BASE = BASE_URL or "http://127.0.0.1:8765"

_scheduler = None


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# a report whose process is gone never finishes (the server or the command was stopped during it)
for _r in db.query("SELECT id, pid FROM daily_reports WHERE state = 'running'"):
    if not _r["pid"] or not _alive(_r["pid"]):
        db.execute("UPDATE daily_reports SET state = 'error', error = 'interrupted: stopped during the run', "
                   "finished_at = ? WHERE id = ?", (db.now(), _r["id"]))


def _why(e):
    """A failure in a line: a broker's as its host and HTTP status (its URL holds the whole query), else the exception."""
    if isinstance(e, requests.RequestException) and e.request is not None and e.request.url:
        status = f" (HTTP {e.response.status_code})" if e.response is not None else f" ({type(e).__name__})"
        return f"{urlparse(e.request.url).hostname} failed{status}"
    return f"{type(e).__name__}: {e}"


def settings(labeler):
    rows = db.query("SELECT config FROM daily_settings WHERE labeler = ?", (labeler,))
    return DEFAULTS | (json.loads(rows[0]["config"]) if rows else {})


def save_settings(labeler, config):
    config = {k: config[k] for k in DEFAULTS if k in config}
    datetime.strptime(config.get("time", DEFAULTS["time"]), "%H:%M")  # ValueError on a bad time
    db.execute("INSERT OR REPLACE INTO daily_settings (labeler, config, updated_at) VALUES (?, ?, ?)",
               (labeler, json.dumps(config), db.now()))
    return settings(labeler)


def emails(labeler):
    """Whether this labeler's reports can be emailed: their address is only in .env (DOZYNKI_REPORT_EMAIL)."""
    return labeler.lower() in REPORT_EMAILS


def posts(labeler):
    """Whether this labeler's reports can go to Slack: their webhook is only in .env (DOZYNKI_SLACK_WEBHOOK)."""
    return labeler.lower() in SLACK_WEBHOOKS


def send_slack(labeler, title, report):
    """Post a report to the labeler's Slack channel; returns "sent" or why it wasn't, for daily_reports.slacked."""
    try:
        slack.post(SLACK_WEBHOOKS[labeler.lower()], slack_text(title, report))
        return "sent"
    except Exception as e:
        return f"not sent: {_why(e)}"


def last_night(now=None):
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(hours=35)).date().isoformat()


def sn_list_name(labeler):
    return f"ALeRCE SN · daily · {labeler}"


# ---------- jobs ----------

def night_size(night):
    """LSST objects alerting during the night (nightrun's definition), counted past ANTARES's 10,000 cap."""
    return sum(n for *_, n in nightrun._slices(nightrun.night_filters(night)))


def _run_for(night, scr):
    """A finished run of the night through this version of the filter, else None."""
    return next((r for r in reversed(db.night_runs(scr["name"]))
                 if r["night"] == night and r["version"] == scr["version"] and r["state"] == "done"), None)


def filter_job(labeler, night, name):
    """Run the night through a saved filter (unless that's done already), wait for the background check, and count."""
    out = {"name": name}
    try:
        scr = screen.resolve(name)
    except KeyError:
        return out | {"error": "no saved filter with this name any more"}
    run = _run_for(night, scr)
    while run is None:
        while nightrun._running is not None:
            time.sleep(POLL)
        try:
            run_id = nightrun.start(night, scr)
        except RuntimeError:
            continue  # another run started first
        while nightrun._running == run_id:
            time.sleep(POLL)
        run = next(r for r in db.night_runs(name) if r["id"] == run_id)
        if run["state"] == "error":
            return out | {"run": run_id, "error": re.sub(r" for url: \S+", "", run["error"] or "")}
    screen.start()  # resumes after a restart
    deadline = time.time() + SCREEN_WAIT
    while True:
        counts = next(r for r in achievements.night_runs(labeler, name) if r["id"] == run["id"])
        if not counts["screening"] or time.time() > deadline:
            break
        time.sleep(POLL)
    return out | {"run": run["id"]} | {k: counts[k] for k in ("n_night", "n_passed", "filtered", "screening", "left", "vetted")}


def recheck_watchlist(labeler):
    """Re-poll ANTARES for the labeler's watched objects; any with new alerts go back to their queue."""
    updated, unchanged, errors = [], 0, {}
    for item in db.watched(labeler):
        oid = item["object_id"]
        try:
            old = db.cache_get(oid, "antares")
            n_old = (old["data"].get("num_alerts") or len(old["data"]["points"])) if old else 0
            if item["antares_locus"]:
                summary = antares.locus_summary(item["antares_locus"])
                db.set_summary(oid, summary)
                n_new = summary["num_alerts"] or 0
            else:  # a ZTF object whose light curve came from ALeRCE: refetch it to count
                n_new = assemble.refresh_brokers(oid, labeler)["num_alerts"] or 0
            if n_new > n_old:
                if item["antares_locus"]:
                    assemble.refresh_brokers(oid, labeler)
                db.set_status(oid, labeler, None)
                updated.append({"object_id": oid, "new_alerts": n_new - n_old})
            else:
                unchanged += 1
        except Exception as e:
            errors[oid] = _why(e)
    return {"requeued": updated, "unchanged": unchanged, "errors": errors}


def alerce_sn_job(labeler, night, cfg):
    """ALeRCE's LSST objects first detected in the last sn_days nights with SN on top, added to the labeler's list."""
    lo, hi = sources.night_mjd_range(night)
    rows = alerce.lsst_top_class_since(SN_CLASSIFIER, "SN", cfg["sn_min_prob"], lo + 1 - cfg["sn_days"])
    rows = [r for r in rows if r["firstmjd"] < hi and r["n_det"] >= cfg["sn_min_det"]]
    name = sn_list_name(labeler)
    on_list = db.list_object_ids(name)
    new = sorted((r for r in rows if str(r["oid"]) not in on_list), key=lambda r: -r["probability"])
    out = {"list": name, "classifier": SN_CLASSIFIER, "matched": len(rows), "new": len(new),
           **{k: cfg[k] for k in ("sn_min_prob", "sn_min_det", "sn_days", "sn_max")}}
    take = new[:cfg["sn_max"]]
    if not take:
        return out | {"added": [], "missing": 0}
    note = (f"LSST objects whose top class from ALeRCE's {SN_CLASSIFIER} is SN (p ≥ {cfg['sn_min_prob']}), with "
            f"{cfg['sn_min_det']}+ detections, added each morning by the daily jobs: broker-selected, not a random sample")
    result = lists.load(name, "\n".join(str(r["oid"]) for r in take), owner=labeler, note=note)
    prob = {str(r["oid"]): r["probability"] for r in take}
    return out | {"added": [{"object_id": o, "p": round(prob.get(o, 0), 3)} for o in result["object_ids"]],
                  "missing": len(result["missing"])}


def _to_vet(labeler, object_ids):
    """How many of these objects are ready to vet and not yet labeled, skipped or watched by the labeler."""
    if not object_ids:
        return 0
    marks = ",".join("?" * len(object_ids))
    return db.query(f"SELECT COUNT(*) n FROM queue q WHERE q.object_id IN ({marks}) AND q.status = 'pending' "
                    "AND NOT EXISTS (SELECT 1 FROM queue_status s WHERE s.object_id = q.object_id AND s.labeler = ?)",
                    (*object_ids, labeler))[0]["n"]


def headline(report):
    n, night = report["n_to_vet"], report["night"]
    bad = sum(1 for part, _ in problems(report) if part != "night")  # the night's count isn't a job
    vet = f"{n} new object{'s' * (n != 1)} to vet"
    if report.get("n_night") == 0:
        title = f"LSST produced no new alerts last night ({night})" + (f"; {vet}" if n else "")
    else:
        title = vet if n else f"Nothing new to vet after the night of {night}"
    return title + (f" ({bad} job{'s' * (bad != 1)} failed)" if bad else "")


def problems(report, state="done", error=None):
    """What went wrong in a report, as [(part, line)]; part is what rerun() redoes ("night", "filter:<name>",
    "watchlist", "alerce_sn"). A run that stopped in the middle names the jobs it never got to as "all"."""
    report = report or {}
    out = []
    if state == "error":
        out.append(("all", f"The run stopped: {error}"))
    if report.get("night_error"):
        out.append(("night", f"Counting the night in ANTARES: {report['night_error']}"))
    out += [(f"filter:{f['name']}", f"Filter \"{f['name']}\": {f['error']}") for f in report.get("filters", []) if f.get("error")]
    for key, what in (("watchlist", "Watchlist recheck"), ("alerce_sn", "ALeRCE SN")):
        if (report.get(key) or {}).get("error"):
            out.append((key, f"{what}: {report[key]['error']}"))
    return out


# ---------- report ----------

def _jobs(labeler, night, cfg, report, redo=None):
    """Run the jobs into report. redo (a set of problems() parts) runs only those and keeps the rest; "all" in it
    runs everything configured that the report has no result for, as after a run that stopped."""
    everything = redo is None or "all" in redo
    want = lambda part, done: redo is None or part in redo or ("all" in redo and not done)
    if want("night", "n_night" in report):
        report.pop("night_error", None)
        try:
            report["n_night"] = night_size(night)
        except Exception as e:
            report["n_night"], report["night_error"] = None, _why(e)
    old = {f["name"]: f for f in report.get("filters", [])}
    names = list(old) + [n for n in cfg["filters"] if n not in old] if everything else list(old)
    report["filters"] = []
    for name in names:
        if not want(f"filter:{name}", name in old):
            report["filters"].append(old[name])
            continue
        try:
            report["filters"].append(filter_job(labeler, night, name))
        except Exception as e:
            report["filters"].append({"name": name, "error": _why(e)})
    if cfg["watchlist"] and want("watchlist", "watchlist" in report) or "watchlist" in (redo or ()):
        try:
            report["watchlist"] = recheck_watchlist(labeler)
        except Exception as e:
            report["watchlist"] = {"error": _why(e)}
    if cfg["alerce_sn"] and want("alerce_sn", "alerce_sn" in report) or "alerce_sn" in (redo or ()):
        try:
            report["alerce_sn"] = alerce_sn_job(labeler, night, cfg)
        except Exception as e:
            report["alerce_sn"] = {"error": _why(e)}
    sn = report.get("alerce_sn") or {}
    if "added" in sn:
        sn["to_vet"] = _to_vet(labeler, [a["object_id"] for a in sn["added"]])
    report["n_to_vet"] = (sum(f.get("left", 0) for f in report["filters"])
                          + len((report.get("watchlist") or {}).get("requeued", []))
                          + sn.get("to_vet", 0))
    return headline(report)


def run(labeler, night=None, email=True, report_id=None):
    """Run the labeler's jobs for a night (default: last night) and store the report; returns its id."""
    cfg = settings(labeler)
    night = night or last_night()
    sources.night_mjd_range(night)  # ValueError on a bad date
    if report_id is None:
        report_id = start_report(labeler, night)
    report = {"night": night}
    try:
        title = _jobs(labeler, night, cfg, report)
        emailed = None
        if email and cfg["email"] and emails(labeler):
            try:
                if not mail.configured():
                    raise RuntimeError("no DOZYNKI_SMTP_HOST")
                mail.send(REPORT_EMAILS[labeler.lower()], f"DOZYNKI: {title}", email_body(title, report))
                emailed = "sent"
            except Exception as e:
                emailed = f"not sent: {_why(e)}"
        slacked = send_slack(labeler, title, report) if email and cfg["slack"] and posts(labeler) else None
        db.execute("UPDATE daily_reports SET state = 'done', headline = ?, report = ?, emailed = ?, slacked = ?, "
                   "finished_at = ? WHERE id = ?", (title, json.dumps(report), emailed, slacked, db.now(), report_id))
    except Exception as e:
        db.execute("UPDATE daily_reports SET state = 'error', report = ?, error = ?, finished_at = ? WHERE id = ?",
                   (json.dumps(report), _why(e), db.now(), report_id))
    return report_id


def _rerun(labeler, row, redo):
    report = json.loads(row["report"]) if row["report"] else {"night": row["night"]}
    try:
        title = _jobs(labeler, row["night"], settings(labeler), report, redo)
        db.execute("UPDATE daily_reports SET state = 'done', headline = ?, report = ?, error = NULL, finished_at = ?, "
                   "read_at = NULL WHERE id = ?", (title, json.dumps(report), db.now(), row["id"]))
    except Exception as e:
        db.execute("UPDATE daily_reports SET state = 'error', report = ?, error = ?, finished_at = ? WHERE id = ?",
                   (json.dumps(report), _why(e), db.now(), row["id"]))


def rerun(labeler, report_id):
    """Run a report's failed jobs again, in the background, into the same report (not emailed again)."""
    rows = db.query("SELECT * FROM daily_reports WHERE id = ? AND labeler = ?", (report_id, labeler))
    if not rows:
        raise KeyError(report_id)
    row = rows[0]
    redo = {part for part, _ in problems(json.loads(row["report"]) if row["report"] else None, row["state"], row["error"])}
    if not redo:
        raise ValueError("every job in this report ran")
    if any(_alive(r["pid"]) for r in db.query("SELECT pid FROM daily_reports WHERE labeler = ? AND state = 'running'",
                                               (labeler,))):
        raise RuntimeError("the daily jobs are already running")
    db.execute("UPDATE daily_reports SET state = 'running', pid = ?, dismissed_at = NULL WHERE id = ?", (os.getpid(), report_id))
    threading.Thread(target=_rerun, args=(labeler, row, redo), daemon=True).start()


def alert(labeler):
    """The latest report, if it ran with problems the labeler hasn't dismissed, or is being rerun."""
    rows = db.query("SELECT * FROM daily_reports WHERE labeler = ? ORDER BY id DESC LIMIT 1", (labeler,))
    if not rows:
        return None
    r = rows[0]
    if r["state"] == "running":
        return {"id": r["id"], "night": r["night"], "running": True}
    found = problems(json.loads(r["report"]) if r["report"] else None, r["state"], r["error"])
    if not found or r["dismissed_at"]:
        return None
    return {"id": r["id"], "night": r["night"], "running": False, "headline": r["headline"],
            "problems": [line for _, line in found]}


def dismiss(labeler, report_id):
    db.execute("UPDATE daily_reports SET dismissed_at = ? WHERE id = ? AND labeler = ?", (db.now(), report_id, labeler))


def start_report(labeler, night):
    """The report's row, before its jobs run; RuntimeError if one is already running for this labeler."""
    if any(_alive(r["pid"]) for r in db.query("SELECT pid FROM daily_reports WHERE labeler = ? AND state = 'running'",
                                               (labeler,))):
        raise RuntimeError("the daily jobs are already running")
    return db.execute("INSERT INTO daily_reports (labeler, night, state, pid, started_at) VALUES (?, ?, 'running', ?, ?)",
                      (labeler, night, os.getpid(), db.now())).lastrowid


def run_in_background(labeler, night=None):
    night = night or last_night()
    sources.night_mjd_range(night)
    report_id = start_report(labeler, night)
    threading.Thread(target=run, args=(labeler, night), kwargs={"report_id": report_id}, daemon=True).start()
    return report_id


def email_body(title, report):
    night, lines = report["night"], [title, ""]
    if report.get("n_night") is not None:
        lines.append(f"Night of {night} (Rubin dayObs): {report['n_night']:,} LSST objects alerted.")
    elif report.get("night_error"):
        lines.append(f"Night of {night}: couldn't count it in ANTARES ({report['night_error']}).")
    for f in report["filters"]:
        if f.get("error"):
            lines.append(f"\nFilter \"{f['name']}\": failed ({f['error']}).")
            continue
        lines.append(f"\nFilter \"{f['name']}\": {f['n_passed']:,} passed the listing rules; {f['left']} kept and left to vet"
                     + (f", {f['screening']} still being checked" if f["screening"] else "") + ".")
        if f["left"]:
            lines.append(f"  Vet them in Filtered mode with \"{f['name']}\": {LINK_BASE}/vet")
    w = report.get("watchlist")
    if w:
        if w.get("error"):
            lines.append(f"\nWatchlist: recheck failed ({w['error']}).")
        else:
            lines.append(f"\nWatchlist: {len(w['requeued'])} of {len(w['requeued']) + w['unchanged']} watched objects have new alerts"
                         + (f" ({len(w['errors'])} couldn't be checked)" if w["errors"] else "") + ".")
            lines += [f"  {LINK_BASE}/vet?object={r['object_id']}  (+{r['new_alerts']} alerts)" for r in w["requeued"]]
    sn = report.get("alerce_sn")
    if sn:
        if sn.get("error"):
            lines.append(f"\nALeRCE SN: failed ({sn['error']}).")
        else:
            lines.append(f"\nALeRCE SN: {sn['matched']} LSST objects first seen in the last {sn['sn_days']} nights have SN on top "
                         f"(p ≥ {sn['sn_min_prob']}, {sn['sn_min_det']}+ detections); {sn['new']} new, "
                         f"{len(sn['added'])} added to the list \"{sn['list']}\" ({sn.get('to_vet', 0)} to vet).")
            lines += [f"  {LINK_BASE}/vet?object={a['object_id']}  p={a['p']}" for a in sn["added"][:20]]
            if len(sn["added"]) > 20:
                lines.append(f"  ... and {len(sn['added']) - 20} more in List mode")
    lines.append(f"\nAll reports: {LINK_BASE}/daily")
    return "\n".join(lines)


def slack_text(title, report):
    """The email's text with a bold title; Slack makes the links clickable itself."""
    return f"*DOZYNKI: {title}*\n" + email_body(title, report).split("\n", 1)[1]


def post_report(labeler, report_id):
    """Post a stored report to the labeler's Slack channel now (the Daily page's "Send to Slack")."""
    rows = db.query("SELECT headline, report FROM daily_reports WHERE id = ? AND labeler = ? AND state = 'done'",
                    (report_id, labeler))
    if not rows:
        raise KeyError(report_id)
    if not posts(labeler):
        raise ValueError(f"no Slack webhook for {labeler} in DOZYNKI_SLACK_WEBHOOK")
    result = send_slack(labeler, rows[0]["headline"], json.loads(rows[0]["report"]))
    db.execute("UPDATE daily_reports SET slacked = ? WHERE id = ?", (result, report_id))
    return result


def reports(labeler, limit=30):
    rows = db.query("SELECT * FROM daily_reports WHERE labeler = ? ORDER BY id DESC LIMIT ?", (labeler, limit))
    for r in rows:
        r["report"] = json.loads(r["report"]) if r["report"] else None
        r.pop("pid")
    return rows


def unread(labeler):
    return db.query("SELECT COUNT(*) n FROM daily_reports WHERE labeler = ? AND state != 'running' AND read_at IS NULL",
                    (labeler,))[0]["n"]


def mark_read(labeler):
    db.execute("UPDATE daily_reports SET read_at = ? WHERE labeler = ? AND state != 'running' AND read_at IS NULL",
               (db.now(), labeler))


# ---------- schedule ----------

def due(now=None):
    """Labelers whose jobs should start now: enabled, past their time today, and last night not reported yet
    (a report that failed is tried again after an hour)."""
    now = now or datetime.now()
    night = last_night()
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    out = []
    for r in db.query("SELECT labeler FROM daily_settings"):
        cfg = settings(r["labeler"])
        if not cfg["enabled"] or now.strftime("%H:%M") < cfg["time"]:
            continue
        if not db.query("SELECT 1 FROM daily_reports WHERE labeler = ? AND night = ? AND "
                        "(state != 'error' OR started_at > ?)", (r["labeler"], night, hour_ago)):
            out.append(r["labeler"])
    return out


def _schedule():
    while True:
        for labeler in due():
            try:
                night = last_night()
                run(labeler, night, report_id=start_report(labeler, night))
            except Exception as e:  # already running from the command line, say; the next minute tries again
                print(f"[daily] {labeler}: {type(e).__name__}: {e}")
        time.sleep(60)


def start_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = threading.Thread(target=_schedule, daemon=True)
        _scheduler.start()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the daily jobs now and store (and email) the report.")
    parser.add_argument("--labeler", default=LABELER)
    parser.add_argument("--night", help="Rubin dayObs, YYYY-MM-DD (default: last night)")
    parser.add_argument("--no-email", action="store_true", help="neither email nor post to Slack")
    args = parser.parse_args(argv)
    report_id = run(args.labeler, args.night, email=not args.no_email)
    r = reports(args.labeler, 1)[0]
    print(r["headline"] or r["error"])
    if r["report"] and r["state"] == "done":
        print(email_body(r["headline"], r["report"]))
    if r["emailed"]:
        print(f"email: {r['emailed']}")
    if r["slacked"]:
        print(f"Slack: {r['slacked']}")
    return report_id


if __name__ == "__main__":
    main()
