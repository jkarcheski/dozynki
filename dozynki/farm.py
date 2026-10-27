"""Farm: coins earned by vetting, spent in the landing page's shop.

Coins are counted from labels like achievements: an object pays once per
labeler, by that labeler's latest label of it, 1 coin for the label (whatever
the class, and whether or not the brokers agree), 1 for a chosen host (not
hostless) and 1 for any flag or a note, so at most 3. Re-labeling or deleting
moves the count with it. Only purchases are stored; balance = earned - spent.

Unlocks are not for sale: each comes with the first tier of an achievement.
Items are drawn into the landing page's wheat field by harvest.js, by key.
"""

import json

from . import achievements, db

# (key, name, price); cheapest first, which is their order in the shop.
SHOP = [
    ("geese", "Geese", 25),
    ("well", "Well", 40),
    ("beehives", "Beehives", 60),
    ("sunflowers", "Sunflowers", 80),
    ("poppies", "Poppies", 80),
    ("cow", "Cow", 150),
    ("barn", "Barn", 200),
    ("horse", "Horse", 250),
    ("windmill", "Windmill", 500),
    ("wreath", "Harvest wreath", 800),
]
# (key, name, achievement key): owned once that achievement has its first tier.
UNLOCKS = [
    ("telescope", "Backyard telescope", "transients"),
    ("weathervane", "Weathervane", "agn"),
    ("rocks", "Rock garden", "sso"),
    ("lantern", "Lantern", "nights"),
    ("stork", "Stork's nest", "rare"),
]
PRICE = {key: price for key, _, price in SHOP}


def earned(labeler):
    """{label, host, extra, total}: coins from the labeler's latest label of each object."""
    latest = db.query(
        "SELECT host, flags, notes FROM labels WHERE id IN "
        "(SELECT MAX(id) FROM labels WHERE COALESCE(labeler, 'anonymous') = ? GROUP BY object_id)",
        (labeler,),
    )
    host = sum(bool(r["host"]) and not json.loads(r["host"]).get("hostless") for r in latest)
    extra = sum(bool(json.loads(r["flags"] or "[]")) or bool((r["notes"] or "").strip()) for r in latest)
    return {"label": len(latest), "host": host, "extra": extra, "total": len(latest) + host + extra}


def state(labeler):
    coins = earned(labeler)
    bought = db.purchases(labeler)
    spent = sum(p["price"] for p in bought)
    owned = {p["item"] for p in bought}
    tiers = {a["key"]: a for a in achievements.summary(labeler)}
    return {
        "coins": coins | {"spent": spent, "balance": coins["total"] - spent},
        "shop": [{"key": k, "name": n, "price": p, "owned": k in owned} for k, n, p in SHOP],
        "unlocks": [{"key": k, "name": n, "achievement": tiers[a]["name"], "what": tiers[a]["what"],
                     "owned": tiers[a]["earned"] > 0} for k, n, a in UNLOCKS],
    }


def buy(labeler, item):
    """Spend coins on a shop item; ValueError if it is unknown, owned or too dear."""
    if item not in PRICE:
        raise ValueError(f"no shop item {item!r}")
    now = state(labeler)
    if any(s["key"] == item and s["owned"] for s in now["shop"]):
        raise ValueError("already on your farm")
    if now["coins"]["balance"] < PRICE[item]:
        raise ValueError(f"needs {PRICE[item]} coins, you have {now['coins']['balance']}")
    db.add_purchase(labeler, item, PRICE[item])
    return state(labeler)
