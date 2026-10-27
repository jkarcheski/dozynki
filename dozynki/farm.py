"""Farm: coins earned by vetting, spent in the landing page's shop.

Coins are counted from labels like achievements: an object pays once per
labeler, by that labeler's latest label of it, 1 coin for the label (whatever
the class, and whether or not the brokers agree), 1 for a chosen host (not
hostless) and 1 for any flag or a note, so at most 3. Re-labeling or deleting
moves the count with it. Only purchases are stored; balance = earned - spent.

Unlocks are not for sale: each comes with the first tier of an achievement.
Items are drawn into the landing page's wheat field by harvest.js, by key.

The farmer stands in the field too. Wardrobe items are bought like shop items
and worn one per slot (stored in `wearing`); a slot with nothing bought or
worn shows the default: short hair, work clothes, shoes, no hat.
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
# (key, name, slot, price); grouped by slot, cheapest first.
SLOTS = ["hat", "hair", "outfit", "boots"]
WARDROBE = [
    ("straw_hat", "Farmer's straw hat", "hat", 30),
    ("flat_cap", "Flat cap", "hat", 40),
    ("cowboy_hat", "Cowboy hat", "hat", 60),
    ("rogatywka", "Kraków hat with peacock feather", "hat", 400),
    ("long_hair", "Long hair", "hair", 20),
    ("bun", "Bun", "hair", 20),
    ("curly", "Curly hair", "hair", 20),
    ("braids", "Braids with ribbons", "hair", 30),
    ("flannel", "Flannel shirt", "outfit", 60),
    ("overalls", "Overalls", "outfit", 80),
    ("lowicz", "Łowicz striped costume", "outfit", 1000),
    ("krakow", "Kraków costume", "outfit", 1200),
    ("wellies", "Rubber boots", "boots", 30),
    ("cowboy_boots", "Cowboy boots", "boots", 60),
]
SLOT_OF = {key: slot for key, _, slot, _ in WARDROBE}
PRICE = {key: price for key, _, price in SHOP} | {key: price for key, _, _, price in WARDROBE}


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
    worn = db.wearing(labeler)
    return {
        "coins": coins | {"spent": spent, "balance": coins["total"] - spent},
        "shop": [{"key": k, "name": n, "price": p, "owned": k in owned} for k, n, p in SHOP],
        "unlocks": [{"key": k, "name": n, "achievement": tiers[a]["name"], "what": tiers[a]["what"],
                     "owned": tiers[a]["earned"] > 0} for k, n, a in UNLOCKS],
        "slots": SLOTS,
        "wardrobe": [{"key": k, "name": n, "slot": sl, "price": p, "owned": k in owned, "worn": worn.get(sl) == k}
                     for k, n, sl, p in WARDROBE],
        "wearing": {sl: worn.get(sl) for sl in SLOTS},
    }


def buy(labeler, item):
    """Spend coins on a shop or wardrobe item (worn straight away); ValueError if it is unknown, owned or too dear."""
    if item not in PRICE:
        raise ValueError(f"no shop item {item!r}")
    now = state(labeler)
    if any(s["key"] == item and s["owned"] for s in now["shop"] + now["wardrobe"]):
        raise ValueError("already yours")
    if now["coins"]["balance"] < PRICE[item]:
        raise ValueError(f"needs {PRICE[item]} coins, you have {now['coins']['balance']}")
    db.add_purchase(labeler, item, PRICE[item])
    if item in SLOT_OF:
        db.put_on(labeler, SLOT_OF[item], item)
    return state(labeler)


def wear(labeler, slot, item=None):
    """Put on an owned wardrobe item, or with None go back to the slot's default."""
    if slot not in SLOTS:
        raise ValueError(f"no slot {slot!r}")
    if item is not None:
        if SLOT_OF.get(item) != slot:
            raise ValueError(f"{item!r} is not a {slot}")
        if not any(p["item"] == item for p in db.purchases(labeler)):
            raise ValueError("buy it first")
    db.put_on(labeler, slot, item)
    return state(labeler)
