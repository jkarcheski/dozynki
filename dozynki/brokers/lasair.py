"""Lasair: Sherlock's contextual classification of an LSST object (SN, NT, AGN, VS, CV, BS, ORPHAN, UNCLEAR),
from what it lies on or near in host and star catalogs rather than from its light curve.

Lasair's API needs a token, so each labeler enters their own on the profile page. What Sherlock says doesn't
depend on whose token asked, so the result is cached once per object (kind "lasair", assemble.py) and shown
to everyone; labelers without a token see what's cached and fetch nothing. The token is stored encrypted
with the YSE PZ key (yse.py), so the same caveat holds: whoever can read the data directory can decrypt it.

An ordinary Lasair account gets 100 calls an hour. A throttled or failed fetch is cached with its error and
retried later (assemble.with_lasair), not on every open.
"""

import re

import requests

from .. import db
from ..config import HTTP_TIMEOUT, LASAIR_API
from .yse import _fernet  # one key file encrypts both

_session = requests.Session()


class TokenError(ValueError):
    pass


def _post(path, token, data):
    r = _session.post(f"{LASAIR_API}/{path}/", data=data, headers={"Authorization": f"Token {token}"},
                      timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


# ---------- each labeler's token ----------

def token_for(labeler):
    rows = db.query("SELECT token FROM lasair_tokens WHERE labeler = ?", (labeler,))
    return _fernet().decrypt(rows[0]["token"].encode()).decode() if rows else None


def saved_at(labeler):
    rows = db.query("SELECT saved_at FROM lasair_tokens WHERE labeler = ?", (labeler,))
    return rows[0]["saved_at"] if rows else None


def _rejected(e):
    return isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code in (401, 403)


def _throttled(e):
    return isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code == 429


def check_token(token):
    """Raise TokenError unless Lasair accepts token. A count cone search: one of the hour's calls, no data."""
    try:
        _post("cone", token, {"ra": 0, "dec": 0, "radius": 1, "requestType": "count"})
    except requests.RequestException as e:
        if _rejected(e):
            raise TokenError("Lasair didn't accept that token. Copy it again from your Lasair profile page.")
        if _throttled(e):
            raise TokenError("Lasair says this token has used its calls for the hour; try again later.")
        raise TokenError(f"Couldn't reach Lasair to check the token ({type(e).__name__}); try again later.")


def save_token(labeler, token):
    token = (token or "").strip()
    if not token:
        raise TokenError("Paste your Lasair API token.")
    check_token(token)
    db.execute("INSERT OR REPLACE INTO lasair_tokens (labeler, token, saved_at) VALUES (?, ?, ?)",
               (labeler, _fernet().encrypt(token.encode()).decode(), db.now()))


def forget_token(labeler):
    db.execute("DELETE FROM lasair_tokens WHERE labeler = ?", (labeler,))


# ---------- Sherlock ----------

def _plain(text):
    """Sherlock's description has catalog links in it; keep the words."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip() or None


def _sherlock(record, oid):
    """(classification, description) from either shape Lasair returns: the full record's
    {"classifications": {oid: [class, description]}, ...} or the lite record's flat columns."""
    if "classifications" in record:
        pair = (record["classifications"] or {}).get(oid) or next(iter((record["classifications"] or {}).values()), None)
        return (pair[0], _plain(pair[1] if len(pair) > 1 else None)) if pair else (None, None)
    return record.get("classification"), _plain(record.get("description"))


def fetch(dia_object_id, token):
    """{"lsst": {"classification", "description", "n_crossmatches"} | None, "errors": {}}, or
    errors["login"] when Lasair refuses the token. dia_object_id is None for objects sampled from ZTF."""
    result = {"lsst": None, "errors": {}}
    if not dia_object_id:
        return result
    oid = str(dia_object_id)
    try:
        data = _post("sherlock/object", token, {"objectId": oid})
    except requests.RequestException as e:
        key = "login" if _rejected(e) else "lsst"
        result["errors"][key] = "throttled: this hour's Lasair calls are used up" if _throttled(e) else str(e)
        return result
    record = data.get(oid) if isinstance(data, dict) else None
    if not record:
        result["errors"]["lsst"] = "not in Lasair"
        return result
    classification, description = _sherlock(record, oid)
    result["lsst"] = {"classification": classification, "description": description,
                      "n_crossmatches": len(record.get("crossmatches") or [])}
    return result
