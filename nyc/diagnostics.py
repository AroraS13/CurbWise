"""Per-candidate geographic / classification diagnostics for Phase 3.6 audits."""

from __future__ import annotations

from typing import Any

from .models import EvaluationContext, ParkingDecision, ScopeCertainty
from .parse_regs import parse_meter_schedule_record, parse_sign_record
from .parking_rules import _PROHIBITING, _regulation_scope_verified


def candidate_diagnostic(candidate: dict[str, Any], decision: ParkingDecision) -> dict[str, Any]:
    """Build a readable diagnostic record for one street-side or fallback candidate."""
    meter = (candidate.get("meters") or [None])[0] or {}
    retrieval = candidate.get("retrieval") or {}
    matched_signs = []
    for s in candidate.get("signs") or []:
        reg = parse_sign_record(s)
        matched_signs.append(
            {
                "order_number": s.get("order_number"),
                "sign_code": s.get("sign_code"),
                "on_street": s.get("on_street"),
                "side_of_street": s.get("side_of_street"),
                "association_distance_m": s.get("association_distance_m"),
                "street_side_matched": s.get("street_side_matched"),
                "scope_verified": bool(
                    _regulation_scope_verified(reg, EvaluationContext())
                ),
                "scope_certainty": reg.scope_certainty.value,
                "arrow_direction": s.get("arrow_direction") or reg.arrow_direction,
                "kind": reg.kind.value,
                "sign_description": s.get("sign_description"),
                "inferred_scope_note": (
                    "DOUBLE_ARROW (<->) plate cue — not SCOPE_VERIFIED"
                    if reg.scope_certainty == ScopeCertainty.DOUBLE_ARROW
                    else (
                        "ARROW_PARTIAL plate cue — not SCOPE_VERIFIED"
                        if reg.scope_certainty == ScopeCertainty.ARROW_PARTIAL
                        else "arrow/scope cue unknown — not SCOPE_VERIFIED"
                    )
                ),
            }
        )

    unmatched = []
    for s in candidate.get("nearby_signs_unmatched") or []:
        unmatched.append(
            {
                "order_number": s.get("order_number"),
                "on_street": s.get("on_street"),
                "side_of_street": s.get("side_of_street"),
                "association_distance_m": s.get("association_distance_m"),
                "unmatched_reason": s.get("unmatched_reason") or s.get("street_side_match_reason"),
                "sign_description": (s.get("sign_description") or "")[:120],
                "applied_as_restriction": False,
            }
        )

    meter_regs = parse_meter_schedule_record(meter) if meter else []
    passenger_max = [
        reg.max_duration_minutes
        for reg in meter_regs
        if reg.vehicle_category == "passenger" and reg.max_duration_minutes
    ]
    applied = decision.applied_regulations or []
    prohibiting = [r for r in applied if r.kind in _PROHIBITING]

    return {
        "candidate_kind": candidate.get("candidate_kind"),
        "physical_id": candidate.get("physical_id"),
        "blockface_id": candidate.get("blockface_id"),
        "geometry_is_approximate": candidate.get("geometry_is_approximate"),
        "geometry_provenance": candidate.get("geometry_provenance"),
        "meter_id": candidate.get("meter_number") or meter.get("meter_number"),
        "lat": candidate.get("lat") or candidate.get("approx_lat"),
        "lon": candidate.get("lon") or candidate.get("approx_lon"),
        "on_street": candidate.get("on_street"),
        "side_of_street": candidate.get("side_of_street"),
        "meter_status": meter.get("status"),
        "meter_hours": meter.get("meter_hours") or candidate.get("meter_hours"),
        "meter_max_duration_minutes": max(passenger_max) if passenger_max else None,
        "meter_schedule_components": [reg.to_dict() for reg in meter_regs],
        "metering_classification": candidate.get("metering_classification") or (
            "METERED" if candidate.get("meters") else "UNKNOWN"
        ),
        "metering_basis": candidate.get("metering_basis"),
        "legal_status": decision.status.value,
        "legal_explanation": decision.explanation,
        "rule_findings": decision.rule_findings,
        "matched_signs": matched_signs,
        "unmatched_nearby_signs": unmatched,
        "prohibiting_applied": [
            {
                "source_id": r.source_id,
                "kind": r.kind.value,
                "scope_certainty": r.scope_certainty.value,
                "scope_verified": _regulation_scope_verified(r, EvaluationContext()),
                "raw_description": r.raw_description,
            }
            for r in prohibiting
        ],
        "missing_evidence": list(decision.limitations or []),
        "retrieval": {
            "signs_complete": retrieval.get("signs_complete"),
            "signs_truncated": retrieval.get("signs_truncated"),
            "signs_matched": retrieval.get("signs_matched", len(matched_signs)),
            "signs_unmatched_nearby": retrieval.get(
                "signs_unmatched_nearby", len(unmatched)
            ),
        },
        "notes": [
            "CSCL street-side display geometry is approximate and does not represent an exact curb boundary.",
            "STREET_SIDE_MATCHED establishes geographic consistency only.",
            "SCOPE_VERIFIED is never inferred from DOUBLE_ARROW, STREET_SIDE_MATCHED, or proximity.",
            "Unmatched nearby signs are diagnostic context and are not applied as restrictions.",
        ],
    }
