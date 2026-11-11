"""NYC DOT Parking Meters Locations and Status (693u-uax6)."""

from __future__ import annotations

from typing import Any

from .socrata import dataset_page_url, soda_get
from .signs import block_key, normalize_street_name

DATASET_ID = "693u-uax6"
SOURCE_URL = dataset_page_url(DATASET_ID)

METER_FIELDS = (
    "objectid,meter_number,status,pay_by_cell_number,meter_hours,parking_facility_name,"
    "facility,borough,on_street,side_of_street,from_street,to_street,lat,long,x,y,location"
)


def fetch_meters_near(lat: float, lon: float, *, radius_m: float = 400, limit: int = 200) -> dict[str, Any]:
    """Fetch meters within radius_m of a WGS84 point using SoQL within_circle."""
    where = f"within_circle(location, {lat}, {lon}, {radius_m})"
    rows = soda_get(
        DATASET_ID,
        {
            "$select": METER_FIELDS,
            "$where": where,
            "$limit": limit,
            "$order": "on_street,side_of_street",
        },
    )
    return {
        "dataset_id": DATASET_ID,
        "source": SOURCE_URL,
        "center": {"lat": lat, "lon": lon},
        "radius_m": radius_m,
        "count": len(rows),
        "truncated": len(rows) >= limit,
        "records": [_normalize_meter(r) for r in rows],
    }


def _normalize_meter(row: dict) -> dict[str, Any]:
    lat = _num(row.get("lat"))
    lon = _num(row.get("long"))
    borough = normalize_street_name(row.get("borough")) or None
    on_street = normalize_street_name(row.get("on_street")) or None
    from_street = normalize_street_name(row.get("from_street")) or None
    to_street = normalize_street_name(row.get("to_street")) or None
    side = normalize_street_name(row.get("side_of_street")) or None
    return {
        "objectid": row.get("objectid"),
        "meter_number": row.get("meter_number"),
        "status": row.get("status"),
        "pay_by_cell_number": row.get("pay_by_cell_number"),
        "meter_hours": row.get("meter_hours"),
        "parking_facility_name": row.get("parking_facility_name"),
        "facility": row.get("facility"),
        "borough": borough,
        "on_street": on_street,
        "side_of_street": side,
        "from_street": from_street,
        "to_street": to_street,
        "lat": lat,
        "lon": lon,
        "x": _num(row.get("x")),
        "y": _num(row.get("y")),
        "source": SOURCE_URL,
        "block_key": block_key(borough, on_street, from_street, to_street, side),
    }


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
