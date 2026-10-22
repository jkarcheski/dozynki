"""Export the training set.

Writes to exports/<timestamp>/:
  labels.parquet / .csv     every label row (blind and revealed phases)
  objects.parquet / .csv    one row per object: final label + blind label + broker summaries
  lightcurves.parquet       object_id -> normalized photometry (JSON), as seen when labeled

Broker classifications are kept in their own columns (prefixed broker_) so they
can be used as features or for bias checks, never mixed into the label.

Run directly:  python -m dozynki.export
"""

import json
from datetime import datetime, timezone

import pandas as pd

from . import assemble, db
from .config import EXPORT_DIR
from .labels import CLASS_BY_KEY


def _broker_summary(raw):
    op = assemble.opinions(raw)
    out = {
        "broker_lsst_reliability_mean": op["reliability"]["lsst_mean"],
        "broker_ztf_drb_mean": op["reliability"]["ztf_drb_mean"],
        "broker_antares_tags": json.dumps(op["antares"]["tags"]),
        "broker_babamul_properties": json.dumps((op["babamul"] or {}).get("properties")),
        "broker_babamul_ztf_match": (op["babamul"].get("ztf_match") or {}).get("objectId"),
    }
    for survey in ("lsst", "ztf"):
        for clf, entry in ((op["alerce"] or {}).get(survey) or {}).items():
            out[f"broker_alerce_{survey}_{clf}_top"] = entry["top"]
            out[f"broker_alerce_{survey}_{clf}_probs"] = json.dumps(entry["probs"])
    return out


def run():
    labels = pd.DataFrame(db.query("SELECT * FROM labels ORDER BY id"))
    if labels.empty:
        return {"error": "no labels yet"}
    queue = pd.DataFrame(db.query("SELECT * FROM queue"))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = EXPORT_DIR / stamp
    out_dir.mkdir(parents=True)

    labels["is_transient"] = labels["klass"].map(lambda k: CLASS_BY_KEY.get(k, {}).get("transient"))
    labels.to_parquet(out_dir / "labels.parquet", index=False)
    labels.to_csv(out_dir / "labels.csv", index=False)

    final = labels.groupby("object_id").tail(1).set_index("object_id")
    blind = labels[labels["phase"] == "blind"].groupby("object_id").tail(1).set_index("object_id")
    objects, curves = [], []
    for oid, row in final.iterrows():
        raw = assemble._gather(oid)
        if raw["antares"] is None:
            continue
        ev = assemble.evidence(oid, raw)
        q = queue[queue["object_id"] == oid].iloc[0]
        xm = ev["xmatch"] or {}
        top_host = (xm.get("host_candidates") or [None])[0] or {}
        rec = {
            "object_id": oid,
            "antares_locus": ev["locus_id"],
            "ztf_object_id": ev["ztf_object_id"],
            "ra": ev["ra"],
            "dec": ev["dec"],
            "selection_source": q["source"],
            "selection_params": q["source_params"],
            "label": row["klass"],
            "subclass": row["subclass"],
            "is_real": row["is_real"],
            "is_transient": row["is_transient"],
            "confidence": row["confidence"],
            "flags": row["flags"],
            "host": row["host"],
            "label_phase": row["phase"],
            "blind_label": blind["klass"].get(oid) if oid in blind.index else None,
            "changed_after_reveal": bool(oid in blind.index and blind.loc[oid, "klass"] != row["klass"]),
            "labeler": row["labeler"],
            "labeled_at": row["created_at"],
            "gal_b": (xm.get("context") or {}).get("gal_b"),
            "ecl_lat": (xm.get("context") or {}).get("ecl_lat"),
            "xm_top_host_ls_id": top_host.get("ls_id"),
            "xm_top_host_d_dlr": top_host.get("d_dlr"),
            "xm_top_host_zphot": top_host.get("z_phot_median"),
            **{f"snap_{k}": v for k, v in json.loads(row["snapshot"] or "{}").items() if k != "fetched_at"},
            **_broker_summary(raw),
        }
        objects.append(rec)
        curves.append({"object_id": oid, "label": row["klass"], "points": json.dumps(ev["points"])})

    objs = pd.DataFrame(objects)
    objs.to_parquet(out_dir / "objects.parquet", index=False)
    objs.to_csv(out_dir / "objects.csv", index=False)
    pd.DataFrame(curves).to_parquet(out_dir / "lightcurves.parquet", index=False)
    return {"dir": str(out_dir), "n_labels": len(labels), "n_objects": len(objs)}


if __name__ == "__main__":
    print(run())
