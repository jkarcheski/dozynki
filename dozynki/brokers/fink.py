"""Fink classifier scores for an LSST object, one row per alert (diaSource).

Fink's per-alert classifiers on LSST (see arXiv:2404.08798): CATS (broad class + its probability),
SuperNNova SN-vs-others, the early SN Ia score, and ELEPHANT's hostless indicators. Fink's crossmatch
columns are left out: the crossmatch is our own (crossmatch.py). No login; ZTF objects aren't fetched.
"""

import requests

from ..config import FINK_LSST_API, HTTP_TIMEOUT

CATS_CLASSES = {11: "SN-like", 12: "Fast", 13: "Long", 21: "Periodic", 22: "Non-periodic"}

# Fink column -> our name
COLUMNS = {
    "f:clf_cats_class": "cats_class",
    "f:clf_cats_score": "cats_score",
    "f:clf_snnSnVsOthers_score": "snn_sn_vs_others",
    "f:clf_earlySNIa_score": "early_snia",
    "f:clf_elephant_kstest_science": "hostless_science",
    "f:clf_elephant_kstest_template": "hostless_template",
}

_session = requests.Session()


def _alert(r):
    a = {"mjd": r.get("r:midpointMjdTai")} | {ours: r.get(theirs) for theirs, ours in COLUMNS.items()}
    # -1 is Fink's "not processed"
    a["cats_class"] = CATS_CLASSES.get(a["cats_class"])
    if a["cats_class"] is None:
        a["cats_score"] = None
    if a["early_snia"] is not None and a["early_snia"] < 0:
        a["early_snia"] = None
    return a


def fetch(dia_object_id):
    """{"lsst": {"alerts": [...] oldest first, "latest": the newest alert} | None, "errors": {}}.
    dia_object_id is None for objects sampled from ZTF."""
    result = {"lsst": None, "errors": {}}
    if not dia_object_id:
        return result
    try:
        r = _session.post(f"{FINK_LSST_API}/sources", timeout=HTTP_TIMEOUT, json={
            "diaObjectId": str(dia_object_id), "columns": ",".join(["r:midpointMjdTai", *COLUMNS])})
        r.raise_for_status()
        alerts = sorted((_alert(row) for row in r.json()), key=lambda a: a["mjd"] or 0)
    except Exception as e:
        result["errors"]["lsst"] = str(e)
        return result
    if not alerts:
        result["errors"]["lsst"] = "not in Fink"
        return result
    result["lsst"] = {"alerts": alerts, "latest": alerts[-1]}
    return result
