"""Score a rejection pipeline (rejection.py) against the human labels.

Truth is each object's first *blind* label by default: several rules read broker
output, and a label made after seeing that output would flatter them. "unsure"
labels are left out of every rate and counted separately.

Only objects from the uniform random sample (queue.source antares_random_lsst)
are scored by default, so the rates estimate what the pipeline does to the LSST
stream within the sampling filters recorded in each object's selection params.

The target is what the pipeline is meant to keep: a list of class keys
(labels.py) and transient subclasses written "transient:nuclear", or the
shorthands "real" (classes with real=True) and "transient". With include_alt,
an object also counts as target when one of its "could also be" classes is.
Metrics, each with a Wilson 95% interval:
  completeness  target objects kept / target objects
  purity        target objects kept / all kept
  fpr           non-target kept / non-target          (false-positive rate)
  bogus_leak    bogus kept / bogus
plus the no-filter baseline (purity = prevalence), per-step rejections by class,
rejections only that step makes, and the metrics with each step left out.

Run:  python -m dozynki.evaluate [--pipeline my_cuts.json] [--truth blind|final] [--target transient,agn]
Writes exports/rejection/<timestamp>/: report.txt, metrics.json, decisions.csv
The interactive page (/rejection in the app) calls evaluate() with save=False.
"""

import argparse
import copy
import json
import math
import os
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from . import db, rejection
from .config import EXPORT_DIR
from .labels import CLASS_BY_KEY, CLASSES, TRANSIENT_SUBCLASSES

RANDOM_SOURCES = ("antares_random_lsst",)
SHORTHANDS = {"real": [c["key"] for c in CLASSES if c["real"]],
              "transient": [c["key"] for c in CLASSES if c["transient"]]}


def wilson(k, n, z=1.96):
    if n == 0:
        return None, None, None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return p, max(0.0, centre - half), min(1.0, centre + half)


def truth_labels(truth="blind"):
    """{object_id: label row}: first blind label, or the latest label of any phase."""
    if truth == "blind":
        sql = "SELECT * FROM labels WHERE id IN (SELECT MIN(id) FROM labels WHERE phase = 'blind' GROUP BY object_id)"
    elif truth == "final":
        sql = "SELECT * FROM labels WHERE id IN (SELECT MAX(id) FROM labels GROUP BY object_id)"
    else:
        raise ValueError("truth must be blind or final")
    return {r["object_id"]: {"klass": r["klass"], "subclass": r["subclass"],
                             "alt_classes": json.loads(r.get("alt_classes") or "[]")}
            for r in db.query(sql)}


def parse_target(target):
    """"transient,agn" or ["transient:nuclear", ...] -> set of class / class:subclass keys."""
    keys = target.split(",") if isinstance(target, str) else list(target)
    out = set()
    for k in (k.strip() for k in keys if k.strip()):
        if k in SHORTHANDS:
            out.update(SHORTHANDS[k])
        elif k in CLASS_BY_KEY or (k.startswith("transient:") and k.split(":", 1)[1] in {s["key"] for s in TRANSIENT_SUBCLASSES}):
            out.add(k)
        else:
            raise ValueError(f"unknown target {k!r}")
    if not out:
        raise ValueError("empty target")
    return out


def is_target(label, target, include_alt=False):
    """A subclass target only matches the primary transient label (alt classes have no subclass)."""
    if label["klass"] in target:
        return True
    if label["klass"] == "transient" and label["subclass"] and f"transient:{label['subclass']}" in target:
        return True
    return include_alt and any(a in target for a in label["alt_classes"])


def metrics(classes, pos, kept):
    """Parallel lists over the scored objects: truth class, is-target, kept."""
    tp = sum(p and k for p, k in zip(pos, kept))
    n_pos, n_kept = sum(pos), sum(kept)
    fp = n_kept - tp
    n_bogus = sum(c == "bogus" for c in classes)
    bogus_kept = sum(c == "bogus" and k for c, k in zip(classes, kept))
    out = {}
    for name, (k, n) in {
        "completeness": (tp, n_pos),
        "purity": (tp, n_kept),
        "fpr": (fp, len(classes) - n_pos),
        "bogus_leak": (bogus_kept, n_bogus),
    }.items():
        p, lo, hi = wilson(k, n)
        out[name] = {"value": p, "ci95": [lo, hi], "k": k, "n": n}
    out["kept"] = n_kept
    out["rejected"] = len(classes) - n_kept
    return out


def evaluate(pipeline=rejection.DEFAULT_PIPELINE, truth="blind", target="transient", include_alt=False,
             all_sources=False, save=True):
    pipeline = rejection.resolve(pipeline)
    target_keys = parse_target(target)
    steps = [rejection.step_key(s) for s in pipeline]
    labels = truth_labels(truth)
    queue = {r["object_id"]: r for r in db.query("SELECT object_id, source, source_params FROM queue")}
    population = {oid: lab for oid, lab in labels.items()
                  if all_sources or queue.get(oid, {}).get("source") in RANDOM_SOURCES}
    ver, results = rejection.run(population, pipeline, save=save)

    rows, excluded = [], Counter()
    for oid, lab in population.items():
        if oid not in results:
            excluded["not fetched"] += 1
            continue
        if CLASS_BY_KEY.get(lab["klass"], {}).get("real") is None:
            excluded[lab["klass"]] += 1
            continue
        d = results[oid]["decisions"]
        first = rejection.first_rejection(d, steps)
        feats = {k: v for k, v in results[oid]["features"].items() if not isinstance(v, dict)}
        rows.append({"object_id": oid, "truth": lab["klass"], "subclass": lab["subclass"],
                     "alt_classes": ",".join(lab["alt_classes"]),
                     "target": is_target(lab, target_keys, include_alt),
                     "kept": first is None, "rejected_by": first, **{f"step:{s}": d[s] for s in steps}, **feats})
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("no labeled objects to score")
    classes = [c["key"] for c in CLASSES if c["key"] in set(df["truth"])]
    truth_list, pos = list(df["truth"]), list(df["target"])

    rejects = {s: df[f"step:{s}"].fillna(False).astype(bool) for s in steps}
    report = {
        "pipeline_version": ver,
        "pipeline": pipeline,
        "truth": truth,
        "target": sorted(target_keys),
        "include_alt": include_alt,
        "population": {"scored": len(df), "n_target": int(df["target"].sum()),
                       "by_class": df["truth"].value_counts().to_dict(),
                       "excluded": dict(excluded),
                       "sampling_filters": sorted({json.dumps(json.loads(queue[o]["source_params"] or "{}").get("filters"))
                                                   for o in df["object_id"]})},
        "baseline": metrics(truth_list, pos, [True] * len(df)),
        "pipeline_metrics": metrics(truth_list, pos, list(df["kept"])),
        "confusion": {c: {"kept": int(((df["truth"] == c) & df["kept"]).sum()),
                          "rejected": int(((df["truth"] == c) & ~df["kept"]).sum()),
                          **{f"first:{s}": int(((df["truth"] == c) & (df["rejected_by"] == s)).sum()) for s in steps}}
                      for c in classes},
        "steps": {},
    }
    for s in steps:
        others = [o for o in steps if o != s]
        any_other = pd.concat([rejects[o] for o in others], axis=1).any(axis=1) if others else pd.Series(False, index=df.index)
        report["steps"][s] = {
            "broker": rejection.RULES[s.split("[")[0]].broker,
            "no_data": int(df[f"step:{s}"].isna().sum()),
            "alone_rejects": {c: int((rejects[s] & (df["truth"] == c)).sum()) for c in classes},
            "alone": metrics(truth_list, pos, list(~rejects[s])),
            "unique_rejects": {c: int((rejects[s] & ~any_other & (df["truth"] == c)).sum()) for c in classes},
            "without_this_step": metrics(truth_list, pos, list(~any_other)),
        }
    return report, df


def sweep(pipeline, step_index, param, values, **kw):
    """Pipeline metrics as one step's parameter takes each value (nothing saved)."""
    out = []
    for v in values:
        p = copy.deepcopy(rejection.resolve(pipeline))
        p[step_index][param] = v
        report, _ = evaluate(p, save=False, **kw)
        out.append({"value": v, **{k: report["pipeline_metrics"][k] for k in ("completeness", "purity", "fpr", "bogus_leak", "kept")}})
    return out


def _fmt(m):
    if m["value"] is None:
        return f"{'n/a':>6}  ({m['k']}/{m['n']})"
    lo, hi = m["ci95"]
    return f"{m['value']:6.3f}  [{lo:.3f}, {hi:.3f}]  ({m['k']}/{m['n']})"


def text_report(r):
    pm, base = r["pipeline_metrics"], r["baseline"]
    lines = [
        f"Pipeline {r['pipeline_version']}   truth={r['truth']}   target={','.join(r['target'])}"
        + ("   (incl. could-also-be)" if r["include_alt"] else ""),
        "  " + " -> ".join(rejection.step_key(s) for s in r["pipeline"]),
        f"Scored {r['population']['scored']} objects ({r['population']['n_target']} target): {r['population']['by_class']}",
        f"Excluded: {r['population']['excluded'] or 'none'}",
        f"Sampling filters in this population: {', '.join(r['population']['sampling_filters'])}",
        "",
        f"{'':14}{'no filter':>38}   {'pipeline':>38}",
    ]
    for k in ("completeness", "purity", "fpr", "bogus_leak"):
        lines.append(f"{k:14}{_fmt(base[k]):>38}   {_fmt(pm[k]):>38}")
    lines.append(f"{'kept':14}{base['kept']:>38}   {pm['kept']:>38}")

    steps = list(r["steps"])
    lines += ["", "Confusion (truth x outcome; 'first:' = step that rejected it first)"]
    cols = ["kept", "rejected"] + [f"first:{s}" for s in steps]
    for c, row in r["confusion"].items():
        lines.append(f"  {c:10} " + "  ".join(f"{col.removeprefix('first:')}={row[col]}" for col in cols if row[col] or col in ("kept", "rejected")))

    lines += ["", "Per step  (* = reads broker output)"]
    for s, st in r["steps"].items():
        wo = st["without_this_step"]
        lines += [
            f"  {s}{' *' if st['broker'] else ''}   (no data: {st['no_data']})",
            f"    alone rejects:  {st['alone_rejects']}",
            f"    only it rejects: {st['unique_rejects']}",
            f"    pipeline without it: completeness {_fmt(wo['completeness'])}, purity {_fmt(wo['purity'])}, fpr {_fmt(wo['fpr'])}",
        ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pipeline", help='JSON file: [{"rule": "gaia_star", "max_sep": 1.5}, ...]')
    ap.add_argument("--truth", choices=("blind", "final"), default="blind")
    ap.add_argument("--target", default="transient",
                    help="comma list of classes / transient:<subclass>, or real / transient (default transient)")
    ap.add_argument("--include-alt", action="store_true", help="count 'could also be' classes toward the target")
    ap.add_argument("--all-sources", action="store_true", help="also score objects not from the random sample")
    ap.add_argument("--list-rules", action="store_true")
    args = ap.parse_args(argv)

    if args.list_rules:
        for rule in rejection.RULES.values():
            print(f"{rule.name:20} {'broker ' if rule.broker else '       '}{rule.defaults}  {rule.description}")
        return
    if args.pipeline and not os.path.exists(args.pipeline):
        ap.error(f"{args.pipeline} not found; copy cuts.example.json (the default pipeline) and edit it")
    pipeline = json.load(open(args.pipeline)) if args.pipeline else rejection.DEFAULT_PIPELINE
    try:
        report, df = evaluate(pipeline, args.truth, args.target, args.include_alt, args.all_sources)
    except ValueError as e:
        ap.error(str(e))
    text = text_report(report)
    print(text)

    out_dir = EXPORT_DIR / "rejection" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True)
    (out_dir / "report.txt").write_text(text + "\n")
    (out_dir / "metrics.json").write_text(json.dumps(report, indent=2, default=str))
    df.to_csv(out_dir / "decisions.csv", index=False)
    print(f"\nWrote {out_dir}")


if __name__ == "__main__":
    main()
