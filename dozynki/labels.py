"""Label taxonomy. Edit here to change the buttons in the UI.

`real` says whether the class counts as a real astrophysical source. Only
classes with real=True and transient=True go on to anomaly scoring.
"""

CLASSES = [
    {"key": "bogus", "label": "Bogus / artifact", "hotkey": "1", "real": False, "transient": False},
    {"key": "sso", "label": "Asteroid / SSO", "hotkey": "2", "real": True, "transient": False},
    {"key": "star", "label": "Star / variable star", "hotkey": "3", "real": True, "transient": False},
    {"key": "agn", "label": "AGN / QSO", "hotkey": "4", "real": True, "transient": False},
    {"key": "transient", "label": "Transient", "hotkey": "5", "real": True, "transient": True},
    {"key": "other", "label": "Other real", "hotkey": "6", "real": True, "transient": False},
    {"key": "unsure", "label": "Unsure", "hotkey": "0", "real": None, "transient": None},
]

# Optional refinement shown only when "transient" is picked.
TRANSIENT_SUBCLASSES = [
    {"key": "sn_like", "label": "SN-like"},
    {"key": "nuclear", "label": "Nuclear / TDE"},
    {"key": "cv_nova", "label": "CV / nova / flare"},
    {"key": "fast", "label": "Fast / other"},
]

# "watch" also parks the object on the watchlist (queue status 'watch') so it
# comes back for a follow-up label once new alerts arrive.
WATCH_FLAG = "watch"

FLAGS = [
    {"key": WATCH_FLAG, "label": "Watch for more data", "hotkey": "w"},
    {"key": "bad_subtraction", "label": "Bad subtraction"},
    {"key": "dipole", "label": "Dipole"},
    {"key": "near_bright_star", "label": "Near bright star"},
    {"key": "wrong_host", "label": "Broker host wrong"},
    {"key": "wrong_ztf_assoc", "label": "ZTF-LSST assoc. wrong"},
    {"key": "few_points", "label": "Too few points"},
    {"key": "negative_flux", "label": "Negative diff flux"},
]

CONFIDENCE = ["low", "medium", "high"]

CLASS_BY_KEY = {c["key"]: c for c in CLASSES}
