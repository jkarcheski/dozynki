"""The vetted_non_transient rule: drops what the filter's owner already labeled asteroid, star or AGN, unless
they listed transient as a "could also be"; the filter lab (no labeler) keeps everything."""

import json

from dozynki import db, rejection, screen

STEP = [{"rule": "vetted_non_transient"}]
KEY = rejection.step_key(rejection.resolve(STEP)[0])


def _decide(label, **params):
    (decision,) = rejection.apply({}, [{"rule": "vetted_non_transient", **params}], label).values()
    return decision


def test_sso_star_agn_are_dropped():
    for klass in ("sso", "star", "agn"):
        assert _decide({"klass": klass, "alt_classes": "[]"}) is True
    assert _decide({"klass": "sso", "alt_classes": None}) is True


def test_could_also_be_transient_is_kept():
    assert _decide({"klass": "star", "alt_classes": json.dumps(["transient"])}) is False


def test_other_and_switched_off_classes_are_kept():
    assert _decide({"klass": "bogus", "alt_classes": None}) is False
    assert _decide({"klass": "transient", "alt_classes": None}) is False
    assert _decide({"klass": "agn", "alt_classes": None}, agn=0) is False


def test_unlabeled_is_kept_and_no_labeler_is_undecided():
    assert _decide({}) is False
    assert rejection.apply({}, STEP)[KEY] is None  # the filter lab


def test_screen_reads_the_owners_latest_label():
    db.put_saved_pipeline("vetted", STEP, owner="ann")
    scr = screen.resolve("vetted")
    assert scr["labeler"] == "ann"
    db.add_label({"object_id": "v1", "labeler": "ann", "phase": "blind", "klass": "transient", "created_at": db.now()})
    db.add_label({"object_id": "v1", "labeler": "ann", "phase": "final", "klass": "star", "created_at": db.now()})
    db.add_label({"object_id": "v2", "labeler": "bo", "phase": "blind", "klass": "star", "created_at": db.now()})
    assert rejection.apply({}, scr["pipeline"], screen._latest_label("v1", "ann"))[KEY] is True
    assert rejection.apply({}, scr["pipeline"], screen._latest_label("v2", "ann"))[KEY] is False
    assert screen._latest_label("v1", None) is None
