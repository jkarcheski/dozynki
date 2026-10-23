"""Object filters shared by sampling (an ANTARES query) and queue serving (SQL),
so "only objects with >= N detections / alerts in this date range" means the
same thing in both places.

Dates are UTC calendar days (YYYY-MM-DD); date_to is inclusive.
date_mode picks which date has to fall in the range:
  first   the object's first alert (new objects in that window)
  latest  its most recent alert (ANTARES's newest_alert includes ZTF alerts)
  active  first <= date_to and latest >= date_from (alerting at some point across the window)
"""

from dataclasses import asdict, dataclass
from datetime import date

MJD_UNIX_EPOCH = 40587
DATE_MODES = ("first", "latest", "active")
ES_FIELD = {"first": "properties.oldest_alert_observation_time", "latest": "properties.newest_alert_observation_time"}


def _mjd(day):
    return (date.fromisoformat(day) - date(1970, 1, 1)).days + MJD_UNIX_EPOCH


@dataclass
class Filters:
    min_det: int = 1               # detections (ANTARES num_mag_values: LSST + ZTF, no upper limits)
    date_from: str | None = None
    date_to: str | None = None
    date_mode: str = "first"

    def __post_init__(self):
        if self.date_mode not in DATE_MODES:
            raise ValueError(f"date_mode must be one of {DATE_MODES}")
        self.date_from = self.date_from or None
        self.date_to = self.date_to or None

    @property
    def active(self):
        return self.min_det > 1 or self.date_from is not None or self.date_to is not None

    def mjd_range(self):
        lo = _mjd(self.date_from) if self.date_from else None
        hi = _mjd(self.date_to) + 1 if self.date_to else None  # inclusive end day
        return lo, hi

    def es_filters(self):
        out = []
        if self.min_det > 1:
            out.append({"range": {"properties.num_mag_values": {"gte": self.min_det}}})
        lo, hi = self.mjd_range()
        if lo is None and hi is None:
            return out
        if self.date_mode == "active":
            if hi is not None:
                out.append({"range": {ES_FIELD["first"]: {"lt": hi}}})
            if lo is not None:
                out.append({"range": {ES_FIELD["latest"]: {"gte": lo}}})
        else:
            bounds = {k: v for k, v in (("gte", lo), ("lt", hi)) if v is not None}
            out.append({"range": {ES_FIELD[self.date_mode]: bounds}})
        return out

    def sql(self):
        """WHERE fragment over queue.n_det / first_mjd / last_mjd."""
        where, params = [], []
        if self.min_det > 1:
            where.append("n_det >= ?")
            params.append(self.min_det)
        lo, hi = self.mjd_range()
        first_col, last_col = "first_mjd", "last_mjd"
        if self.date_mode == "active":
            if hi is not None:
                where.append(f"{first_col} < ?"); params.append(hi)
            if lo is not None:
                where.append(f"{last_col} >= ?"); params.append(lo)
        else:
            col = first_col if self.date_mode == "first" else last_col
            if lo is not None:
                where.append(f"{col} >= ?"); params.append(lo)
            if hi is not None:
                where.append(f"{col} < ?"); params.append(hi)
        return " AND ".join(where), params

    def as_params(self):
        return asdict(self)
