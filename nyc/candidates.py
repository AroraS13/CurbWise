"""Build nearby street-side parking research candidates."""

from __future__ import annotations

import copy
import json
import time
from collections import OrderedDict, defaultdict
from threading import RLock
from typing import Any

from .closures import fetch_closures_near
from .centerlines import fetch_centerlines_near
from .coords import haversine_m
from .geocode import geocode_destination
from .locations import (
    associate_evidence_to_street_side,
    associate_signs_to_meter,
    meter_point_candidate,
    sign_stretch_candidate,
    street_side_candidates,
    stretch_span_label,
)
from .meters import fetch_meters_near
from .signs import block_key, fetch_signs_near, normalize_street_name

_DISCOVERY_CACHE_TTL_SECONDS = 300.0
_DISCOVERY_CACHE_MAX_ENTRIES = 24
_DISCOVERY_CACHE: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_DISCOVERY_CACHE_LOCK = RLock()


def clear_candidate_cache() -> None:
    with _DISCOVERY_CACHE_LOCK:
        _DISCOVERY_CACHE.clear()


def find_candidates_near(
    destination: str | dict[str, Any],
    *,
    radius_m: float = 500,
    sign_limit: int | None = None,
    meter_limit: int = 250,
    closure_limit: int = 100,
    max_candidates: int = 20,
    prefer_meter_points: bool = False,
) -> dict[str, Any]:
    """Geocode and gather candidates.

    Primary candidates are approximate left/right sides of CSCL road-bed centerlines.
    Signs and meters are evidence attached to those sides; meters never create the
    primary candidate list. Legacy meter/stretch rows remain available as fallback
    diagnostics if centerline retrieval fails.
    """
    cache_key = json.dumps(
        {
            "destination": destination,
            "radius_m": radius_m,
            "sign_limit": sign_limit,
            "meter_limit": meter_limit,
            "closure_limit": closure_limit,
            "max_candidates": max_candidates,
            "prefer_meter_points": prefer_meter_points,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    with _DISCOVERY_CACHE_LOCK:
        cached = _DISCOVERY_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] <= _DISCOVERY_CACHE_TTL_SECONDS:
            _DISCOVERY_CACHE.move_to_end(cache_key)
            return copy.deepcopy(cached[1])
        if cached:
            _DISCOVERY_CACHE.pop(cache_key, None)

    if isinstance(destination, str):
        geo = geocode_destination(destination)
        if not geo["found"]:
            return {
                "found": False,
                "error": "Could not geocode destination.",
                "geocode": geo,
                "candidates": [],
            }
        place = geo["best"]
    else:
        geo = {"query": destination.get("label"), "found": True, "best": destination, "source": "provided"}
        place = destination

    lat, lon = place["lat"], place["lon"]
    centerlines_error = None
    try:
        centerlines = fetch_centerlines_near(
            lat,
            lon,
            radius_m=radius_m,
            limit=max(200, max_candidates * 20),
        )
    except Exception as exc:
        centerlines_error = f"{type(exc).__name__}: {exc}"
        centerlines = {
            "records": [],
            "count": 0,
            "truncated": False,
            "complete": False,
            "source": "https://data.cityofnewyork.us/d/inkn-q76z",
        }
    signs = fetch_signs_near(lat, lon, radius_m=radius_m, limit=sign_limit, paginate=True)
    meters = fetch_meters_near(lat, lon, radius_m=radius_m, limit=meter_limit)
    closures = fetch_closures_near(lat, lon, radius_m=radius_m, limit=closure_limit)

    street_side_cands = []
    for segment in centerlines["records"]:
        for cand in street_side_candidates(segment, dest_lat=lat, dest_lon=lon):
            cand = associate_evidence_to_street_side(
                cand,
                signs=signs["records"],
                meters=meters["records"],
            )
            cand["retrieval"] = {
                **(cand.get("retrieval") or {}),
                "centerlines_complete": centerlines.get("complete"),
                "signs_truncated": signs.get("truncated", False),
                "signs_complete": signs.get("complete"),
                "signs_fetched": signs.get("count"),
                "signs_matched": len(cand.get("signs") or []),
                "signs_unmatched_nearby": len(cand.get("nearby_signs_unmatched") or []),
                "meters_truncated": meters.get("truncated", False),
                "meters_complete": not meters.get("truncated", False),
            }
            if not cand.get("meters") and not meters.get("truncated"):
                cand["metering_classification"] = "UNMETERED"
            on = normalize_street_name(cand.get("on_street"))
            cand["closures"] = [
                c for c in closures["records"] if normalize_street_name(c.get("on_street")) == on
            ]
            street_side_cands.append(cand)
    street_side_cands.sort(
        key=lambda c: (c.get("distance_m") is None, c.get("distance_m") or 1e9)
    )

    # Legacy meter candidates are retained only for centerline-outage fallback and diagnostics.
    meter_cands = []
    for meter in meters["records"]:
        if meter.get("lat") is None or meter.get("lon") is None:
            continue
        cand = meter_point_candidate(meter, dest_lat=lat, dest_lon=lon)
        cand = associate_signs_to_meter(cand, signs["records"], max_distance_m=60.0)
        # Attach closures by street name as advisories only.
        on = normalize_street_name(cand.get("on_street"))
        cand["closures"] = [
            c for c in closures["records"] if normalize_street_name(c.get("on_street")) == on
        ]
        meter_cands.append(cand)

    meter_cands.sort(key=lambda c: (c.get("distance_m") is None, c.get("distance_m") or 1e9))

    # Secondary: dataset stretches for context / diagnostics (not fabricated blocks).
    by_stretch: dict[str, list[dict]] = defaultdict(list)
    stretch_seed: dict[str, dict] = {}
    for sign in signs["records"]:
        key = sign["block_key"]
        by_stretch[key].append(sign)
        stretch_seed.setdefault(key, sign)

    stretch_cands = []
    for key, stretch_signs in by_stretch.items():
        seed = stretch_seed[key]
        stretch_cands.append(sign_stretch_candidate(seed, stretch_signs))
    stretch_cands.sort(key=lambda c: (c.get("distance_m") is None, c.get("distance_m") or 1e9))

    if street_side_cands:
        candidates = street_side_cands[:max_candidates]
    elif prefer_meter_points or meter_cands:
        candidates = meter_cands[:max_candidates]
    else:
        candidates = stretch_cands[:max_candidates]

    overlapping = _find_overlapping_stretches(stretch_cands[:30])

    result = {
        "found": True,
        "destination": place,
        "geocode": geo,
        "radius_m": radius_m,
        "sources": {
            "centerlines": centerlines["source"],
            "signs": signs["source"],
            "meters": meters["source"],
            "closures": closures["source"],
        },
        "counts": {
            "signs": signs["count"],
            "meters": meters["count"],
            "closures": closures["count"],
            "centerline_segments": centerlines["count"],
            "street_side_candidates": len(street_side_cands),
            "meter_point_candidates": len(meter_cands),
            "sign_stretch_candidates": len(stretch_cands),
            "candidates": len(candidates),
        },
        "truncated": {
            "centerlines": centerlines.get("truncated", False),
            "signs": signs.get("truncated", False),
            "signs_complete": signs.get("complete"),
            "meters": meters.get("truncated", False),
            "closures": False,
        },
        "candidates": candidates,
        "all_street_sides": street_side_cands,
        "all_meter_points": meter_cands,
        "all_sign_stretches": stretch_cands,
        "overlapping_stretches": overlapping,
        "limitations": [
            "CSCL geometry is a road-bed centerline; displayed street sides are approximate, not curb boundaries.",
            "Centerline and same-side matching do not establish exact regulatory curb-zone scope.",
            "Missing records do not imply permission to park.",
            "Sign associations to meters require same street and side (STREET_SIDE_MATCHED); "
            "that is not SCOPE_VERIFIED curb coverage. Unmatched nearby signs are diagnostic only.",
            "Construction records are possible restrictions, not proof of an active closure.",
            "Physical signs and temporary postings take precedence over dataset records.",
            *(
                [f"Centerline retrieval failed; legacy fallback candidates used: {centerlines_error}"]
                if centerlines_error
                else []
            ),
        ],
    }
    with _DISCOVERY_CACHE_LOCK:
        _DISCOVERY_CACHE[cache_key] = (time.monotonic(), copy.deepcopy(result))
        _DISCOVERY_CACHE.move_to_end(cache_key)
        while len(_DISCOVERY_CACHE) > _DISCOVERY_CACHE_MAX_ENTRIES:
            _DISCOVERY_CACHE.popitem(last=False)
    return result


def summarize_candidate_blocks(candidates: list[dict[str, Any]], *, top_n: int = 10) -> list[dict[str, Any]]:
    out = []
    for c in candidates[:top_n]:
        out.append(
            {
                "candidate_id": c.get("candidate_id") or c.get("block_key"),
                "candidate_kind": c.get("candidate_kind"),
                "block_key": c.get("block_key"),
                "physical_id": c.get("physical_id"),
                "blockface_id": c.get("blockface_id"),
                "distance_m": c.get("distance_m"),
                "lat": c.get("lat") or c.get("approx_lat"),
                "lon": c.get("lon") or c.get("approx_lon"),
                "meter_number": c.get("meter_number"),
                "on_street": c.get("on_street"),
                "side_of_street": c.get("side_of_street"),
                "metering_classification": c.get("metering_classification"),
                "sign_count": len(c.get("signs") or []),
                "meter_count": len(c.get("meters") or []),
                "closure_count": len(c.get("closures") or []),
                "stretch": c.get("stretch"),
                "sample_signs": [s.get("sign_description") for s in (c.get("signs") or [])[:3]],
                "sample_meters": [m.get("meter_hours") for m in (c.get("meters") or [])[:2]],
            }
        )
    return out


def group_signs_by_block(signs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sign in signs:
        grouped[
            sign.get("block_key")
            or block_key(
                sign.get("borough"),
                sign.get("on_street"),
                sign.get("from_street"),
                sign.get("to_street"),
                sign.get("side_of_street"),
            )
        ].append(sign)
    return dict(grouped)


def _find_overlapping_stretches(stretches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from .locations import overlapping_stretch_keys

    out = []
    for i, a in enumerate(stretches):
        for b in stretches[i + 1 :]:
            if (a.get("on_street") or "").upper() != (b.get("on_street") or "").upper():
                continue
            if (a.get("side_of_street") or "").upper() != (b.get("side_of_street") or "").upper():
                continue
            if overlapping_stretch_keys(a.get("from_street"), a.get("to_street"), b.get("from_street"), b.get("to_street")):
                out.append(
                    {
                        "a": a.get("block_key"),
                        "b": b.get("block_key"),
                        "note": "Overlapping dataset stretches are not merged into one physical block.",
                    }
                )
    return out


def _norm_street(name: str | None) -> str:
    return normalize_street_name(name)
