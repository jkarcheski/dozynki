"""Sky positions of queued objects with their labels, for the sky map page (/sky).

Each object gets equatorial, Galactic and ecliptic coordinates (astropy, the same
frames crossmatch.context() uses, so |b| here is what the galactic_plane rule sees)
and its truth label as in evaluate.py: first blind label, or latest of any phase.
Unlabeled queue entries come back with klass None, so the page can show how the
sample covers the sky. Like evaluate.py, only the uniform random sample is
returned unless all_sources is set.
"""

import astropy.units as u
from astropy.coordinates import SkyCoord

from . import db
from .evaluate import RANDOM_SOURCES, truth_labels


def objects(truth="blind", all_sources=False):
    labels = truth_labels(truth)
    rows = [r for r in db.query("SELECT object_id, ra, dec, source, status FROM queue WHERE ra IS NOT NULL")
            if all_sources or r["source"] in RANDOM_SOURCES]
    if not rows:
        return []
    c = SkyCoord([r["ra"] for r in rows] * u.deg, [r["dec"] for r in rows] * u.deg)
    gal, ecl = c.galactic, c.barycentrictrueecliptic
    out = []
    for i, r in enumerate(rows):
        lab = labels.get(r["object_id"])
        out.append({
            "object_id": r["object_id"], "status": r["status"],
            "ra": r["ra"], "dec": r["dec"],
            "l": float(gal.l.deg[i]), "b": float(gal.b.deg[i]),
            "elon": float(ecl.lon.deg[i]), "elat": float(ecl.lat.deg[i]),
            "klass": lab["klass"] if lab else None,
            "subclass": lab["subclass"] if lab else None,
            "alt_classes": lab["alt_classes"] if lab else [],
        })
    return out
