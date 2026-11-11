"""Deterministic structured results for the CurbWise map UI (not Writer markdown)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .leads import LeadStatus
from .parse_regs import parse_meter_schedule_record
from .parking_rules import meter_schedule_covers_interval


_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_RULE_NAMES = {
    "ASP_STREET_CLEANING": "Street cleaning",
    "NO_PARKING": "No parking",
    "NO_STANDING": "No standing",
    "NO_STOPPING": "No stopping",
}
_RESTRICTION_PRIORITY = {
    "NO_STOPPING": 0,
    "NO_STANDING": 1,
    "NO_PARKING": 2,
    "ASP_STREET_CLEANING": 3,
}


def _clock(minute: int) -> str:
    if minute == 24 * 60:
        minute = 0
    hour, mins = divmod(minute, 60)
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}{f':{mins:02d}' if mins else ''} {suffix}"


def _days(days: list[int]) -> str:
    values = tuple(sorted(set(days)))
    if values == tuple(range(7)):
        return "daily"
    if values == tuple(range(5)):
        return "Monday–Friday"
    if values == tuple(range(6)):
        return "Monday–Saturday"
    names = [_DAY_NAMES[d] for d in values if 0 <= d < 7]
    if len(names) < 2:
        return names[0] if names else "days not established"
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _window_text(window: dict[str, Any]) -> str:
    days = _days(window.get("days") or [])
    if window.get("anytime"):
        return f"{days}, all day"
    return (
        f"{days}, {_clock(int(window.get('start_minute') or 0))}"
        f"–{_clock(int(window.get('end_minute') or 0))}"
    )


def _rule_key(finding: dict[str, Any]) -> tuple[Any, ...]:
    windows = tuple(
        (
            tuple(window.get("days") or []),
            window.get("start_minute"),
            window.get("end_minute"),
            bool(window.get("anytime")),
        )
        for window in finding.get("windows") or []
    )
    return finding.get("kind"), windows


def _plain_rule_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate parsed sign rules and create deterministic user-facing text."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for finding in findings:
        kind = finding.get("kind")
        if (
            kind not in _RULE_NAMES
            or finding.get("parse_status") != "PARSED"
            or not finding.get("windows")
        ):
            continue
        key = _rule_key(finding)
        if key in seen:
            continue
        seen.add(key)
        schedule = "; ".join(_window_text(window) for window in finding["windows"])
        overlaps = bool(finding.get("overlaps_stay"))
        scope_verified = bool(finding.get("scope_verified"))
        ends = (
            finding.get("known_restriction_ends")
            if not any(window.get("anytime") for window in finding["windows"])
            else None
        )
        consequence = (
            "This known restriction overlaps your requested parking time."
            if overlaps
            else "Your requested stay does not overlap this known restriction."
        )
        if overlaps and ends:
            consequence += f" The observed restriction window ends at {_clock(datetime.fromisoformat(ends).hour * 60 + datetime.fromisoformat(ends).minute)}."
        if not scope_verified:
            consequence += " Its precise curb extent is unverified."
        rows.append(
            {
                "kind": kind,
                "label": _RULE_NAMES[kind],
                "schedule": schedule,
                "summary": f"{_RULE_NAMES[kind]}: {schedule}.",
                "consequence": consequence,
                "overlaps_stay": overlaps,
                "restriction_ends": ends,
                "source_id": finding.get("source_id"),
                "source_url": finding.get("source_url"),
                "raw_description": finding.get("raw_description"),
                "precise_curb_extent_verified": scope_verified,
            }
        )
    rows.sort(
        key=lambda row: (
            not row["overlaps_stay"],
            _RESTRICTION_PRIORITY.get(row["kind"], 9),
            row["summary"],
        )
    )
    return rows


def _meter_user_summary(
    meters: list[dict[str, Any]],
    meter_regs: list[Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    if not meters:
        return {
            "classification": "UNMETERED",
            "has_passenger_schedule": False,
            "supports_requested_stay": False,
            "summary": "No associated NYC meter record.",
            "consequence": "Meter payment information is not available for this street side.",
        }

    vehicle = request.get("vehicle_category") or "passenger"
    active = [
        reg
        for reg in meter_regs
        if ((reg.metadata or {}).get("meter_status") or "").lower() in ("", "active")
    ]
    requested = [
        reg
        for reg in active
        if reg.parse_status.value == "PARSED"
        and reg.kind.value == "METERED"
        and reg.vehicle_category == vehicle
    ]
    windows = []
    for reg in requested:
        for window in reg.to_dict().get("windows") or []:
            text = _window_text(window)
            if text not in windows:
                windows.append(text)
    maxima = sorted(
        {reg.max_duration_minutes for reg in requested if reg.max_duration_minutes}
    )
    schedule = "; ".join(windows)
    max_text = (
        f"{maxima[0] // 60}-hour maximum"
        if maxima and maxima[0] % 60 == 0
        else f"{maxima[0]}-minute maximum"
        if maxima
        else "maximum duration not established"
    )
    start_raw, end_raw = request.get("arrival"), request.get("departure")
    supports = False
    duration_ok = False
    if requested and start_raw and end_raw:
        start, end = datetime.fromisoformat(start_raw), datetime.fromisoformat(end_raw)
        supports = meter_schedule_covers_interval(
            meter_regs, start, end, vehicle_category=vehicle
        )
        requested_minutes = int((end - start).total_seconds() // 60)
        duration_ok = not maxima or requested_minutes <= maxima[0]
        supports = supports and duration_ok

    commercial_overlap = any(
        reg.vehicle_category == "commercial"
        and reg.parse_status.value == "PARSED"
        for reg in active
    )
    unparsed = any(reg.parse_status.value == "UNPARSED" for reg in active)
    if requested:
        summary = f"Active {vehicle} meter: {max_text}"
        if schedule:
            summary += f", {schedule}"
        summary += "."
        consequence = (
            "The meter's stated schedule accommodates the requested stay, but other signs still need checking."
            if supports
            else "The meter's stated schedule does not establish metering for the full requested stay."
        )
    elif vehicle == "passenger" and commercial_overlap:
        summary = "An associated meter has commercial hours, but no usable passenger schedule for this stay."
        consequence = "Commercial meter hours do not establish passenger parking eligibility."
    elif unparsed:
        summary = "An associated meter schedule could not be interpreted reliably."
        consequence = "Verify vehicle eligibility, payment hours, and duration at the meter."
    else:
        summary = f"No usable {vehicle} meter schedule was found."
        consequence = "The associated meter records do not establish metering for this vehicle."

    return {
        "classification": "METERED",
        "has_passenger_schedule": bool(requested) if vehicle == "passenger" else False,
        "supports_requested_stay": supports,
        "summary": summary,
        "consequence": consequence,
        "schedule": schedule or None,
        "max_duration_minutes": maxima[0] if maxima else None,
        "source_url": next(
            (meter.get("source") for meter in meters if meter.get("source")), None
        ),
    }


def _user_summary(
    rule_items: list[dict[str, Any]],
    meter: dict[str, Any],
) -> dict[str, Any]:
    primary = rule_items[0] if rule_items else None
    if primary:
        next_step = (
            "Check the physical sign arrows and the exact curb extent before parking."
            if not primary["precise_curb_extent_verified"]
            else "Confirm no temporary postings or construction restrictions are present."
        )
        if primary["overlaps_stay"] and primary["precise_curb_extent_verified"]:
            category = "STRONG_RESTRICTION"
            status = "Restricted during stay"
            consequence = primary["consequence"]
        elif primary["overlaps_stay"]:
            category = "NEARBY_RESTRICTION"
            status = "Rule nearby — verify section"
            consequence = f"{primary['label']} overlaps your requested time"
            if primary.get("restriction_ends"):
                end = datetime.fromisoformat(primary["restriction_ends"])
                consequence += f"; the observed window ends at {_clock(end.hour * 60 + end.minute)}"
            consequence += ". Verify the exact curb section."
        else:
            category = "KNOWN_RULE"
            status = "Known rule found"
            consequence = primary["consequence"]
    elif meter.get("supports_requested_stay"):
        next_step = "Check all physical curb signs before relying on the meter."
        category = "METER_LEAD"
        status = "Passenger meter lead"
        consequence = "This passenger meter's stated schedule supports the requested stay."
    else:
        next_step = (
            "Read all physical signs for this exact street side before parking."
        )
        category = "UNKNOWN"
        status = "Limited rule data"
        consequence = "No usable parking-sign rule was established for this street side."
    return {
        "display_category": category,
        "display_status": status,
        "has_meaningful_finding": bool(rule_items),
        "primary_rule": primary,
        "primary_text": (
            primary["summary"]
            if primary
            else "No usable parking-sign rule found for this street side."
        ),
        "consequence": consequence,
        "meter_text": meter.get("summary"),
        "next_step": next_step,
        "evidence_url": (
            primary.get("source_url")
            if primary
            else meter.get("source_url")
        ),
    }


def build_frontend_results(
    bundle: dict[str, Any],
    classifications: dict[str, Any],
    *,
    explanation_markdown: str | None = None,
) -> dict[str, Any]:
    """Assemble map/sidebar payload from classifier + evidence only.

    Coordinates, statuses, and meter IDs come from NYC records / deterministic
    classification — never by parsing Writer prose.
    """
    class_by_id = {
        (c.get("candidate_id") or c.get("block_key")): c
        for c in classifications.get("classifications") or []
    }
    lead_by_id = {a.get("candidate_id"): a for a in classifications.get("leads") or []}
    cand_by_id = {
        (c.get("candidate_id") or c.get("block_key")): c for c in bundle.get("candidates") or []
    }
    request = bundle.get("request") or {}

    destination = bundle.get("coordinates") or bundle.get("geocode") or {}
    if isinstance(destination, dict) and destination.get("best"):
        destination = destination["best"]

    candidates_out = []
    for cid, cand in cand_by_id.items():
        legal_row = class_by_id.get(cid) or {}
        lead = lead_by_id.get(cid) or {}
        decision = legal_row.get("decision") or {}
        # Prefer classifier diagnostic; fall back to decision fields for UI details.
        diag = legal_row.get("diagnostic")
        if not diag and decision:
            # Minimal fallback
            diag = {
                "legal_explanation": decision.get("explanation"),
                "missing_evidence": decision.get("limitations") or [],
            }
        meters = cand.get("meters") or []
        meter = (meters or [{}])[0] or {}
        lat = cand.get("lat") if cand.get("lat") is not None else cand.get("approx_lat")
        lon = cand.get("lon") if cand.get("lon") is not None else cand.get("approx_lon")
        kind = cand.get("candidate_kind")
        # Coordinates are trusted when sourced from a meter record or CSCL geometry.
        # STREET_SIDE coordinates still represent an approximate centerline-side display,
        # never an exact curb or parking space.
        has_real_coords = bool(
            isinstance(lat, (int, float))
            and isinstance(lon, (int, float))
            and (
                (kind == "METER_POINT" and meter.get("lat") is not None)
                or (
                    kind == "STREET_SIDE"
                    and (cand.get("geometry_provenance") or {}).get("dataset_id")
                    == "inkn-q76z"
                )
            )
        )
        meter_regs = [
            reg for row in meters for reg in parse_meter_schedule_record(row)
        ]
        plain_rules = _plain_rule_findings(decision.get("rule_findings") or [])
        meter_summary = _meter_user_summary(meters, meter_regs, request)
        metering_classification = (
            "METERED"
            if meters
            else cand.get("metering_classification") or "UNKNOWN"
        )
        meter_summary["classification"] = metering_classification
        user_summary = _user_summary(
            plain_rules,
            meter_summary,
        )
        passenger_maxima = sorted(
            {
                reg.max_duration_minutes
                for reg in meter_regs
                if reg.vehicle_category == "passenger" and reg.max_duration_minutes
            }
        )
        retrieval = cand.get("retrieval") or {}
        limitations = decision.get("limitations") or []
        evidence_level = (
            "strong"
            if retrieval.get("signs_complete") is True
            and bool(cand.get("signs"))
            and not limitations
            else "partial"
            if retrieval.get("signs_complete") is True
            else "limited"
        )

        matched_signs = []
        for s in cand.get("signs") or []:
            matched_signs.append(
                {
                    "order_number": s.get("order_number"),
                    "sign_code": s.get("sign_code"),
                    "sign_description": s.get("sign_description"),
                    "on_street": s.get("on_street"),
                    "side_of_street": s.get("side_of_street"),
                    "sign_x_coord": s.get("sign_x_coord"),
                    "sign_y_coord": s.get("sign_y_coord"),
                    "arrow_direction": s.get("arrow_direction"),
                    "association_distance_m": s.get("association_distance_m"),
                    "street_side_matched": s.get("street_side_matched"),
                    "scope_verified": bool(s.get("scope_verified")),
                    "association": s.get("association"),
                    "source": s.get("source"),
                }
            )
        unmatched_signs = []
        for s in cand.get("nearby_signs_unmatched") or []:
            unmatched_signs.append(
                {
                    "order_number": s.get("order_number"),
                    "sign_description": s.get("sign_description"),
                    "on_street": s.get("on_street"),
                    "side_of_street": s.get("side_of_street"),
                    "association_distance_m": s.get("association_distance_m"),
                    "unmatched_reason": s.get("unmatched_reason") or s.get("street_side_match_reason"),
                    "applied_as_restriction": False,
                    "source": s.get("source"),
                }
            )

        candidates_out.append(
            {
                "candidate_id": cid,
                "candidate_kind": kind,
                "meter_number": cand.get("meter_number") or meter.get("meter_number"),
                "lat": lat if has_real_coords else None,
                "lon": lon if has_real_coords else None,
                "coordinates_trusted": has_real_coords,
                "geometry": cand.get("geometry"),
                "display_geometry": cand.get("display_geometry"),
                "geometry_provenance": cand.get("geometry_provenance"),
                "geometry_is_approximate": bool(cand.get("geometry_is_approximate")),
                "physical_id": cand.get("physical_id"),
                "blockface_id": cand.get("blockface_id"),
                "centerline_side": cand.get("centerline_side"),
                "side_derivation": cand.get("side_derivation"),
                "address_range": cand.get("address_range"),
                "on_street": cand.get("on_street"),
                "side_of_street": cand.get("side_of_street"),
                "from_street": cand.get("from_street"),
                "to_street": cand.get("to_street"),
                # Current from/to values come from associated sign stretches, not
                # intersection-to-intersection CSCL node names.
                "cross_streets_reliable": False,
                "distance_m": cand.get("distance_m"),
                "metering_classification": metering_classification,
                "metering_basis": cand.get("metering_basis"),
                "meter_summary": meter_summary,
                "meter_status": meter.get("status"),
                "meter_hours": meter.get("meter_hours"),
                "meter_observations": [
                    {
                        "meter_number": row.get("meter_number"),
                        "lat": row.get("lat"),
                        "lon": row.get("lon"),
                        "status": row.get("status"),
                        "source": row.get("source"),
                        "schedule_components": [
                            reg.to_dict() for reg in parse_meter_schedule_record(row)
                        ],
                    }
                    for row in meters
                    if isinstance(row.get("lat"), (int, float))
                    and isinstance(row.get("lon"), (int, float))
                ],
                "meter_schedule_components": [reg.to_dict() for reg in meter_regs],
                "max_duration_minutes": min(passenger_maxima) if passenger_maxima else None,
                "pay_by_cell_number": meter.get("pay_by_cell_number"),
                "meter_source": meter.get("source") or "https://data.cityofnewyork.us/d/693u-uax6",
                "legal_status": legal_row.get("status") or lead.get("legal_status"),
                "legal_explanation": decision.get("explanation"),
                "legal_limitations": limitations,
                "rule_findings": decision.get("rule_findings") or [],
                "plain_rule_findings": plain_rules,
                "user_summary": user_summary,
                "applied_regulations": decision.get("applied_regulations") or [],
                "supporting_sources": decision.get("supporting_sources") or [],
                "evidence_quality": {
                    "level": evidence_level,
                    "signs_complete": retrieval.get("signs_complete"),
                    "meters_complete": retrieval.get("meters_complete"),
                    "exact_curb_scope_verified": any(
                        bool(sign.get("scope_verified"))
                        for sign in cand.get("signs") or []
                    ),
                },
                "occupancy_data": {
                    "status": "unavailable",
                    "message": "Live space availability is not measured.",
                },
                "lead_status": lead.get("status"),
                "lead_score": lead.get("score"),
                "lead_reasons": lead.get("reasons") or [],
                "verification_tasks": lead.get("verification_tasks") or [],
                "matched_signs": matched_signs,
                "unmatched_nearby_signs": unmatched_signs,
                "retrieval": retrieval,
                "stretch": cand.get("stretch"),
                "closures": cand.get("closures") or [],
                "diagnostic": diag,
            }
        )

    # Rank for sidebar: promising first, then conditional by score, avoid last.
    order = {
        LeadStatus.PROMISING_LEAD.value: 0,
        LeadStatus.CONDITIONAL_LEAD.value: 1,
        LeadStatus.NO_USEFUL_EVIDENCE.value: 2,
        LeadStatus.AVOID.value: 3,
    }

    def _sort_key(c: dict[str, Any]) -> tuple:
        return (
            order.get(c.get("lead_status") or "", 9),
            -(c.get("lead_score") or 0),
            c.get("distance_m") if c.get("distance_m") is not None else 1e9,
        )

    candidates_out.sort(key=_sort_key)

    # Top card list: exclude AVOID from primary recommendations; keep ranked.
    top_cards = [
        c
        for c in candidates_out
        if c.get("lead_status") != LeadStatus.AVOID.value
        and c.get("coordinates_trusted")
    ]
    # Quality tiers for sidebar (not equal weight for all conditionals)
    promising = [c for c in top_cards if c.get("lead_status") == LeadStatus.PROMISING_LEAD.value]
    conditional = [
        c for c in top_cards if c.get("lead_status") == LeadStatus.CONDITIONAL_LEAD.value
    ]
    conditional_top = conditional[:5]
    conditional_more = conditional[5:]

    asp = ((bundle.get("exception_research") or {}).get("asp_calendar") or {})
    live_note = (bundle.get("exception_research") or {}).get("asp_live_note")

    return {
        "schema": "curbwise.frontend_results.v2",
        "request": request,
        "destination": {
            "label": destination.get("label") or destination.get("name"),
            "lat": destination.get("lat"),
            "lon": destination.get("lon"),
            "borough": destination.get("borough"),
            "source": (bundle.get("geocode") or {}).get("source")
            or "https://geosearch.planninglabs.nyc/docs/",
        },
        "status_counts": classifications.get("status_counts"),
        "lead_counts": classifications.get("lead_counts"),
        "recommendation_tiers": {
            "promising": promising,
            "conditional_top": conditional_top,
            "conditional_more_count": len(conditional_more),
            "conditional_more": conditional_more,
            "avoid": [
                c
                for c in candidates_out
                if c.get("lead_status") == LeadStatus.AVOID.value and c.get("coordinates_trusted")
            ],
        },
        "candidates": candidates_out,
        "asp": {
            "arrival_day": asp.get("arrival_day"),
            "calendar_source": asp.get("source"),
            "live_note": live_note,
        },
        "evidence_gaps": bundle.get("evidence_gaps") or [],
        "notes": [
            "Street-side lines use approximate offsets from NYC CSCL road-bed centerlines, not exact curb boundaries.",
            "Live space availability is not measured.",
            "Legal status and lead ranking are separate.",
            "DOUBLE_ARROW (<->), STREET_SIDE_MATCHED, and proximity are not SCOPE_VERIFIED.",
            "INSUFFICIENT_EVIDENCE means legality could not be verified — not that parking is illegal.",
            "Physical signs and temporary postings take precedence.",
        ],
        "explanation_markdown": explanation_markdown,
        "disclaimer": (
            "CurbWise is a research aid. Lead recommendations are investigation priorities, "
            "not determinations of legality or availability."
        ),
    }
