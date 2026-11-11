"""NYC DOT Street Closures Due to Construction Activities by Block (i6b5-j7bu)."""

from __future__ import annotations

import math
from typing import Any

from .socrata import dataset_page_url, soda_get
from .signs import normalize_street_name

DATASET_ID = "i6b5-j7bu"
SOURCE_URL = dataset_page_url(DATASET_ID)

# Omit the_geom from default select to keep payloads small; include optional flag.
CLOSURE_FIELDS = (
    "segmentid,oft,onstreetname,fromstreetname,tostreetname,borough_code,"
    "work_start_date,work_end_date,uniqueid,purpose"
)


def fetch_closures_near(
    lat: float,
    lon: float,
    *,
    radius_m: float = 400,
    limit: int = 100,
    include_geometry: bool = False,
) -> dict[str, Any]:
    """Fetch construction-related street closure permits near a point.

    Important: these records indicate permitted work / occupancy windows.
    They do NOT prove an active physical closure at a specific moment.
    """
    # Approximate degrees for within_box; ~111_320 m per degree lat.
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(0.2, abs(math.cos(math.radians(lat)))))
    where = (
        f"within_box(the_geom, {lat + dlat}, {lon - dlon}, {lat - dlat}, {lon + dlon})"
    )
    select = CLOSURE_FIELDS + (",the_geom" if include_geometry else "")
    rows = soda_get(
        DATASET_ID,
        {
            "$select": select,
            "$where": where,
            "$limit": limit,
            "$order": "work_start_date DESC",
        },
    )
    return {
        "dataset_id": DATASET_ID,
        "source": SOURCE_URL,
        "center": {"lat": lat, "lon": lon},
        "radius_m": radius_m,
        "count": len(rows),
        "truncated": len(rows) >= limit,
        "caveat": (
            "Construction permits may indicate a possible restriction or roadway occupancy; "
            "they do not prove an active closure at a specific time. Physical signs and "
            "on-site conditions take precedence."
        ),
        "records": [_normalize_closure(r) for r in rows],
    }


def _normalize_closure(row: dict) -> dict[str, Any]:
    out = {
        "uniqueid": row.get("uniqueid"),
        "segmentid": row.get("segmentid"),
        "borough_code": row.get("borough_code"),
        "on_street": normalize_street_name(row.get("onstreetname")) or None,
        "from_street": normalize_street_name(row.get("fromstreetname")) or None,
        "to_street": normalize_street_name(row.get("tostreetname")) or None,
        "work_start_date": row.get("work_start_date"),
        "work_end_date": row.get("work_end_date"),
        "purpose": row.get("purpose"),
        "source": SOURCE_URL,
        "certainty": "possible_restriction_only",
    }
    if "the_geom" in row:
        out["the_geom"] = row.get("the_geom")
    return out
