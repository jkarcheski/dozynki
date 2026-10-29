"""Farm: items drawn into the landing page's wheat field, unlocked by achievements.

Each unlock comes with the first tier of an achievement. Items are drawn into
the field by harvest.js, by key. The farmer stands in the field too, in the
default clothes: short hair, work clothes, shoes, no hat.
"""

from . import achievements

# (key, name, achievement key): owned once that achievement has its first tier.
UNLOCKS = [
    ("telescope", "Backyard telescope", "transients"),
    ("weathervane", "Weathervane", "agn"),
    ("rocks", "Rock garden", "sso"),
    ("lantern", "Lantern", "nights"),
    ("stork", "Stork's nest", "rare"),
]


def state(labeler):
    tiers = {a["key"]: a for a in achievements.summary(labeler)}
    return {
        "unlocks": [{"key": k, "name": n, "achievement": tiers[a]["name"], "what": tiers[a]["what"],
                     "owned": tiers[a]["earned"] > 0} for k, n, a in UNLOCKS],
    }
