"""NYC DOT Parking Regulation Locations and Signs (nfid-uabd)."""

from __future__ import annotations

import math
import re
from typing import Any

from .coords import feet_for_meters, latlon_to_ny_state_plane
from .socrata import dataset_page_url, soda_get, soda_get_paginated

DATASET_ID = "nfid-uabd"
SOURCE_URL = dataset_page_url(DATASET_ID)

SIGN_FIELDS = (
    "order_number,record_type,order_type,borough,on_street,from_street,to_street,"
    "side_of_street,order_completed_on_date,sign_code,sign_description,sign_size,"
    "sign_design_voided_on_date,sign_location,distance_from_intersection,"
    "arrow_direction,facing_direction,sign_notes,sign_x_coord,sign_y_coord"
)


def fetch_signs_near(
    lat: float,
    lon: float,
    *,
    radius_m: float = 500,
    limit: int | None = None,
    record_type: str = "Current",
    paginate: bool = True,
) -> dict[str, Any]:
    """Fetch parking signs in a State Plane bbox around a point.

    When paginate=True, pages through SODA until complete or safety caps hit.
    `limit` is an optional hard bound (still reported as truncated if hit).
    """
    x, y = latlon_to_ny_state_plane(lat, lon)
    half = feet_for_meters(radius_m)
    where = (
        f"sign_x_coord between {x - half} and {x + half} "
        f"AND sign_y_coord between {y - half} and {y + half}"
    )
    if record_type:
        where += f" AND record_type='{record_type}'"

    params = {
        "$select": SIGN_FIELDS,
        "$where": where,
        "$order": "sign_x_coord,sign_y_coord,order_number,sign_code",
    }

    if paginate:
        page = soda_get_paginated(DATASET_ID, params, page_size=1000, max_rows=limit)
        rows = page["records"]
        truncated = bool(page.get("truncated"))
        complete = bool(page.get("complete"))
        pages = page.get("pages")
    else:
        fetch_limit = min(limit or 1000, 1000)
        rows = soda_get(DATASET_ID, {**params, "$limit": fetch_limit})
        truncated = len(rows) >= fetch_limit
        complete = not truncated
        pages = 1

    normalized = [_normalize_sign(r) for r in rows]
    normalized = _dedupe_signs(normalized)
    for sign in normalized:
        sx, sy = sign.get("sign_x_coord"), sign.get("sign_y_coord")
        if sx is None or sy is None:
            sign["distance_m_approx"] = None
            continue
        dist_ft = math.hypot(sx - x, sy - y)
        sign["distance_m_approx"] = round(dist_ft / (3937.0 / 1200.0), 1)
    normalized.sort(key=lambda s: (s.get("distance_m_approx") is None, s.get("distance_m_approx") or 1e12))

    return {
        "dataset_id": DATASET_ID,
        "source": SOURCE_URL,
        "center": {"lat": lat, "lon": lon, "x": x, "y": y},
        "radius_m": radius_m,
        "record_type": record_type,
        "count": len(normalized),
        "fetched": len(normalized),
        "truncated": truncated,
        "complete": complete and not truncated,
        "pages": pages,
        "records": normalized,
    }


def fetch_signs_near_point(
    lat: float,
    lon: float,
    *,
    radius_m: float = 80,
    record_type: str = "Current",
) -> dict[str, Any]:
    """Bounded per-location sign fetch for a meter/point candidate."""
    return fetch_signs_near(lat, lon, radius_m=radius_m, limit=None, record_type=record_type, paginate=True)


def fetch_signs_for_block(
    *,
    borough: str,
    on_street: str,
    from_street: str | None = None,
    to_street: str | None = None,
    side_of_street: str | None = None,
    record_type: str = "Current",
    limit: int | None = None,
) -> dict[str, Any]:
    """Fetch signs for a named dataset stretch (paginated)."""
    clauses = [f"borough='{_esc(borough)}'", f"on_street='{_esc(on_street)}'"]
    if from_street:
        clauses.append(f"from_street='{_esc(from_street)}'")
    if to_street:
        clauses.append(f"to_street='{_esc(to_street)}'")
    if side_of_street:
        clauses.append(f"side_of_street='{_esc(side_of_street)}'")
    if record_type:
        clauses.append(f"record_type='{_esc(record_type)}'")

    params = {
        "$select": SIGN_FIELDS,
        "$where": " AND ".join(clauses),
        "$order": "distance_from_intersection,order_number,sign_code",
    }
    page = soda_get_paginated(DATASET_ID, params, page_size=1000, max_rows=limit)
    records = _dedupe_signs([_normalize_sign(r) for r in page["records"]])
    return {
        "dataset_id": DATASET_ID,
        "source": SOURCE_URL,
        "query": {
            "borough": borough,
            "on_street": on_street,
            "from_street": from_street,
            "to_street": to_street,
            "side_of_street": side_of_street,
            "record_type": record_type,
        },
        "count": len(records),
        "truncated": bool(page.get("truncated")),
        "complete": bool(page.get("complete")) and not page.get("truncated"),
        "pages": page.get("pages"),
        "records": records,
    }


def normalize_street_name(name: str | None) -> str:
    """Collapse dataset quirks like 'WEST  116 STREET' vs 'WEST 116 STREET'."""
    if not name:
        return ""
    return re.sub(r"\s+", " ", name).strip().upper()


def block_key(
    borough: str | None,
    on_street: str | None,
    from_street: str | None,
    to_street: str | None,
    side_of_street: str | None,
) -> str:
    # Sort from/to so reversed endpoints still join across datasets.
    endpoints = sorted(
        [
            normalize_street_name(from_street),
            normalize_street_name(to_street),
        ]
    )
    return " | ".join(
        [
            normalize_street_name(borough),
            normalize_street_name(on_street),
            endpoints[0],
            endpoints[1],
            normalize_street_name(side_of_street),
        ]
    )


def _normalize_sign(row: dict) -> dict[str, Any]:
    borough = normalize_street_name(row.get("borough")) or None
    on_street = normalize_street_name(row.get("on_street")) or None
    from_street = normalize_street_name(row.get("from_street")) or None
    to_street = normalize_street_name(row.get("to_street")) or None
    side = normalize_street_name(row.get("side_of_street")) or None
    return {
        "order_number": row.get("order_number"),
        "record_type": row.get("record_type"),
        "order_type": row.get("order_type"),
        "borough": borough,
        "on_street": on_street,
        "from_street": from_street,
        "to_street": to_street,
        "side_of_street": side,
        "order_completed_on_date": row.get("order_completed_on_date"),
        "sign_code": row.get("sign_code"),
        "sign_description": row.get("sign_description"),
        "sign_design_voided_on_date": row.get("sign_design_voided_on_date"),
        "distance_from_intersection": _num(row.get("distance_from_intersection")),
        "arrow_direction": row.get("arrow_direction"),
        "facing_direction": row.get("facing_direction"),
        "sign_notes": row.get("sign_notes"),
        "sign_x_coord": _num(row.get("sign_x_coord")),
        "sign_y_coord": _num(row.get("sign_y_coord")),
        "source": SOURCE_URL,
        "block_key": block_key(borough, on_street, from_street, to_street, side),
    }


def _dedupe_signs(signs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out = []
    for s in signs:
        key = (
            s.get("order_number"),
            s.get("sign_code"),
            s.get("sign_description"),
            s.get("sign_x_coord"),
            s.get("sign_y_coord"),
            s.get("distance_from_intersection"),
            s.get("arrow_direction"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _esc(value: str) -> str:
    return value.replace("'", "''")


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
