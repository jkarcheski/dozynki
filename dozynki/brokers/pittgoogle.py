"""Pitt-Google Broker: random LSST objects from its BigQuery archive of the alert stream.

The archive holds one row per alert (the Rubin alert schema minus cutouts), so a
sample is drawn over diaObjects, not rows: objects with many alerts are not up-weighted.
Queries are billed to the caller's Google Cloud project (PITTGOOGLE_BILLING_PROJECT),
and each is capped at PITTGOOGLE_MAX_BYTES scanned.
"""

from ..config import PITTGOOGLE_BILLING_PROJECT, PITTGOOGLE_MAX_BYTES, PITTGOOGLE_TABLE


class Unavailable(Exception):
    """Not set up here (no library, project or credentials) or the query failed."""


def _client():
    try:
        from google.auth.exceptions import DefaultCredentialsError
        from google.cloud import bigquery
    except ImportError:
        raise Unavailable("google-cloud-bigquery is not installed (pip install -r requirements.txt)")
    if not PITTGOOGLE_BILLING_PROJECT:
        raise Unavailable("set PITTGOOGLE_BILLING_PROJECT to the Google Cloud project that pays for queries")
    try:
        return bigquery, bigquery.Client(project=PITTGOOGLE_BILLING_PROJECT)
    except DefaultCredentialsError:
        raise Unavailable("no Google Cloud credentials: run `gcloud auth application-default login`")


def _where(filters):
    """Filters as SQL over the per-object columns; n_det counts LSST diaSources only (ANTARES adds ZTF)."""
    where, params = [], {}
    if filters.min_det > 1:
        where.append("n_det >= @min_det")
        params["min_det"] = ("INT64", filters.min_det)
    lo, hi = filters.mjd_range()
    if filters.date_mode == "active":
        if hi is not None:
            where.append("first_mjd < @hi")
        if lo is not None:
            where.append("last_mjd >= @lo")
    else:
        col = "first_mjd" if filters.date_mode == "first" else "last_mjd"
        if lo is not None:
            where.append(f"{col} >= @lo")
        if hi is not None:
            where.append(f"{col} < @hi")
    if lo is not None:
        params["lo"] = ("FLOAT64", lo)
    if hi is not None:
        params["hi"] = ("FLOAT64", hi)
    return " AND ".join(where) or "TRUE", params


def random_lsst_query(n, seed, filters):
    """(sql, params): n diaObjects in a random order fixed by seed (a hash of diaObjectId, like ANTARES's night order)."""
    where, params = _where(filters)
    sql = f"""
        WITH objects AS (
          SELECT diaObject.diaObjectId AS dia_object_id,
                 ANY_VALUE(diaObject.ra) AS ra, ANY_VALUE(diaObject.dec) AS dec,
                 MAX(diaObject.nDiaSources) AS n_det,
                 MIN(diaObject.firstDiaSourceMjdTai) AS first_mjd,
                 MAX(diaObject.lastDiaSourceMjdTai) AS last_mjd,
                 COUNT(*) AS num_alerts
          FROM `{PITTGOOGLE_TABLE}`
          WHERE diaObject.diaObjectId IS NOT NULL
          GROUP BY dia_object_id
        )
        SELECT * FROM objects
        WHERE {where}
        ORDER BY FARM_FINGERPRINT(CONCAT(CAST(dia_object_id AS STRING), ':', CAST(@seed AS STRING)))
        LIMIT @n"""
    return sql, {**params, "seed": ("INT64", seed), "n": ("INT64", n)}


def random_lsst_objects(n, seed, filters):
    """(sql, params, objects); objects carry the listing-summary keys the queue reads."""
    bigquery, client = _client()
    sql, params = random_lsst_query(n, seed, filters)
    config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter(k, t, v) for k, (t, v) in params.items()],
        maximum_bytes_billed=PITTGOOGLE_MAX_BYTES)
    try:
        rows = list(client.query(sql, job_config=config).result())
    except Exception as e:  # google.api_core errors: permissions, the bytes cap, bad table
        raise Unavailable(f"Pitt-Google query failed: {getattr(e, 'message', None) or e}")
    objects = [{"locus_id": None, "ra": r["ra"], "dec": r["dec"], "dia_object_id": str(r["dia_object_id"]),
                "num_alerts": r["num_alerts"], "n_det": r["n_det"], "first_mjd": r["first_mjd"],
                "last_mjd": r["last_mjd"]} for r in rows]
    return sql, {k: v for k, (_, v) in params.items()}, objects
