"""Checks that screening keeps only a small record of the objects a filter throws away (db.forget_rejected):
their cache and per-step decisions go, while kept, labeled and object-list objects keep theirs.

Run with: .venv/bin/python -m pytest tests
"""

from dozynki import db


def _screening(oid):
    db.add_to_queue(oid, None, 1.0, 2.0, "antares_night_filtered", {"screen": {"name": "f"}}, screen="f")
    db.cache_put(oid, "antares", {"points": [1, 2, 3]})
    db.save_rejections(oid, "v1", {"gaia_star": 1})


def _kept(oid):
    return {r["kind"] for r in db.query("SELECT kind FROM cache WHERE object_id = ?", (oid,))}, \
        db.query("SELECT COUNT(*) n FROM rejections WHERE object_id = ?", (oid,))[0]["n"]


def test_rejected_object_keeps_only_its_queue_row():
    _screening("rej1")
    db.set_screened("rej1", "filtered", "gaia_star")
    assert _kept("rej1") == (set(), 0)
    row = db.get_queue_item("rej1")
    assert (row["status"], row["screen"], row["screen_result"]) == ("filtered", "f", "gaia_star")
    assert row["screened_at"]


def test_kept_object_keeps_its_cache():
    _screening("keep1")
    db.set_screened("keep1", "pending", "kept")
    assert _kept("keep1") == ({"antares"}, 1)
    assert db.get_queue_item("keep1")["screened_at"]


def test_labeled_and_listed_rejects_keep_their_cache():
    _screening("lab1")
    db.add_label({"object_id": "lab1", "phase": "blind", "klass": "SN", "created_at": db.now()})
    _screening("list1")
    db.execute("INSERT INTO list_members (list, object_id, entry, kind, added_at) "
               "VALUES ('mine', 'list1', 'list1', 'dia_object', ?)", (db.now(),))
    for oid in ("lab1", "list1"):
        db.set_screened(oid, "filtered", "gaia_star")
        assert _kept(oid) == ({"antares"}, 0)


def test_object_taken_over_by_random_mode_is_not_dropped():
    _screening("took1")
    db.add_to_queue("took1", None, 1.0, 2.0, "antares_random_lsst", {})
    db.set_screened("took1", "filtered", "gaia_star")
    assert db.get_queue_item("took1")["status"] == "pending"
    assert _kept("took1") == ({"antares"}, 1)
