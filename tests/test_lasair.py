"""Lasair: each labeler's token, Sherlock's answer in either shape the API documents, and the one shared cache
entry per object (assemble.with_lasair). Lasair's HTTP is faked; no token is needed to run these.

Run with: .venv/bin/python -m pytest tests
"""

from datetime import datetime, timedelta, timezone

import pytest
import requests

from dozynki import assemble, compare, db
from dozynki.brokers import lasair

OID = "170587117208469530"
FULL = {OID: {"classifications": {OID: ["SN", "The transient is possibly associated with <em><a href='x'>SDSS J1</a></em>; "
                                           "a J=17.01 mag galaxy."]},
              "crossmatches": [{"catalogue_object_id": "1"}, {"catalogue_object_id": "2"}]}}
LITE = {OID: {"classification": "VS", "description": "The transient is synonymous with <a href='y'>a star</a>."}}


def _response(status, body=None):
    r = requests.Response()
    r.status_code = status
    r._content = (b"{}" if body is None else __import__("json").dumps(body).encode())
    return r


@pytest.fixture
def lasair_api(monkeypatch):
    """Lasair answers with whatever is in answer[path]; every call is recorded with the token it carried."""
    answer, calls = {}, []

    def post(url, data=None, headers=None, timeout=None):
        path = url.split("/api/", 1)[1].strip("/")
        calls.append((path, headers["Authorization"]))
        return _response(*answer[path])

    monkeypatch.setattr(lasair._session, "post", post)
    return answer, calls


def test_a_token_is_kept_only_if_lasair_accepts_it(lasair_api):
    answer, _ = lasair_api
    answer["cone"] = (401, {"detail": "Invalid token."})
    with pytest.raises(lasair.TokenError, match="didn't accept"):
        lasair.save_token("ana", "bad")
    assert lasair.token_for("ana") is None

    answer["cone"] = (200, {"count": 0})
    lasair.save_token("ana", "  good-token \n")
    assert lasair.token_for("ana") == "good-token"
    assert "good-token" not in db.query("SELECT token FROM lasair_tokens WHERE labeler = 'ana'")[0]["token"]
    lasair.forget_token("ana")
    assert lasair.token_for("ana") is None


@pytest.mark.parametrize("body, cls, n", [(FULL, "SN", 2), (LITE, "VS", 0)])
def test_sherlock_in_either_shape(lasair_api, body, cls, n):
    answer, calls = lasair_api
    answer["sherlock/object"] = (200, body)
    out = lasair.fetch(OID, "tok")
    assert out["errors"] == {} and out["lsst"]["classification"] == cls and out["lsst"]["n_crossmatches"] == n
    assert "<" not in out["lsst"]["description"]  # catalog links stripped
    assert calls == [("sherlock/object", "Token tok")]


def test_fetch_errors(lasair_api):
    answer, _ = lasair_api
    answer["sherlock/object"] = (200, {})
    assert lasair.fetch(OID, "tok")["errors"] == {"lsst": "not in Lasair"}
    answer["sherlock/object"] = (429, {})
    assert lasair.fetch(OID, "tok")["errors"]["lsst"].startswith("throttled")
    answer["sherlock/object"] = (401, {})
    assert "login" in lasair.fetch(OID, "tok")["errors"]
    assert lasair.fetch(None, "tok") == {"lsst": None, "errors": {}}  # ZTF objects aren't asked about


def _token(labeler, lasair_api):
    lasair_api[0]["cone"] = (200, {"count": 0})
    lasair.save_token(labeler, f"tok-{labeler}")


def test_one_cache_entry_shared_by_everyone(lasair_api):
    answer, calls = lasair_api
    oid = "500000000000000001"
    assert assemble.with_lasair(oid, {}, "nobody")["lasair"]["data"] == {"enabled": False}

    _token("bea", lasair_api)
    answer["sherlock/object"] = (200, {oid: FULL[OID]})
    calls.clear()
    assert assemble.with_lasair(oid, {}, "bea")["lasair"]["data"]["lsst"]["classification"] == "SN"
    assert assemble.with_lasair(oid, {}, "bea")["lasair"]["data"]["lsst"]["classification"] == "SN"
    # someone without a token sees what bea's fetched; nobody fetched it twice
    assert assemble.with_lasair(oid, {}, "nobody")["lasair"]["data"]["lsst"]["classification"] == "SN"
    assert calls == [("sherlock/object", "Token tok-bea")]


def test_a_rejected_token_is_its_owners_problem_only(lasair_api):
    answer, _ = lasair_api
    oid = "500000000000000002"
    _token("cy", lasair_api)
    answer["sherlock/object"] = (401, {})
    assert "login" in assemble.with_lasair(oid, {}, "cy")["lasair"]["data"]["errors"]
    assert db.cache_get(oid, "lasair") is None


def test_a_failed_fetch_is_retried_later_not_every_open(lasair_api):
    answer, calls = lasair_api
    oid = "500000000000000003"
    _token("di", lasair_api)
    answer["sherlock/object"] = (429, {})
    assemble.with_lasair(oid, {}, "di")
    answer["sherlock/object"] = (200, {oid: LITE[OID]})
    calls.clear()
    assert assemble.with_lasair(oid, {}, "di")["lasair"]["data"]["errors"]  # too soon to try again
    assert calls == []

    old = (datetime.now(timezone.utc) - timedelta(seconds=assemble.LASAIR_RETRY_SECONDS + 1)).isoformat()
    db.execute("UPDATE cache SET fetched_at = ? WHERE object_id = ? AND kind = 'lasair'", (old, oid))
    assert assemble.with_lasair(oid, {}, "di")["lasair"]["data"]["lsst"]["classification"] == "VS"


def test_sherlock_is_a_compare_source():
    db.cache_put("500000000000000004", "lasair", {"lsst": {"classification": "NT"}, "errors": {}})
    db.cache_put("500000000000000005", "lasair", {"lsst": {"classification": "ORPHAN"}, "errors": {}})
    got = compare.load("lasair:sherlock")
    assert got["500000000000000004"]["mapped"] == "transient" and got["500000000000000004"]["subclass"] == "nuclear"
    assert got["500000000000000005"]["mapped"] is None
    assert any(s["id"] == "lasair:sherlock" for s in compare.sources())
