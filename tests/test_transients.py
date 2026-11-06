"""The transient-candidate CSVs: by each labeler's latest label, primary class or a "could also be"."""

import csv
import io
import json

from fastapi.testclient import TestClient

from dozynki import db
from dozynki.app import LABELER, app


def _label(oid, klass, alts=(), labeler=LABELER):
    db.add_label({"object_id": oid, "phase": "blind", "klass": klass, "alt_classes": json.dumps(list(alts)),
                  "labeler": labeler, "created_at": db.now()})


def _ids(also):
    r = TestClient(app).get("/api/transients.csv", params={"also": also})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    return {row["object_id"] for row in csv.DictReader(io.StringIO(r.text))}


def test_transient_csvs_split_primary_from_could_also_be():
    _label("tr_primary", "transient")
    _label("tr_also", "agn", ["transient"])
    _label("tr_other_alt", "star", ["agn"])
    _label("tr_relabeled", "transient")
    _label("tr_relabeled", "bogus")  # the latest label speaks
    _label("tr_someone_else", "transient", labeler="someone-else")
    mine = {"tr_primary", "tr_also", "tr_other_alt", "tr_relabeled", "tr_someone_else"}  # other tests share the db
    assert _ids(False) & mine == {"tr_primary"}
    assert _ids(True) & mine == {"tr_also"}
