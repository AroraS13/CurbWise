"""NYC Street Centerline (CSCL) segments for street-first discovery."""

from __future__ import annotations

from typing import Any

from .socrata import dataset_page_url, soda_get

DATASET_ID = "inkn-q76z"
SOURCE_URL = dataset_page_url(DATASET_ID)

CENTERLINE_FIELDS = (
    "the_geom,physicalid,l_blockfaceid,r_blockfaceid,"
    "l_low_hn,l_high_hn,r_low_hn,r_high_hn,"
    "status,accessible,nonped,boroughcode,segmentlength,"
    "number_park_lanes,full_street_name,street_name,stname_label"
)


def fetch_centerlines_near(
    lat: float,
    lon: float,
    *,
    radius_m: float = 500,
    limit: int = 400,
) -> dict[str, Any]:
    """Fetch road-bed centerlines intersecting a radius around a WGS84 point."""
    rows = soda_get(
        DATASET_ID,
        {
            "$select": CENTERLINE_FIELDS,
            "$where": f"within_circle(the_geom, {lat}, {lon}, {radius_m})",
            "$limit": limit,
            "$order": "physicalid",
        },
    )
    records = [_normalize_centerline(row) for row in rows]
    records = [row for row in records if row.get("geometry") and row.get("physical_id")]
    return {
        "dataset_id": DATASET_ID,
        "source": SOURCE_URL,
        "center": {"lat": lat, "lon": lon},
        "radius_m": radius_m,
        "count": len(records),
        "truncated": len(rows) >= limit,
        "complete": len(rows) < limit,
        "records": records,
    }


def _normalize_centerline(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "physical_id": _text(row.get("physicalid")),
        "left_blockface_id": _text(row.get("l_blockfaceid")),
        "right_blockface_id": _text(row.get("r_blockfaceid")),
        "left_address_range": _range(row.get("l_low_hn"), row.get("l_high_hn")),
        "right_address_range": _range(row.get("r_low_hn"), row.get("r_high_hn")),
        "full_street_name": _street_name(row),
        "street_name": _text(row.get("street_name")),
        "borough_code": _text(row.get("boroughcode")),
        "status": _text(row.get("status")),
        "accessible": _text(row.get("accessible")),
        "non_pedestrian": _text(row.get("nonped")),
        "segment_length": _number(row.get("segmentlength")),
        "number_park_lanes": _number(row.get("number_park_lanes")),
        "geometry": row.get("the_geom"),
        "source": SOURCE_URL,
        "geometry_provenance": {
            "dataset_id": DATASET_ID,
            "physical_id": _text(row.get("physicalid")),
            "geometry_type": (row.get("the_geom") or {}).get("type"),
            "representation": "road-bed centerline",
            "precision": "approximate street-side display; not a curb boundary",
        },
    }


def _street_name(row: dict[str, Any]) -> str | None:
    for key in ("stname_label", "full_street_name", "street_name"):
        value = _text(row.get(key))
        if value:
            return " ".join(value.split()).upper()
    return None


def _range(low: Any, high: Any) -> dict[str, str | None]:
    return {"low": _text(low), "high": _text(high)}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
