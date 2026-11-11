"""Deterministic parking-interval classifier (no LLM).

Evaluates a requested stay against structured regulations and returns
POTENTIALLY_PERMITTED / PROHIBITED / INSUFFICIENT_EVIDENCE with provenance.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, time
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .models import (
    AspSuspension,
    DayTimeWindow,
    EvaluationContext,
    ParkingDecision,
    ParkingStatus,
    ParseStatus,
    Regulation,
    RestrictionKind,
    ScopeCertainty,
)
from .parse_regs import parse_candidate_regulations, parse_meter_record, parse_sign_record

NYC_TZ = ZoneInfo("America/New_York")

_PROHIBITING = {
    RestrictionKind.NO_PARKING,
    RestrictionKind.NO_STANDING,
    RestrictionKind.NO_STOPPING,
    RestrictionKind.ASP_STREET_CLEANING,
}

# All plate-level cues are unverified geographic applicability without external proof.
# DOUBLE_ARROW (<->) is a bidirectional plate cue, not curb-zone verification.
_UNVERIFIED_SCOPE = {
    ScopeCertainty.DOUBLE_ARROW,
    ScopeCertainty.ARROW_PARTIAL,
    ScopeCertainty.UNKNOWN,
}


def evaluate_parking_interval(
    arrival: datetime,
    departure: datetime,
    regulations: list[Regulation],
    *,
    context: EvaluationContext | None = None,
    candidate: dict[str, Any] | None = None,
) -> ParkingDecision:
    """Classify whether parking may be permitted for [arrival, departure).

    arrival/departure should be timezone-aware; naive values are assumed America/New_York.
    """
    ctx = context or EvaluationContext()
    candidate = candidate or {}
    start = _as_nyc(arrival)
    end = _as_nyc(departure)
    if end <= start:
        return ParkingDecision(
            status=ParkingStatus.INSUFFICIENT_EVIDENCE,
            explanation="Departure must be after arrival.",
            interval=_interval_dict(start, end),
            candidate=_candidate_stub(candidate),
            limitations=["Invalid parking interval."],
        )

    limitations: list[str] = []
    applied: list[Regulation] = []
    unknown: list[Regulation] = []
    sources: list[dict[str, str]] = []

    for reg in regulations:
        _add_source(sources, reg)
        if reg.parse_status == ParseStatus.UNPARSED or reg.kind == RestrictionKind.UNKNOWN:
            unknown.append(reg)
            if reg.parse_limitation:
                limitations.append(reg.parse_limitation)
        elif reg.parse_status == ParseStatus.INFORMATIONAL or reg.kind == RestrictionKind.INFORMATIONAL:
            continue
        else:
            applied.append(reg)
            if reg.parse_limitation:
                limitations.append(reg.parse_limitation)

    # --- Hard evidence-completeness gates ---
    if ctx.signs_truncated or ctx.meters_truncated:
        limitations.append(
            "API results were truncated; claims of complete regulatory coverage for this area are invalid."
        )

    has_sign_evidence = ctx.has_sign_records or any(
        r.kind in _PROHIBITING
        or (r.kind == RestrictionKind.METERED and "HMP" in (r.raw_description or "").upper())
        or (r.parse_status == ParseStatus.UNPARSED and "693u-uax6" not in (r.source_url or ""))
        or (r.kind == RestrictionKind.INFORMATIONAL)
        for r in regulations
    )
    has_meter_evidence = ctx.has_meter_records or any(
        r.kind == RestrictionKind.METERED and "HMP" not in (r.raw_description or "").upper()
        for r in regulations
    )

    if not regulations and not has_sign_evidence and not has_meter_evidence:
        limitations.append("No sign or meter records were provided for this location.")
        return ParkingDecision(
            status=ParkingStatus.INSUFFICIENT_EVIDENCE,
            explanation=(
                "Missing records do not imply permission to park. "
                "No curb-regulation evidence was available for this candidate."
            ),
            applied_regulations=[],
            unknown_regulations=unknown,
            supporting_sources=sources,
            limitations=limitations,
            interval=_interval_dict(start, end),
            candidate=_candidate_stub(candidate),
        )

    # Construction permits: advisory only.
    if ctx.construction_permits:
        limitations.append(
            "Construction/occupancy permits are advisory evidence of a possible restriction; "
            "they do not prove a continuous active closure during the requested interval."
        )
        for permit in ctx.construction_permits:
            sources.append(
                {
                    "id": str(permit.get("uniqueid") or permit.get("segmentid") or "closure"),
                    "url": str(permit.get("source") or "https://data.cityofnewyork.us/d/i6b5-j7bu"),
                }
            )

    # Never apply today-only 311 ASP status to a future (or non-today) evaluation.
    if ctx.asp_status_today and not ctx.evaluate_date_is_today:
        limitations.append(
            "A today-only 311 ASP status was supplied but not applied because the evaluated "
            "interval is not today in America/New_York."
        )
    elif ctx.asp_status_today and ctx.evaluate_date_is_today:
        status_u = (ctx.asp_status_today or "").upper()
        if "SUSPEND" in status_u:
            limitations.append(
                f"Live ASP status today indicates suspension ({ctx.asp_status_today!r}); "
                "street-cleaning ASP rules are treated as suspended for today's evaluation."
            )
        elif status_u and "IN EFFECT" not in status_u and "NOT IN EFFECT" not in status_u:
            limitations.append(
                f"Live ASP status today is ambiguous ({ctx.asp_status_today!r}); "
                "weather/emergency uncertainty remains."
            )
        if ctx.asp_status_today_source:
            sources.append({"id": "asp_status_today", "url": ctx.asp_status_today_source})

    # Unknown future emergency/weather ASP.
    if not ctx.evaluate_date_is_today:
        limitations.append(
            "Emergency/weather ASP suspensions cannot be known in advance from the holiday calendar alone."
        )

    suspensions_by_date = {s.date: s for s in ctx.asp_suspensions}

    # Location scope notes (FULL_SEGMENT vs ARROW_PARTIAL) — informational.
    _, scope_notes = _location_scope_ok(applied, ctx)
    limitations.extend(scope_notes)

    # Slice the stay into local day segments and evaluate overlaps.
    verified_prohibitions: list[tuple[Regulation, str]] = []
    uncertain_prohibitions: list[tuple[Regulation, str]] = []
    meter_violations: list[tuple[Regulation, str]] = []

    for seg_start, seg_end in _iter_local_segments(start, end):
        day = seg_start.date()
        day_key = day.isoformat()
        suspension = suspensions_by_date.get(day_key)
        major_holiday = bool(suspension and suspension.major_legal_holiday)
        asp_holiday_suspended = bool(suspension)  # any listed holiday suspends street cleaning
        live_asp_suspended = bool(
            ctx.evaluate_date_is_today
            and ctx.asp_status_today
            and "SUSPEND" in (ctx.asp_status_today or "").upper()
        )

        for reg in applied:
            if reg.kind in _PROHIBITING:
                if reg.kind == RestrictionKind.ASP_STREET_CLEANING and (
                    asp_holiday_suspended or live_asp_suspended
                ):
                    continue
                if not _segment_overlaps_regulation(seg_start, seg_end, reg):
                    continue
                msg = f"{reg.kind.value} overlaps {seg_start.isoformat()}–{seg_end.isoformat()}"
                if _regulation_scope_verified(reg, ctx):
                    verified_prohibitions.append((reg, msg))
                elif _regulation_geographically_consistent(reg):
                    # STREET_SIDE_MATCHED / candidate-local, but curb extent unverified.
                    uncertain_prohibitions.append((reg, msg))
                # else: unmatched / wrong-side should not be in applied regs

            if reg.kind == RestrictionKind.METERED:
                if major_holiday:
                    # DOT: parking meter regulations suspended on major legal holidays.
                    continue
                if reg.vehicle_category and reg.vehicle_category != ctx.vehicle_category:
                    if _segment_overlaps_regulation(seg_start, seg_end, reg):
                        limitations.append(
                            f"{reg.vehicle_category.title()} meter schedule overlaps the stay; "
                            f"it does not establish {ctx.vehicle_category} meter eligibility."
                        )
                    continue
                violation = _meter_violation_for_segment(seg_start, seg_end, start, end, reg)
                if violation:
                    # Point-meter duration/hours apply at this meter candidate.
                    if _is_point_meter(reg) or ctx.has_meter_records or ctx.location_scope_established:
                        meter_violations.append((reg, violation))

    # Unparsed regulations that look like curb rules create insufficiency unless
    # we already have a clear prohibition on an established location.
    critical_unknown = [
        u
        for u in unknown
        if u.kind in {RestrictionKind.UNKNOWN, RestrictionKind.NO_PARKING, RestrictionKind.NO_STANDING}
        or u.parse_status == ParseStatus.UNPARSED
    ]

    # --- Decision policy (conservative; STREET_SIDE_MATCHED ≠ SCOPE_VERIFIED) ---

    if verified_prohibitions:
        hit_regs = _unique_regs([r for r, _ in verified_prohibitions])
        reasons = [msg for _, msg in verified_prohibitions]
        return ParkingDecision(
            status=ParkingStatus.PROHIBITED,
            explanation=(
                "At least one restriction with verified curb coverage overlaps the requested "
                "interval. " + "; ".join(dict.fromkeys(reasons))
            ),
            applied_regulations=hit_regs,
            unknown_regulations=unknown,
            supporting_sources=sources,
            limitations=_dedupe(limitations),
            interval=_interval_dict(start, end),
            candidate=_candidate_stub(candidate),
        )

    if meter_violations:
        hit_regs = _unique_regs([r for r, _ in meter_violations])
        reasons = [msg for _, msg in meter_violations]
        return ParkingDecision(
            status=ParkingStatus.PROHIBITED,
            explanation=(
                "Meter rules are violated for the requested stay at this meter location. "
                + "; ".join(dict.fromkeys(reasons))
            ),
            applied_regulations=hit_regs,
            unknown_regulations=unknown,
            supporting_sources=sources,
            limitations=_dedupe(limitations),
            interval=_interval_dict(start, end),
            candidate=_candidate_stub(candidate),
        )

    if uncertain_prohibitions:
        limitations.append(
            "One or more geographically consistent restrictions have unverified curb extent "
            "(single arrow, missing arrow, bus-stop, or partial coverage). "
            "STREET_SIDE_MATCHED is not SCOPE_VERIFIED; they are not asserted as prohibitions "
            "at the meter point."
        )
        return ParkingDecision(
            status=ParkingStatus.INSUFFICIENT_EVIDENCE,
            explanation=(
                "Nearby same-street/side restrictions may affect part of the curb, but evidence "
                "does not verify that their regulatory extent covers this meter point. "
                "Not classified as prohibited or potentially permitted."
            ),
            applied_regulations=_unique_regs([r for r, _ in uncertain_prohibitions]),
            unknown_regulations=unknown,
            supporting_sources=sources,
            limitations=_dedupe(limitations),
            interval=_interval_dict(start, end),
            candidate=_candidate_stub(candidate),
        )

    if ctx.signs_truncated or ctx.meters_truncated:
        return ParkingDecision(
            status=ParkingStatus.INSUFFICIENT_EVIDENCE,
            explanation="Truncated API results prevent a complete regulatory assessment.",
            applied_regulations=applied,
            unknown_regulations=unknown,
            supporting_sources=sources,
            limitations=_dedupe(limitations),
            interval=_interval_dict(start, end),
            candidate=_candidate_stub(candidate),
        )

    if ctx.require_sign_retrieval_completeness and ctx.signs_complete is not True:
        if ctx.signs_complete is None:
            limitations.append(
                "Sign retrieval completeness is unknown; unknown is not treated as complete."
            )
            explanation = (
                "Sign retrieval completeness is unknown, so legality cannot be affirmed "
                "as potentially permitted."
            )
        else:
            limitations.append("Sign retrieval for this location was incomplete.")
            explanation = "Incomplete sign retrieval prevents affirming potential permission."
        return ParkingDecision(
            status=ParkingStatus.INSUFFICIENT_EVIDENCE,
            explanation=explanation,
            applied_regulations=applied,
            unknown_regulations=unknown,
            supporting_sources=sources,
            limitations=_dedupe(limitations),
            interval=_interval_dict(start, end),
            candidate=_candidate_stub(candidate),
        )

    if not has_sign_evidence:
        limitations.append("No parking-sign records were available for this candidate.")
        return ParkingDecision(
            status=ParkingStatus.INSUFFICIENT_EVIDENCE,
            explanation="Missing sign records do not imply permission to park.",
            applied_regulations=applied,
            unknown_regulations=unknown,
            supporting_sources=sources,
            limitations=_dedupe(limitations),
            interval=_interval_dict(start, end),
            candidate=_candidate_stub(candidate),
        )

    if critical_unknown:
        return ParkingDecision(
            status=ParkingStatus.INSUFFICIENT_EVIDENCE,
            explanation=(
                "One or more sign/meter descriptions could not be parsed under the supported "
                "subset, so legality cannot be affirmed."
            ),
            applied_regulations=applied,
            unknown_regulations=critical_unknown,
            supporting_sources=sources,
            limitations=_dedupe(limitations),
            interval=_interval_dict(start, end),
            candidate=_candidate_stub(candidate),
        )

    # Permission requires verified curb-segment evidence (FULL_SEGMENT regs and/or
    # explicitly established scope) — not merely a meter point coordinate.
    permission_scope_ok = _permission_scope_established(applied, ctx)
    if not permission_scope_ok:
        return ParkingDecision(
            status=ParkingStatus.INSUFFICIENT_EVIDENCE,
            explanation=(
                "Evidence does not establish verified curb coverage for this location; "
                "declining to label it as potentially permitted. "
                "Meter coordinates and STREET_SIDE_MATCHED associations are not SCOPE_VERIFIED."
            ),
            applied_regulations=applied,
            unknown_regulations=unknown,
            supporting_sources=sources,
            limitations=_dedupe(limitations),
            interval=_interval_dict(start, end),
            candidate=_candidate_stub(candidate),
        )

    # Adequate supporting evidence and no overlapping prohibition/meter violation.
    relevant = [
        r
        for r in applied
        if r.kind in _PROHIBITING or r.kind == RestrictionKind.METERED
    ]
    return ParkingDecision(
        status=ParkingStatus.POTENTIALLY_PERMITTED,
        explanation=(
            "Parsed regulations with verified curb-segment evidence do not prohibit the "
            "requested interval; this is not a guarantee of legality or space availability. "
            "Physical signs and temporary restrictions take precedence."
        ),
        applied_regulations=relevant or applied,
        unknown_regulations=unknown,
        supporting_sources=sources,
        limitations=_dedupe(limitations),
        interval=_interval_dict(start, end),
        candidate=_candidate_stub(candidate),
    )


def evaluate_candidate(
    candidate: dict[str, Any],
    arrival: datetime,
    departure: datetime,
    *,
    asp_suspensions: list[dict[str, Any] | AspSuspension] | None = None,
    signs_truncated: bool = False,
    meters_truncated: bool = False,
    closures_truncated: bool = False,
    asp_status_today: str | None = None,
    asp_status_today_source: str | None = None,
    location_scope_established: bool | None = None,
    signs_complete: bool | None = None,
    meters_complete: bool | None = None,
    require_sign_retrieval_completeness: bool | None = None,
) -> ParkingDecision:
    """Parse a Phase-1-style candidate dict and classify the stay."""
    regs = parse_candidate_regulations(candidate)
    suspensions = [_coerce_suspension(s) for s in (asp_suspensions or [])]
    start = _as_nyc(arrival)
    end = _as_nyc(departure)
    today = datetime.now(NYC_TZ).date()
    # Only treat as "today evaluation" when the entire stay is on today's date.
    evaluate_is_today = start.date() == today and (end - timedelta(microseconds=1)).date() == today

    # Infer scope from FULL_SEGMENT curb regs only — never from meter coordinates.
    inferred_scope = _infer_scope_established(regs)
    scope_flag = inferred_scope if location_scope_established is None else location_scope_established

    retrieval = candidate.get("retrieval") or {}
    if signs_complete is None and "signs_complete" in retrieval:
        signs_complete = retrieval.get("signs_complete")
    if meters_complete is None and "meters_complete" in retrieval:
        meters_complete = retrieval.get("meters_complete")

    kind = candidate.get("candidate_kind")
    # Meter-point pipeline requires known-complete retrieval before permission claims.
    req_complete = (
        require_sign_retrieval_completeness
        if require_sign_retrieval_completeness is not None
        else kind == "METER_POINT"
    )

    ctx = EvaluationContext(
        signs_truncated=signs_truncated,
        meters_truncated=meters_truncated,
        closures_truncated=closures_truncated,
        has_sign_records=bool(candidate.get("signs")),
        has_meter_records=bool(candidate.get("meters")),
        location_scope_established=scope_flag,
        signs_complete=signs_complete,
        meters_complete=meters_complete,
        require_sign_retrieval_completeness=bool(req_complete),
        candidate_kind=kind,
        asp_suspensions=suspensions,
        asp_status_today=asp_status_today if evaluate_is_today else None,
        asp_status_today_source=asp_status_today_source if evaluate_is_today else None,
        evaluate_date_is_today=evaluate_is_today,
        construction_permits=list(candidate.get("closures") or []),
    )
    # If caller passed today status but stay isn't today, keep a note via context path.
    if asp_status_today and not evaluate_is_today:
        ctx.asp_status_today = asp_status_today
        ctx.asp_status_today_source = asp_status_today_source
        ctx.evaluate_date_is_today = False

    decision = evaluate_parking_interval(
        start,
        end,
        regs,
        context=ctx,
        candidate=candidate,
    )
    decision.rule_findings = build_rule_findings(start, end, regs, context=ctx)
    return decision


# --- overlap helpers ---


def _as_nyc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=NYC_TZ)
    return dt.astimezone(NYC_TZ)


def _interval_dict(start: datetime, end: datetime) -> dict[str, str]:
    return {
        "arrival": start.isoformat(),
        "departure": end.isoformat(),
        "timezone": "America/New_York",
    }


def _candidate_stub(candidate: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "block_key",
        "borough",
        "on_street",
        "from_street",
        "to_street",
        "side_of_street",
        "approx_lat",
        "approx_lon",
        "distance_m",
    )
    return {k: candidate.get(k) for k in keys if k in candidate}


def _add_source(sources: list[dict[str, str]], reg: Regulation) -> None:
    item = {"id": reg.source_id, "url": reg.source_url}
    if item not in sources and (reg.source_id or reg.source_url):
        sources.append(item)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def _unique_regs(regs: Iterable[Regulation]) -> list[Regulation]:
    out: list[Regulation] = []
    seen: set[tuple] = set()
    for r in regs:
        key = (r.kind, r.source_id, r.raw_description, r.arrow_direction)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _coerce_suspension(value: dict[str, Any] | AspSuspension) -> AspSuspension:
    if isinstance(value, AspSuspension):
        return value
    return AspSuspension(
        date=str(value.get("date")),
        holiday=str(value.get("holiday") or ""),
        major_legal_holiday=bool(value.get("major_legal_holiday")),
        source=value.get("source"),
    )


def _iter_local_segments(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Split [start, end) into per-local-date segments."""
    segments: list[tuple[datetime, datetime]] = []
    cur = start
    while cur < end:
        next_midnight = datetime.combine(cur.date() + timedelta(days=1), time(0, 0), tzinfo=NYC_TZ)
        seg_end = min(end, next_midnight)
        segments.append((cur, seg_end))
        cur = seg_end
    return segments


def _minute_of(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _window_active_on_day(window: DayTimeWindow, day: date) -> bool:
    return day.weekday() in window.days


def _ranges_for_window_on_day(window: DayTimeWindow) -> list[tuple[int, int]]:
    """Return [start, end) minute ranges on a single local day for a window."""
    if window.anytime:
        return [(0, 24 * 60)]
    if window.end_minute > window.start_minute:
        return [(window.start_minute, window.end_minute)]
    if window.end_minute == window.start_minute:
        # Degenerate: treat as empty rather than all-day.
        return []
    # Overnight: e.g. 22:00-06:00 => [22:00, 24:00) on this day encoding,
    # and [0, 06:00) also on this day encoding when the window is interpreted
    # as applying each listed weekday's overnight start.
    return [(window.start_minute, 24 * 60), (0, window.end_minute)]


def _segment_overlaps_regulation(seg_start: datetime, seg_end: datetime, reg: Regulation) -> bool:
    for window in reg.windows:
        if _segment_overlaps_window(seg_start, seg_end, window):
            return True
    return False


def _segment_overlaps_window(seg_start: datetime, seg_end: datetime, window: DayTimeWindow) -> bool:
    # Segment is within one local date.
    day = seg_start.date()
    if not _window_active_on_day(window, day):
        # Overnight windows: the early-morning piece belongs to the weekday of
        # the overnight start (previous calendar day). Handle by also checking
        # previous day for the morning remnant when window crosses midnight.
        if window.anytime or window.end_minute > window.start_minute:
            return False
        prev = day - timedelta(days=1)
        if prev.weekday() not in window.days:
            return False
        # Morning remnant [0, end) from previous day's overnight start.
        return _ranges_overlap(
            _minute_of(seg_start),
            _minute_of(seg_end) if seg_end.date() == day else 24 * 60,
            0,
            window.end_minute,
        )

    seg_a = _minute_of(seg_start)
    seg_b = _minute_of(seg_end) if seg_end.date() == day else 24 * 60
    if seg_end.date() == day and seg_b == seg_a and seg_end > seg_start:
        # sub-minute; treat end exclusive with at least epsilon
        seg_b = min(24 * 60, seg_a + 1)
    for a, b in _ranges_for_window_on_day(window):
        if _ranges_overlap(seg_a, seg_b, a, b):
            return True
    return False


def _window_overlaps_interval(start: datetime, end: datetime, window: DayTimeWindow) -> bool:
    for seg_start, seg_end in _iter_local_segments(start, end):
        if _segment_overlaps_window(seg_start, seg_end, window):
            return True
    return False


def meter_schedule_covers_interval(
    regulations: list[Regulation],
    start: datetime,
    end: datetime,
    *,
    vehicle_category: str = "passenger",
) -> bool:
    """True only when parsed meter windows for the vehicle cover the whole stay."""
    start = _as_nyc(start)
    end = _as_nyc(end)
    intervals: list[tuple[datetime, datetime]] = []
    for reg in regulations:
        if (
            reg.kind != RestrictionKind.METERED
            or reg.parse_status != ParseStatus.PARSED
            or reg.vehicle_category != vehicle_category
        ):
            continue
        for window in reg.windows:
            intervals.extend(_window_occurrences(window, start, end))
    clipped = sorted(
        (max(start, a), min(end, b))
        for a, b in intervals
        if max(start, a) < min(end, b)
    )
    cursor = start
    for a, b in clipped:
        if a > cursor:
            return False
        cursor = max(cursor, b)
        if cursor >= end:
            return True
    return False


def build_rule_findings(
    start: datetime,
    end: datetime,
    regulations: list[Regulation],
    *,
    context: EvaluationContext | None = None,
) -> list[dict[str, Any]]:
    """Produce deterministic, UI-ready rule-window and stay-overlap facts."""
    ctx = context or EvaluationContext()
    start = _as_nyc(start)
    end = _as_nyc(end)
    findings: list[dict[str, Any]] = []
    for reg in regulations:
        overlaps: list[tuple[datetime, datetime]] = []
        overlapping_occurrences: list[tuple[datetime, datetime]] = []
        for window in reg.windows:
            for a, b in _window_occurrences(window, start, end):
                if max(start, a) < min(end, b):
                    overlapping_occurrences.append((a, b))
                    overlaps.append((max(start, a), min(end, b)))
        overlaps.sort()
        overlapping_occurrences.sort()
        findings.append(
            {
                "kind": reg.kind.value,
                "parse_status": reg.parse_status.value,
                "raw_description": reg.raw_description,
                "source_id": reg.source_id,
                "source_url": reg.source_url,
                "vehicle_category": reg.vehicle_category,
                "applies_to_requested_vehicle": (
                    reg.vehicle_category in (None, ctx.vehicle_category)
                ),
                "scope_certainty": reg.scope_certainty.value,
                "scope_verified": _regulation_scope_verified(reg, ctx),
                "max_duration_minutes": reg.max_duration_minutes,
                "windows": [
                    {
                        "days": sorted(window.days),
                        "start_minute": window.start_minute,
                        "end_minute": window.end_minute,
                        "anytime": window.anytime,
                    }
                    for window in reg.windows
                ],
                "overlaps_stay": bool(overlaps),
                "overlaps": [
                    {"start": a.isoformat(), "end": b.isoformat()} for a, b in overlaps
                ],
                "known_restriction_ends": (
                    overlapping_occurrences[-1][1].isoformat()
                    if overlapping_occurrences
                    else None
                ),
                "parse_limitation": reg.parse_limitation,
            }
        )
    return findings


def _window_occurrences(
    window: DayTimeWindow,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Expand one recurring local window around an interval into datetimes."""
    occurrences: list[tuple[datetime, datetime]] = []
    day = start.date() - timedelta(days=1)
    final_day = end.date()
    while day <= final_day:
        if day.weekday() in window.days:
            day_start = datetime.combine(day, time(0, 0), tzinfo=NYC_TZ)
            if window.anytime:
                a, b = day_start, day_start + timedelta(days=1)
            else:
                a = day_start + timedelta(minutes=window.start_minute)
                if window.end_minute > window.start_minute:
                    b = day_start + timedelta(minutes=window.end_minute)
                elif window.end_minute < window.start_minute:
                    b = day_start + timedelta(days=1, minutes=window.end_minute)
                else:
                    b = a
            if a < end and b > start:
                occurrences.append((a, b))
        day += timedelta(days=1)
    return occurrences


def _ranges_overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
    return a0 < b1 and b0 < a1


def _meter_violation_for_segment(
    seg_start: datetime,
    seg_end: datetime,
    stay_start: datetime,
    stay_end: datetime,
    reg: Regulation,
) -> str | None:
    """Return violation message if metered max duration is exceeded during enforcement.

    Policy: while the vehicle remains parked, continuous time that falls inside
    meter enforcement windows must not exceed max_duration_minutes. Time outside
    enforcement hours does not count toward the limit, but a stay that is entirely
    outside enforcement is not a meter violation.
    """
    if not reg.max_duration_minutes:
        return (
            f"Meter regulation {reg.source_id} has no parseable max duration."
            if reg.parse_status == ParseStatus.PARSED
            else None
        )

    # Sum overlapping enforcement minutes across the whole stay for this meter pattern.
    enforced = 0
    for s, e in _iter_local_segments(stay_start, stay_end):
        for window in reg.windows:
            enforced += _overlap_minutes(s, e, window)
    if enforced <= 0:
        return None
    if enforced > reg.max_duration_minutes:
        return (
            f"Meter max {reg.max_duration_minutes} minutes exceeded: "
            f"{enforced} enforced minutes during {reg.raw_description!r}"
        )
    # Also: if the stay overlaps enforcement and duration of the full stay within
    # a single contiguous enforcement span exceeds max — handled by sum above for
    # typical daytime meters. For multi-day stays, summing all enforced minutes is
    # conservative (stricter), which matches our conservative policy.
    return None


def _overlap_minutes(seg_start: datetime, seg_end: datetime, window: DayTimeWindow) -> int:
    if not _segment_overlaps_window(seg_start, seg_end, window):
        # Check overnight morning remnant path already inside _segment_overlaps_window;
        # still compute minutes carefully.
        day = seg_start.date()
        if window.anytime or window.end_minute > window.start_minute:
            return 0
        prev = day - timedelta(days=1)
        if prev.weekday() not in window.days:
            return 0
        seg_a = _minute_of(seg_start)
        seg_b = _minute_of(seg_end) if seg_end.date() == day else 24 * 60
        return max(0, min(seg_b, window.end_minute) - max(seg_a, 0))

    day = seg_start.date()
    if day.weekday() not in window.days and not (
        not window.anytime and window.end_minute <= window.start_minute
    ):
        return 0
    seg_a = _minute_of(seg_start)
    seg_b = _minute_of(seg_end) if seg_end.date() == day else 24 * 60
    total = 0
    if day.weekday() in window.days:
        for a, b in _ranges_for_window_on_day(window):
            total += max(0, min(seg_b, b) - max(seg_a, a))
    return total


def _is_point_meter(reg: Regulation) -> bool:
    """Dataset meters are point evidence; they do not verify full block-side coverage."""
    return reg.kind == RestrictionKind.METERED and "693u-uax6" in (reg.source_url or "")


def _regulation_geographically_consistent(reg: Regulation) -> bool:
    """True when the reg is eligible for consideration at this candidate.

    STREET_SIDE_MATCHED (metadata True) or no association metadata (stretch / unit tests)
    counts as geographically consistent. Explicit False means unmatched — do not apply.
    """
    meta = reg.metadata or {}
    if "street_side_matched" in meta:
        return bool(meta.get("street_side_matched"))
    return True


def _regulation_scope_verified(reg: Regulation, ctx: EvaluationContext) -> bool:
    """SCOPE_VERIFIED: regulatory extent is proven to cover the candidate location.

    Never true from DOUBLE_ARROW (<->), ARROW_PARTIAL, STREET_SIDE_MATCHED, or proximity.
    True only when metadata.scope_verified is set, or the caller explicitly established
    location scope (tests / future geometry integration).
    """
    if not _regulation_geographically_consistent(reg):
        return False
    meta = reg.metadata or {}
    if meta.get("scope_verified") is True:
        return True
    # Explicit established location (not inferred from arrows).
    if ctx.location_scope_established:
        return True
    return False


def _permission_scope_established(applied: list[Regulation], ctx: EvaluationContext) -> bool:
    """POTENTIALLY_PERMITTED requires proven curb applicability — never arrow cues alone."""
    return bool(ctx.location_scope_established)


def _location_scope_ok(applied: list[Regulation], ctx: EvaluationContext) -> tuple[bool, list[str]]:
    notes: list[str] = []
    if ctx.location_scope_established:
        return True, notes

    curb_regs = [
        r
        for r in applied
        if (r.kind in _PROHIBITING or r.kind == RestrictionKind.METERED) and not _is_point_meter(r)
        and _regulation_geographically_consistent(r)
    ]
    if not curb_regs:
        notes.append("No parsed curb-sign regulations available; geographic applicability unverified.")
        return False, notes

    notes.append(
        "Sign arrows (including <-> DOUBLE_ARROW), STREET_SIDE_MATCHED, and proximity "
        "do not verify curb-zone boundaries or meter applicability (not SCOPE_VERIFIED)."
    )
    return False, notes


def _infer_scope_established(regs: list[Regulation]) -> bool:
    """Never infer SCOPE_VERIFIED from plate arrows or street/side match."""
    return False


# Re-export parsers for convenience.
__all__ = [
    "NYC_TZ",
    "evaluate_parking_interval",
    "evaluate_candidate",
    "parse_sign_record",
    "parse_meter_record",
]
