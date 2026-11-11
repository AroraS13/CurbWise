"""Candidate location models: meter points vs dataset street stretches.

Dataset from/to ranges are often multi-block stretches, not single physical blocks.
We do not invent intersection geometry. Meter coordinates are preferred actionable anchors.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .coords import haversine_m, latlon_to_ny_state_plane
from .signs import normalize_street_name

_STREET_NUM = re.compile(
    r"\b(?:WEST|EAST|NORTH|SOUTH)?\s*(\d+)\s*(?:STREET|ST|AVENUE|AVE|ROAD|RD|BOULEVARD|BLVD)?\b",
    re.I,
)

_STREET_TOKEN_REPLACEMENTS = {
    "W": "WEST",
    "E": "EAST",
    "N": "NORTH",
    "S": "SOUTH",
    "ST": "STREET",
    "AVE": "AVENUE",
    "AV": "AVENUE",
    "RD": "ROAD",
    "BLVD": "BOULEVARD",
    "PL": "PLACE",
    "PKWY": "PARKWAY",
}


def canonical_street_name(name: str | None) -> str:
    """Normalize common CSCL/sign/meter street-name abbreviations for joins."""
    tokens = normalize_street_name(name).replace(".", "").split()
    return " ".join(_STREET_TOKEN_REPLACEMENTS.get(token, token) for token in tokens)


def street_side_candidates(
    segment: dict[str, Any],
    *,
    dest_lat: float,
    dest_lon: float,
) -> list[dict[str, Any]]:
    """Create left/right approximate street-side candidates from one centerline."""
    lines = _geometry_lines(segment.get("geometry"))
    if not lines:
        return []
    anchor_lat, anchor_lon, distance_m = _nearest_point(lines, dest_lat, dest_lon)
    direction = _overall_direction(lines, anchor_lat)
    if direction is None:
        return []
    dx, dy = direction
    left_normal = (-dy, dx)
    left_side = _normal_to_cardinal(*left_normal)
    right_side = _normal_to_cardinal(-left_normal[0], -left_normal[1])
    rows = []
    for lr, side, normal in (
        ("L", left_side, left_normal),
        ("R", right_side, (-left_normal[0], -left_normal[1])),
    ):
        blockface_id = segment.get("left_blockface_id" if lr == "L" else "right_blockface_id")
        address_range = segment.get("left_address_range" if lr == "L" else "right_address_range")
        physical_id = str(segment.get("physical_id") or "")
        candidate_id = f"street-side:{physical_id}:{lr}"
        rows.append(
            {
                "candidate_id": candidate_id,
                "candidate_kind": "STREET_SIDE",
                "block_key": candidate_id,
                "physical_id": physical_id,
                "blockface_id": blockface_id,
                "centerline_side": lr,
                "side_of_street": side,
                "side_derivation": {
                    "method": "centerline_digitized_direction_normal",
                    "centerline_side": lr,
                    "display_side": side,
                    "certainty": "approximate",
                },
                "borough": segment.get("borough_code"),
                "on_street": segment.get("full_street_name"),
                "from_street": None,
                "to_street": None,
                "address_range": address_range,
                "lat": anchor_lat,
                "lon": anchor_lon,
                "approx_lat": anchor_lat,
                "approx_lon": anchor_lon,
                "distance_m": round(distance_m, 1),
                "geometry": segment.get("geometry"),
                "display_geometry": _offset_geometry(lines, normal, 5.0, anchor_lat),
                "geometry_provenance": segment.get("geometry_provenance"),
                "geometry_is_approximate": True,
                "number_park_lanes": segment.get("number_park_lanes"),
                "signs": [],
                "meters": [],
                "closures": [],
                "nearby_signs_unmatched": [],
                "retrieval": {
                    "centerlines_complete": True,
                    "signs_truncated": False,
                    "signs_complete": None,
                    "meters_truncated": False,
                    "meters_complete": None,
                },
                "evidence_summary": {
                    "sign_count": 0,
                    "meter_count": 0,
                    "has_coordinates": True,
                    "classification_ready": True,
                },
            }
        )
    return rows


def associate_evidence_to_street_side(
    candidate: dict[str, Any],
    *,
    signs: list[dict[str, Any]],
    meters: list[dict[str, Any]],
    max_distance_m: float = 40.0,
) -> dict[str, Any]:
    """Attach same-street/same-side records to a centerline-side candidate."""
    lines = _geometry_lines(candidate.get("geometry"))
    street = canonical_street_name(candidate.get("on_street"))
    side = normalize_side(candidate.get("side_of_street"))
    matched_signs: list[dict[str, Any]] = []
    unmatched_signs: list[dict[str, Any]] = []
    matched_meters: list[dict[str, Any]] = []

    for sign in signs:
        sx, sy = sign.get("sign_x_coord"), sign.get("sign_y_coord")
        distance = _state_plane_distance_to_lines(lines, sx, sy)
        if distance is None or distance > max_distance_m:
            continue
        item = dict(sign)
        item["association_distance_m"] = round(distance, 1)
        street_ok = street == canonical_street_name(sign.get("on_street"))
        side_ok = side == normalize_side(sign.get("side_of_street"))
        item["street_side_matched"] = bool(street_ok and side_ok)
        item["scope_verified"] = False
        if street_ok and side_ok:
            item["association"] = "centerline_street_side_matched"
            item["street_side_match_reason"] = "street_side_matched"
            matched_signs.append(item)
        else:
            item["association"] = "nearby_unmatched"
            item["unmatched_reason"] = "street_mismatch" if not street_ok else "side_mismatch"
            unmatched_signs.append(item)

    for meter in meters:
        mx, my = meter.get("x"), meter.get("y")
        distance = _state_plane_distance_to_lines(lines, mx, my)
        if distance is None or distance > max_distance_m:
            continue
        if street != canonical_street_name(meter.get("on_street")):
            continue
        if side != normalize_side(meter.get("side_of_street")):
            continue
        item = dict(meter)
        item["association_distance_m"] = round(distance, 1)
        item["association"] = "centerline_street_side_matched"
        matched_meters.append(item)

    matched_signs.sort(key=lambda row: row.get("association_distance_m") or 1e9)
    unmatched_signs.sort(key=lambda row: row.get("association_distance_m") or 1e9)
    matched_meters.sort(key=lambda row: row.get("association_distance_m") or 1e9)
    out = dict(candidate)
    out["signs"] = matched_signs
    out["meters"] = matched_meters
    out["nearby_signs_unmatched"] = unmatched_signs[:40]
    out["metering_classification"] = (
        "METERED"
        if matched_meters
        else "UNMETERED" if signs and meters is not None else "UNKNOWN"
    )
    out["metering_basis"] = (
        "Associated NYC meter record(s)."
        if matched_meters
        else "No meter record matched this street side; this does not imply free or legal parking."
    )
    out["evidence_summary"] = {
        "sign_count": len(matched_signs),
        "meter_count": len(matched_meters),
        "unmatched_nearby_sign_count": len(unmatched_signs),
        "has_coordinates": True,
        "classification_ready": True,
    }
    if matched_signs:
        first = matched_signs[0]
        out["from_street"] = first.get("from_street")
        out["to_street"] = first.get("to_street")
    return out


def stretch_span_label(from_street: str | None, to_street: str | None) -> dict[str, Any]:
    """Describe whether a dataset from/to looks like a multi-block stretch."""
    fr = normalize_street_name(from_street)
    to = normalize_street_name(to_street)
    n1, n2 = _street_number(fr), _street_number(to)
    multi = False
    approx_blocks = None
    if n1 is not None and n2 is not None and n1 != n2:
        approx_blocks = abs(n2 - n1)
        multi = approx_blocks > 1
    return {
        "from_street": fr or None,
        "to_street": to or None,
        "likely_multi_block_stretch": multi,
        "approx_numbered_span": approx_blocks,
        "note": (
            "Dataset from/to identifies a recorded street stretch, not a verified "
            "intersection-to-intersection physical block."
            if multi
            else "Dataset from/to stretch; physical block boundaries are not fabricated."
        ),
    }


def meter_point_candidate(meter: dict[str, Any], *, dest_lat: float, dest_lon: float) -> dict[str, Any]:
    """Build an actionable meter-point candidate with real coordinates."""
    lat, lon = meter.get("lat"), meter.get("lon")
    dist = None
    if lat is not None and lon is not None:
        dist = round(haversine_m(dest_lat, dest_lon, lat, lon), 1)
    span = stretch_span_label(meter.get("from_street"), meter.get("to_street"))
    meter_id = str(meter.get("meter_number") or meter.get("objectid") or "")
    return {
        "candidate_id": f"meter:{meter_id}",
        "candidate_kind": "METER_POINT",
        "meter_number": meter.get("meter_number"),
        "objectid": meter.get("objectid"),
        "status": meter.get("status"),
        "meter_hours": meter.get("meter_hours"),
        "pay_by_cell_number": meter.get("pay_by_cell_number"),
        "borough": meter.get("borough"),
        "on_street": meter.get("on_street"),
        "from_street": meter.get("from_street"),
        "to_street": meter.get("to_street"),
        "side_of_street": meter.get("side_of_street"),
        "approx_lat": lat,
        "approx_lon": lon,
        "lat": lat,
        "lon": lon,
        "x": meter.get("x"),
        "y": meter.get("y"),
        "distance_m": dist,
        "block_key": meter.get("block_key"),
        "stretch": span,
        "signs": [],
        "meters": [meter],
        "closures": [],
        "nearby_signs_association": "none",
        "retrieval": {
            "signs_truncated": False,
            "signs_complete": None,
            "meters_truncated": False,
            "meters_complete": True,
        },
        "evidence_summary": {
            "sign_count": 0,
            "meter_count": 1,
            "has_coordinates": lat is not None and lon is not None,
            "classification_ready": True,
        },
    }


def sign_stretch_candidate(seed: dict[str, Any], signs: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep a dataset stretch as secondary context (not a fabricated physical block)."""
    span = stretch_span_label(seed.get("from_street"), seed.get("to_street"))
    dists = [s.get("distance_m_approx") for s in signs if s.get("distance_m_approx") is not None]
    return {
        "candidate_id": f"stretch:{seed.get('block_key')}",
        "candidate_kind": "SIGN_STRETCH",
        "block_key": seed.get("block_key"),
        "borough": seed.get("borough"),
        "on_street": seed.get("on_street"),
        "from_street": seed.get("from_street"),
        "to_street": seed.get("to_street"),
        "side_of_street": seed.get("side_of_street"),
        "approx_lat": None,
        "approx_lon": None,
        "distance_m": min(dists) if dists else None,
        "stretch": span,
        "signs": signs,
        "meters": [],
        "closures": [],
        "retrieval": {
            "signs_truncated": False,
            "signs_complete": None,
            "meters_truncated": False,
            "meters_complete": None,
        },
        "evidence_summary": {
            "sign_count": len(signs),
            "meter_count": 0,
            "has_coordinates": False,
            "classification_ready": bool(signs),
        },
    }


def normalize_side(side: str | None) -> str | None:
    """Normalize curb side to a single letter when possible (E/W/N/S)."""
    raw = normalize_street_name(side)
    if not raw:
        return None
    aliases = {
        "E": "E",
        "EAST": "E",
        "W": "W",
        "WEST": "W",
        "N": "N",
        "NORTH": "N",
        "S": "S",
        "SOUTH": "S",
    }
    if raw in aliases:
        return aliases[raw]
    if len(raw) == 1 and raw in {"E", "W", "N", "S"}:
        return raw
    return raw


def street_side_match(
    meter_or_cand: dict[str, Any],
    sign: dict[str, Any],
) -> tuple[bool, str]:
    """Geographic consistency only — not regulatory scope verification.

    Returns (matched, reason). Same street + side => STREET_SIDE_MATCHED.
    Missing street/side or mismatch => not matched (diagnostic context only).
    """
    m_street = normalize_street_name(meter_or_cand.get("on_street"))
    s_street = normalize_street_name(sign.get("on_street"))
    if not m_street or not s_street:
        return False, "missing_street"
    if m_street != s_street:
        return False, "street_mismatch"
    m_side = normalize_side(meter_or_cand.get("side_of_street"))
    s_side = normalize_side(sign.get("side_of_street"))
    if not m_side or not s_side:
        return False, "missing_side"
    if m_side != s_side:
        return False, "side_mismatch"
    return True, "street_side_matched"


def association_distance_m(meter_cand: dict[str, Any], sign: dict[str, Any]) -> float | None:
    """Distance between meter and sign in meters (State Plane preferred)."""
    mx, my = meter_cand.get("x"), meter_cand.get("y")
    sx, sy = sign.get("sign_x_coord"), sign.get("sign_y_coord")
    if mx is not None and my is not None and sx is not None and sy is not None:
        return math.hypot(float(sx) - float(mx), float(sy) - float(my)) / (3937.0 / 1200.0)
    return None


def associate_signs_to_meter(
    meter_cand: dict[str, Any],
    signs: list[dict[str, Any]],
    *,
    max_distance_m: float = 60.0,
    max_signs: int = 40,
) -> dict[str, Any]:
    """Attach nearby same-street/side signs; keep mismatches as diagnostic context.

    STREET_SIDE_MATCHED means geographic consistency only. It does not mean
    SCOPE_VERIFIED — arrowed/partial plates still have unverified curb extent.
    Unmatched nearby signs are retained for diagnostics and are not applied as
    restrictions.
    """
    matched: list[tuple[float, dict]] = []
    unmatched: list[tuple[float, dict]] = []
    for sign in signs:
        d = association_distance_m(meter_cand, sign)
        if d is None or d > max_distance_m:
            continue
        item = dict(sign)
        item["association_distance_m"] = round(d, 1)
        ok, reason = street_side_match(meter_cand, sign)
        item["street_side_matched"] = ok
        item["street_side_match_reason"] = reason
        # Same street/side is never by itself scope verification.
        item["scope_verified"] = False
        if ok:
            item["association"] = "street_side_matched"
            matched.append((d, item))
        else:
            item["association"] = "nearby_unmatched"
            item["unmatched_reason"] = reason
            unmatched.append((d, item))

    matched.sort(key=lambda t: t[0])
    unmatched.sort(key=lambda t: t[0])
    attached = [s for _, s in matched[:max_signs]]
    nearby_unmatched = [s for _, s in unmatched[:max_signs]]

    out = dict(meter_cand)
    out["signs"] = attached
    out["nearby_signs_unmatched"] = nearby_unmatched
    out["nearby_signs_association"] = "street_side_matched" if attached else "none"
    if meter_cand.get("retrieval") is not None:
        out["retrieval"] = dict(meter_cand.get("retrieval") or {})
    out["evidence_summary"] = {
        **(out.get("evidence_summary") or {}),
        "sign_count": len(attached),
        "unmatched_nearby_sign_count": len(nearby_unmatched),
        "meter_count": len(out.get("meters") or []),
        "has_coordinates": out.get("lat") is not None,
        "classification_ready": True,
        "note": (
            "Attached signs share street and side with the meter (STREET_SIDE_MATCHED). "
            "That is not SCOPE_VERIFIED curb coverage."
        ),
    }
    return out


def overlapping_stretch_keys(a_from: str | None, a_to: str | None, b_from: str | None, b_to: str | None) -> bool:
    """True when two numbered street spans appear to overlap (for tests / diagnostics)."""
    a1, a2 = _street_number(a_from), _street_number(a_to)
    b1, b2 = _street_number(b_from), _street_number(b_to)
    if None in (a1, a2, b1, b2):
        return False
    lo_a, hi_a = sorted((a1, a2))
    lo_b, hi_b = sorted((b1, b2))
    return lo_a < hi_b and lo_b < hi_a and (lo_a, hi_a) != (lo_b, hi_b)


def _street_number(name: str | None) -> int | None:
    if not name:
        return None
    m = _STREET_NUM.search(name)
    if not m:
        return None
    return int(m.group(1))


def _geometry_lines(geometry: dict[str, Any] | None) -> list[list[list[float]]]:
    if not geometry:
        return []
    coords = geometry.get("coordinates") or []
    if geometry.get("type") == "LineString":
        return [coords] if len(coords) >= 2 else []
    if geometry.get("type") == "MultiLineString":
        return [line for line in coords if len(line) >= 2]
    return []


def _overall_direction(
    lines: list[list[list[float]]],
    reference_lat: float,
) -> tuple[float, float] | None:
    first = lines[0][0]
    last = lines[-1][-1]
    scale_x = 111_320.0 * math.cos(math.radians(reference_lat))
    dx = (float(last[0]) - float(first[0])) * scale_x
    dy = (float(last[1]) - float(first[1])) * 111_320.0
    length = math.hypot(dx, dy)
    if length < 0.1:
        return None
    return dx / length, dy / length


def _normal_to_cardinal(nx: float, ny: float) -> str:
    if abs(nx) >= abs(ny):
        return "E" if nx >= 0 else "W"
    return "N" if ny >= 0 else "S"


def _nearest_point(
    lines: list[list[list[float]]],
    lat: float,
    lon: float,
) -> tuple[float, float, float]:
    scale_x = 111_320.0 * math.cos(math.radians(lat))
    scale_y = 111_320.0
    best = (lat, lon, float("inf"))
    for line in lines:
        for a, b in zip(line, line[1:]):
            ax, ay = (float(a[0]) - lon) * scale_x, (float(a[1]) - lat) * scale_y
            bx, by = (float(b[0]) - lon) * scale_x, (float(b[1]) - lat) * scale_y
            vx, vy = bx - ax, by - ay
            denom = vx * vx + vy * vy
            t = 0.0 if denom == 0 else max(0.0, min(1.0, -(ax * vx + ay * vy) / denom))
            px, py = ax + t * vx, ay + t * vy
            distance = math.hypot(px, py)
            if distance < best[2]:
                best = (lat + py / scale_y, lon + px / scale_x, distance)
    return best


def _offset_geometry(
    lines: list[list[list[float]]],
    normal: tuple[float, float],
    offset_m: float,
    reference_lat: float,
) -> dict[str, Any]:
    dx, dy = normal[0] * offset_m, normal[1] * offset_m
    lon_delta = dx / (111_320.0 * max(0.2, math.cos(math.radians(reference_lat))))
    lat_delta = dy / 111_320.0
    shifted = [
        [[float(point[0]) + lon_delta, float(point[1]) + lat_delta] for point in line]
        for line in lines
    ]
    return {"type": "MultiLineString", "coordinates": shifted}


def _state_plane_distance_to_lines(
    lines: list[list[list[float]]],
    x: Any,
    y: Any,
) -> float | None:
    if x is None or y is None or not lines:
        return None
    px, py = float(x), float(y)
    best_ft = float("inf")
    for line in lines:
        projected = [
            latlon_to_ny_state_plane(float(point[1]), float(point[0])) for point in line
        ]
        for (ax, ay), (bx, by) in zip(projected, projected[1:]):
            vx, vy = bx - ax, by - ay
            denom = vx * vx + vy * vy
            t = 0.0 if denom == 0 else max(
                0.0,
                min(1.0, ((px - ax) * vx + (py - ay) * vy) / denom),
            )
            qx, qy = ax + t * vx, ay + t * vy
            best_ft = min(best_ft, math.hypot(px - qx, py - qy))
    return best_ft / (3937.0 / 1200.0)
