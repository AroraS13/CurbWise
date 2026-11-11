"""Deterministic research-lead ranking (NOT a legal classification)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from .models import ParkingStatus, ParseStatus, RestrictionKind
from .parse_regs import parse_meter_schedule_record, parse_sign_record
from .parking_rules import (
    NYC_TZ,
    _PROHIBITING,
    _iter_local_segments,
    _meter_violation_for_segment,
    _segment_overlaps_regulation,
    _as_nyc,
    meter_schedule_covers_interval,
)


class LeadStatus(str, Enum):
    PROMISING_LEAD = "PROMISING_LEAD"
    CONDITIONAL_LEAD = "CONDITIONAL_LEAD"
    AVOID = "AVOID"
    NO_USEFUL_EVIDENCE = "NO_USEFUL_EVIDENCE"


@dataclass
class LeadAssessment:
    status: LeadStatus
    score: int
    reasons: list[str] = field(default_factory=list)
    verification_tasks: list[str] = field(default_factory=list)
    legal_status: str | None = None
    candidate_id: str | None = None
    meter_number: str | None = None
    lat: float | None = None
    lon: float | None = None
    distance_m: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


def assess_lead(
    candidate: dict[str, Any],
    *,
    legal_status: str,
    arrival: datetime,
    departure: datetime,
    vehicle_category: str = "passenger",
) -> LeadAssessment:
    """Rank a candidate as a research lead without changing legal classification."""
    start = _as_nyc(arrival)
    end = _as_nyc(departure)
    reasons: list[str] = []
    tasks: list[str] = []
    score = 0

    kind = candidate.get("candidate_kind") or "SIGN_STRETCH"
    lat = candidate.get("lat") if candidate.get("lat") is not None else candidate.get("approx_lat")
    lon = candidate.get("lon") if candidate.get("lon") is not None else candidate.get("approx_lon")
    meter = (candidate.get("meters") or [None])[0]
    meter_number = (meter or {}).get("meter_number") if meter else candidate.get("meter_number")

    retrieval = candidate.get("retrieval") or {}
    signs = candidate.get("signs") or []
    parsed_signs = [parse_sign_record(s) for s in signs]
    parsed_meters = [
        reg
        for meter_row in (candidate.get("meters") or [])
        for reg in parse_meter_schedule_record(meter_row)
    ]

    # Hard avoid: verified prohibition at an established meter/point location.
    prohibited_here = False
    if legal_status == ParkingStatus.PROHIBITED.value and kind == "METER_POINT":
        # Only avoid if a parsed prohibiting regulation overlaps and is not merely an unverified stretch projection.
        for reg in parsed_signs:
            if reg.parse_status != ParseStatus.PARSED or reg.kind not in _PROHIBITING:
                continue
            for seg_a, seg_b in _iter_local_segments(start, end):
                if _segment_overlaps_regulation(seg_a, seg_b, reg):
                    # Bus-stop / arrow-partial near a meter: avoid that specific point.
                    prohibited_here = True
                    reasons.append(
                        f"Parsed {reg.kind.value} overlaps the stay near this location "
                        f"({reg.source_id})."
                    )
                    break
            if prohibited_here:
                break
        if prohibited_here or legal_status == ParkingStatus.PROHIBITED.value:
            # If engine marked prohibited with established scope at this candidate, avoid.
            return LeadAssessment(
                status=LeadStatus.AVOID,
                score=-100,
                reasons=reasons
                or ["Deterministic legal classification is PROHIBITED for this location."],
                verification_tasks=["Do not park here without reading the physical signs."],
                legal_status=legal_status,
                candidate_id=candidate.get("candidate_id"),
                meter_number=meter_number,
                lat=lat,
                lon=lon,
                distance_m=candidate.get("distance_m"),
            )

    if legal_status == ParkingStatus.PROHIBITED.value and kind != "METER_POINT":
        return LeadAssessment(
            status=LeadStatus.AVOID,
            score=-80,
            reasons=["Stretch/candidate classified PROHIBITED for the requested interval."],
            verification_tasks=["Skip this stretch unless on-site signs clearly contradict the record."],
            legal_status=legal_status,
            candidate_id=candidate.get("candidate_id"),
            meter_number=meter_number,
            lat=lat,
            lon=lon,
            distance_m=candidate.get("distance_m"),
        )

    # Geographic precision. A CSCL street side is a useful investigation unit,
    # but its display line is an approximate offset from the road-bed centerline.
    if kind == "METER_POINT" and lat is not None and lon is not None:
        score += 25
        reasons.append("Exact meter coordinates are available as an investigation anchor.")
    elif kind == "STREET_SIDE" and candidate.get("geometry"):
        score += 15
        reasons.append(
            "NYC CSCL geometry identifies an approximate physical street side for investigation."
        )
        tasks.append("Confirm the exact curb-zone boundaries from physical sign arrows on site.")
    elif lat is not None:
        score += 5
    else:
        tasks.append("Confirm the physical curb location on site; dataset stretch is not a single block.")

    # Meter fit
    active_compatible = False
    meter_parse_unknown = False
    passenger_regs = []
    for reg in parsed_meters:
        status = ((reg.metadata or {}).get("meter_status") or "").lower()
        if status and status != "active":
            reasons.append(f"Meter {reg.source_id} status is {status!r} (not Active).")
            continue
        if reg.parse_status != ParseStatus.PARSED or reg.kind != RestrictionKind.METERED:
            meter_parse_unknown = True
            reasons.append(f"Meter schedule {reg.source_id or 'record'} could not be parsed safely.")
            tasks.append("Confirm the complete meter schedule on the pay station or app.")
            continue
        if reg.vehicle_category != vehicle_category:
            if any(
                _segment_overlaps_regulation(a, b, reg)
                for a, b in _iter_local_segments(start, end)
            ):
                reasons.append(
                    f"{reg.vehicle_category.title()} meter hours overlap the stay but do not "
                    f"establish {vehicle_category} eligibility."
                )
            continue
        passenger_regs.append(reg)
        viol = None
        for seg_a, seg_b in _iter_local_segments(start, end):
            viol = _meter_violation_for_segment(seg_a, seg_b, start, end, reg)
            if viol:
                break
        if viol:
            reasons.append(f"Meter duration/hours conflict: {viol}")
            score -= 20
            continue
    if passenger_regs and meter_schedule_covers_interval(
        parsed_meters,
        start,
        end,
        vehicle_category=vehicle_category,
    ):
        has_violation = any(
            _meter_violation_for_segment(a, b, start, end, reg)
            for reg in passenger_regs
            for a, b in _iter_local_segments(start, end)
        )
        if not has_violation:
            active_compatible = True
            score += 35
            reasons.append("Parsed passenger meter schedule covers the full requested stay.")
            max_values = sorted(
                {reg.max_duration_minutes for reg in passenger_regs if reg.max_duration_minutes}
            )
            if max_values:
                reasons.append(
                    "Applicable posted maximum: "
                    + ", ".join(f"{value} minutes" for value in max_values)
                    + "."
                )
    elif parsed_meters and not meter_parse_unknown:
        reasons.append(
            "No parsed passenger meter schedule covers the entire requested stay."
        )
        tasks.append("Verify passenger eligibility and payment hours on site.")
    elif kind == "STREET_SIDE":
        classification = candidate.get("metering_classification") or "UNKNOWN"
        if classification == "UNMETERED":
            reasons.append(
                "No NYC meter record matched this street side; this does not establish free or legal parking."
            )
        else:
            reasons.append("Metering for this street side is unknown.")

    # Nearby restrictions / uncertainty — only STREET_SIDE_MATCHED signs are in candidate["signs"].
    # Unmatched opposite-curb / cross-street signs are diagnostic only and must not drive AVOID.
    bus_or_partial = False
    unparsed = 0
    for sign, reg in zip(signs, parsed_signs):
        # Skip explicitly unmatched tags if any slipped through.
        if sign.get("street_side_matched") is False:
            continue
        if reg.parse_status == ParseStatus.UNPARSED:
            unparsed += 1
            continue
        if reg.kind == RestrictionKind.INFORMATIONAL:
            continue
        if reg.kind in _PROHIBITING:
            overlaps = any(
                _segment_overlaps_regulation(a, b, reg) for a, b in _iter_local_segments(start, end)
            )
            if overlaps:
                bus_or_partial = True
                score -= 15
                reasons.append(
                    f"Same-street/side {reg.kind.value} may affect part of the curb "
                    f"({reg.source_id}; scope={reg.scope_certainty.value}; "
                    f"STREET_SIDE_MATCHED≠SCOPE_VERIFIED)."
                )
                tasks.append("Verify physical sign arrows and exact curb extent at this spot.")
            else:
                score += 5
                reasons.append(
                    f"Associated {reg.kind.value} does not overlap the requested interval "
                    f"({reg.source_id})."
                )

    unmatched_n = len(candidate.get("nearby_signs_unmatched") or [])
    if unmatched_n:
        reasons.append(
            f"{unmatched_n} nearby sign(s) were excluded (street/side mismatch) and not applied."
        )

    if unparsed:
        score -= 5 * min(unparsed, 3)
        tasks.append(f"Interpret {unparsed} unparsed nearby sign description(s) on site.")

    completeness_unknown = retrieval.get("signs_complete") is not True
    if retrieval.get("signs_truncated") or retrieval.get("signs_complete") is False:
        score -= 10
        tasks.append("Sign retrieval for this location was incomplete; re-check curb signs.")
    elif retrieval.get("signs_complete") is True:
        score += 5
    elif retrieval.get("signs_complete") is None:
        score -= 5
        tasks.append(
            "Sign retrieval completeness is unknown; confirm curb signs on site."
        )

    # Distance preference (closer is better, mild)
    dist = candidate.get("distance_m")
    if isinstance(dist, (int, float)):
        if dist <= 150:
            score += 10
        elif dist <= 300:
            score += 5
        elif dist > 500:
            score -= 5

    stretch = candidate.get("stretch") or {}
    if stretch.get("likely_multi_block_stretch"):
        tasks.append(
            "Dataset from/to spans multiple numbered streets; do not treat it as one physical block."
        )
        if kind != "METER_POINT":
            score -= 15

    # Inactive meters are never promising passenger parking opportunities.
    meter_status = ((meter or {}).get("status") or "").lower()
    inactive = bool(meter_status and meter_status != "active")

    # Final lead bucket — recalculated after association fixes.
    # Do not auto-upgrade uncertain / incomplete retrieval cases to PROMISING_LEAD.
    if inactive and kind == "METER_POINT":
        status = LeadStatus.CONDITIONAL_LEAD if score >= 0 else LeadStatus.AVOID
        reasons.append("Meter is not Active; not promoted as a promising passenger opportunity.")
        tasks.append("Confirm meter status on the pay station or app before relying on it.")
    elif not active_compatible and kind == "METER_POINT":
        if score < 0:
            status = LeadStatus.AVOID
        else:
            status = LeadStatus.CONDITIONAL_LEAD
            tasks.append("Confirm meter status/hours on the pay station or app.")
    elif (
        active_compatible
        and not bus_or_partial
        and unparsed == 0
        and not completeness_unknown
        and bool(signs)
    ):
        # Require at least one same-street/side sign record; a bare meter point
        # with zero matched signs is useful but not PROMISING on silence alone.
        status = LeadStatus.PROMISING_LEAD
    elif active_compatible:
        status = LeadStatus.CONDITIONAL_LEAD
        if not signs:
            reasons.append(
                "No same-street/side signs were matched; silence is not evidence of permission."
            )
            tasks.append("Confirm curb signs on site — none were matched to this meter side.")
        if bus_or_partial:
            tasks.append("Check for bus stop / arrowed no-standing plates immediately adjacent.")
        if completeness_unknown:
            reasons.append(
                "Retrieval completeness unknown or incomplete — not auto-upgraded to PROMISING_LEAD."
            )
    elif kind == "METER_POINT":
        status = LeadStatus.CONDITIONAL_LEAD if score >= 10 else LeadStatus.NO_USEFUL_EVIDENCE
    else:
        status = LeadStatus.NO_USEFUL_EVIDENCE if score < 15 else LeadStatus.CONDITIONAL_LEAD

    if legal_status == ParkingStatus.INSUFFICIENT_EVIDENCE.value and status == LeadStatus.PROMISING_LEAD:
        # Useful research lead with unresolved legality — keep PROMISING only when
        # Active+compatible+complete and no overlapping same-side uncertain plates.
        # Otherwise CONDITIONAL.
        if bus_or_partial or completeness_unknown or inactive:
            status = LeadStatus.CONDITIONAL_LEAD
            reasons.append(
                "Legal classification remains INSUFFICIENT_EVIDENCE; lead kept CONDITIONAL."
            )
        else:
            reasons.append(
                "Legal classification remains INSUFFICIENT_EVIDENCE; research lead only — "
                "not a determination of legality."
            )
        tasks.append("Read the physical signs before parking; legality is not confirmed.")
    elif legal_status == ParkingStatus.INSUFFICIENT_EVIDENCE.value and status == LeadStatus.CONDITIONAL_LEAD:
        tasks.append("Read the physical signs before parking; legality is not confirmed.")

    return LeadAssessment(
        status=status,
        score=score,
        reasons=reasons,
        verification_tasks=list(dict.fromkeys(tasks)),
        legal_status=legal_status,
        candidate_id=candidate.get("candidate_id"),
        meter_number=meter_number,
        lat=lat,
        lon=lon,
        distance_m=candidate.get("distance_m"),
    )


def rank_leads(assessments: list[LeadAssessment], *, top_n: int = 5) -> list[LeadAssessment]:
    order = {
        LeadStatus.PROMISING_LEAD: 0,
        LeadStatus.CONDITIONAL_LEAD: 1,
        LeadStatus.NO_USEFUL_EVIDENCE: 2,
        LeadStatus.AVOID: 3,
    }
    usable = [a for a in assessments if a.status != LeadStatus.AVOID]
    usable.sort(key=lambda a: (order.get(a.status, 9), -a.score, a.distance_m or 1e9))
    return usable[:top_n]
