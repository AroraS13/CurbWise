"""Conservative deterministic parsers for a supported subset of NYC sign/meter text."""

from __future__ import annotations

import re
from typing import Any

from .models import (
    ALL_DAYS,
    MON_FRI,
    MON_SAT,
    ArrowIndicator,
    DayTimeWindow,
    ParseStatus,
    Regulation,
    RestrictionKind,
    ScopeCertainty,
)

# Strip trailing supersession notes commonly present in the signs dataset.
_SUPERSEDES = re.compile(r"\s*\(SUPERSEDES[^)]*\)\s*$", re.I)

# Arrow glyphs appearing in sign_description text.
_ARROW_BOTH = "<->"
_ARROW_SINGLE = "-->"

# Clock times: 9AM, 9:30AM, 12PM, 8:30AM
_CLOCK = re.compile(
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>AM|PM)",
    re.I,
)
# Military meter times: 0800, 0830, 2200
_MILITARY = re.compile(r"\b(?P<h>\d{2})(?P<m>\d{2})\b")

_DAYS_MON_FRI = re.compile(r"\bMONDAY\s*-\s*FRIDAY\b|\bMON-FRI\b", re.I)
_DAYS_MON_SAT = re.compile(r"\bMONDAY\s*-\s*SATURDAY\b|\bMON-SAT\b", re.I)
_EXCEPT_SUNDAY = re.compile(r"\bEXCEPT\s+SUNDAY\b", re.I)
_ANYTIME = re.compile(r"\bANYTIME\b", re.I)
_DAY_NAME = re.compile(
    r"\b(MONDAYS?|TUESDAYS?|WEDNESDAYS?|THURSDAYS?|FRIDAYS?|SATURDAYS?|SUNDAYS?|"
    r"MON|TUE|TUES|WED|THU|THUR|THURS|FRI|SAT|SUN)\b",
    re.I,
)
_SUPPORTED_DAY_PHRASE = re.compile(
    r"\bEXCEPT\s+SUNDAY\b|"
    r"\bMONDAY\s*-\s*FRIDAY\b|\bMON-FRI\b|"
    r"\bMONDAY\s*-\s*SATURDAY\b|\bMON-SAT\b|"
    # Explicit lists (not ranges): MONDAY THURSDAY means Mon and Thu only.
    r"\bMONDAY\s+THURSDAY\b|\bTUESDAY\s+FRIDAY\b|"
    r"\bMONDAY\s+WEDNESDAY\s+FRIDAY\b|\bTUESDAY\s+THURSDAY\s+SATURDAY\b|"
    r"\bMONDAYS?\s+AND\s+THURSDAYS?\b|\bTUESDAYS?\s+AND\s+FRIDAYS?\b",
    re.I,
)

_DAY_TOKEN = {
    "MON": 0,
    "MONDAY": 0,
    "MONDAYS": 0,
    "TUE": 1,
    "TUES": 1,
    "TUESDAY": 1,
    "TUESDAYS": 1,
    "WED": 2,
    "WEDNESDAY": 2,
    "WEDNESDAYS": 2,
    "THU": 3,
    "THUR": 3,
    "THURS": 3,
    "THURSDAY": 3,
    "THURSDAYS": 3,
    "FRI": 4,
    "FRIDAY": 4,
    "FRIDAYS": 4,
    "SAT": 5,
    "SATURDAY": 5,
    "SATURDAYS": 5,
    "SUN": 6,
    "SUNDAY": 6,
    "SUNDAYS": 6,
}

# Supported curb-rule patterns (after supersedes strip / uppercasing helpers).
_ASP = re.compile(
    r"^NO\s+PARKING\s+\(SANITATION\s+BROOM\s+SYMBOL\)\s+"
    r"(?P<start>\d{1,2}(?::\d{2})?\s*[AP]M)\s*-\s*(?P<end>\d{1,2}(?::\d{2})?\s*[AP]M)"
    r"(?P<rest>.*)$",
    re.I,
)
# e.g. NO PARKING (SANITATION BROOM SYMBOL) MONDAY THURSDAY 11AM-12:30PM
_ASP_DAYS_FIRST = re.compile(
    r"^NO\s+PARKING\s+\(SANITATION\s+BROOM\s+SYMBOL\)\s+"
    r"(?P<days>.+?)\s+"
    r"(?P<start>\d{1,2}(?::\d{2})?\s*[AP]M)\s*-\s*(?P<end>\d{1,2}(?::\d{2})?\s*[AP]M)"
    r"(?P<rest>.*)$",
    re.I,
)
_NO_STANDING = re.compile(r"^NO\s+STANDING\b(?P<rest>.*)$", re.I)
_NO_STOPPING = re.compile(r"^NO\s+STOPPING\b(?P<rest>.*)$", re.I)
_NO_PARKING = re.compile(r"^NO\s+PARKING\b(?P<rest>.*)$", re.I)
_HMP = re.compile(
    r"^(?P<hours>\d+)\s*HMP\b(?P<rest>.*)$",
    re.I,
)
_TRUCK_LOADING = re.compile(
    r"^TRUCK\s*\(SYMBOL\)\s*TRUCK\s+LOADING\s+ONLY\b(?P<rest>.*)$",
    re.I,
)
_BUS_STOP_NO_STANDING = re.compile(
    r"^BUS\s+STOP\s+SIGN\b.*\bNO\s+STANDING\b.*$",
    re.I,
)
_PAY_BY_CELL = re.compile(r"^PAY-BY-CELL\s+LOCATOR\s+NUMBER$", re.I)
_INFO_PANEL = re.compile(
    r"(ROUTE\s+PANEL|DESTINATION\s+PANEL|LOCATION\s+PANEL|BOTTOM\s+LOCATION\s+PANEL)",
    re.I,
)

# Meter dataset: "2HR Pas Mon-Sat 0900-1900" / "6HR Pas Mon-Fri 0800-2200"
_METER = re.compile(
    r"^(?P<hours>\d+)\s*HR\s+Pas\s+(?P<days>Mon-Fri|Mon-Sat)\s+"
    r"(?P<start>\d{4})\s*-\s*(?P<end>\d{4})\s*$",
    re.I,
)
_METER_VEHICLE_SPEC = re.compile(r"(?P<hours>\d+)\s*HR\s+(?P<vehicle>Pas|Com)\b", re.I)
_METER_TIME_RANGE = re.compile(
    r"(?P<start>\d{4})\s*-\s*(?P<end>\d{4})\s*$",
    re.I,
)


def parse_clock_to_minute(text: str) -> int:
    """Parse '9AM', '9:30AM', '7PM' -> minutes from midnight."""
    m = _CLOCK.fullmatch(text.strip())
    if not m:
        raise ValueError(f"Unrecognized clock time: {text!r}")
    hour = int(m.group("h"))
    minute = int(m.group("m") or 0)
    ampm = m.group("ampm").upper()
    if hour < 1 or hour > 12 or minute > 59:
        raise ValueError(f"Invalid clock time: {text!r}")
    if ampm == "AM":
        hour = 0 if hour == 12 else hour
    else:
        hour = 12 if hour == 12 else hour + 12
    return hour * 60 + minute


def parse_military_to_minute(text: str) -> int:
    m = _MILITARY.fullmatch(text.strip())
    if not m:
        raise ValueError(f"Unrecognized military time: {text!r}")
    hour = int(m.group("h"))
    minute = int(m.group("m"))
    if hour > 23 or minute > 59:
        raise ValueError(f"Invalid military time: {text!r}")
    return hour * 60 + minute


def _arrow_and_scope(text: str, arrow_direction: str | None) -> tuple[ArrowIndicator, ScopeCertainty]:
    """Separate plate arrow evidence from verified geographic applicability.

    <-> is a DOUBLE_ARROW plate cue only — never SCOPE_VERIFIED curb bounds.
    Missing arrow must NOT be inferred as full-block coverage.
    """
    if _ARROW_BOTH in text:
        return ArrowIndicator.BOTH, ScopeCertainty.DOUBLE_ARROW
    if _ARROW_SINGLE in text or (arrow_direction and str(arrow_direction).strip()):
        return ArrowIndicator.SINGLE, ScopeCertainty.ARROW_PARTIAL
    return ArrowIndicator.UNKNOWN, ScopeCertainty.UNKNOWN


def _days_from_rest(rest: str, *, default: frozenset[int]) -> frozenset[int]:
    parsed = _parse_day_set(rest)
    if parsed is not None:
        return parsed
    return default


def _parse_day_set(text: str) -> frozenset[int] | None:
    """Parse supported day phrases. Never treat 'MONDAY THURSDAY' as Mon–Thu inclusive."""
    if not text:
        return None
    if _EXCEPT_SUNDAY.search(text):
        return MON_SAT
    if _DAYS_MON_FRI.search(text):
        return MON_FRI
    if _DAYS_MON_SAT.search(text):
        return MON_SAT
    # Explicit common ASP pairs / triples (space or AND separated lists).
    upper = text.upper()
    if re.search(r"\bMONDAYS?\s+(?:AND\s+)?THURSDAYS?\b", upper):
        return frozenset({0, 3})
    if re.search(r"\bTUESDAYS?\s+(?:AND\s+)?FRIDAYS?\b", upper):
        return frozenset({1, 4})
    if re.search(r"\bMONDAYS?\s+WEDNESDAYS?\s+(?:AND\s+)?FRIDAYS?\b", upper):
        return frozenset({0, 2, 4})
    if re.search(r"\bTUESDAYS?\s+THURSDAYS?\s+(?:AND\s+)?SATURDAYS?\b", upper):
        return frozenset({1, 3, 5})
    # Generic explicit list only when every day token is isolated (no hyphen ranges beyond supported).
    if "-" in text and not (_DAYS_MON_FRI.search(text) or _DAYS_MON_SAT.search(text)):
        return None
    tokens = [t.upper() for t in _DAY_NAME.findall(text)]
    if not tokens:
        return None
    days = {_DAY_TOKEN[t] for t in tokens if t in _DAY_TOKEN}
    # Require that the phrase matches a supported pattern or is a pure list we recognize above.
    if _SUPPORTED_DAY_PHRASE.search(text) and days:
        return frozenset(days)
    return None


def _has_unsupported_day_phrase(rest: str) -> bool:
    """True when day names appear but not in an explicitly supported phrase."""
    if not _DAY_NAME.search(rest):
        return False
    if _parse_day_set(rest) is not None:
        return False
    return True


def _time_range_from_rest(rest: str) -> tuple[int, int] | None:
    times = list(_CLOCK.finditer(rest))
    if len(times) < 2:
        return None
    start = parse_clock_to_minute(times[0].group(0))
    end = parse_clock_to_minute(times[1].group(0))
    return start, end


def _clean_description(raw: str) -> str:
    return _SUPERSEDES.sub("", (raw or "").strip()).strip()


def parse_sign_description(
    raw_description: str,
    *,
    source_id: str,
    source_url: str,
    arrow_direction: str | None = None,
    sign_code: str | None = None,
) -> Regulation:
    """Parse one sign description into a Regulation. Never invents unsupported meanings."""
    raw = (raw_description or "").strip()
    text = _clean_description(raw)
    arrow_indicator, scope = _arrow_and_scope(text, arrow_direction)
    base_kwargs = dict(
        raw_description=raw,
        source_id=source_id or "",
        source_url=source_url or "",
        arrow_direction=arrow_direction,
        arrow_indicator=arrow_indicator,
        scope_certainty=scope,
        sign_code=sign_code,
    )

    if not text:
        return Regulation(
            kind=RestrictionKind.UNKNOWN,
            parse_status=ParseStatus.UNPARSED,
            parse_limitation="Empty sign description.",
            **base_kwargs,
        )

    if _PAY_BY_CELL.fullmatch(text):
        return Regulation(
            kind=RestrictionKind.INFORMATIONAL,
            parse_status=ParseStatus.INFORMATIONAL,
            parse_limitation="Pay-by-cell locator is not itself a parking restriction.",
            **base_kwargs,
        )

    if _INFO_PANEL.search(text):
        return Regulation(
            kind=RestrictionKind.INFORMATIONAL,
            parse_status=ParseStatus.INFORMATIONAL,
            parse_limitation="Informational bus/location panel; not treated as a curb parking rule.",
            **base_kwargs,
        )

    if _BUS_STOP_NO_STANDING.match(text):
        base_kwargs = {
            **base_kwargs,
            "arrow_indicator": ArrowIndicator.SINGLE
            if base_kwargs["arrow_indicator"] == ArrowIndicator.UNKNOWN
            else base_kwargs["arrow_indicator"],
            "scope_certainty": ScopeCertainty.ARROW_PARTIAL,
        }
        return Regulation(
            kind=RestrictionKind.NO_STANDING,
            parse_status=ParseStatus.PARSED,
            windows=[DayTimeWindow(days=ALL_DAYS, anytime=True)],
            parse_limitation=(
                "Bus-stop no-standing parsed as anytime no standing; "
                "exact bus-stop length along the curb is not established from this text alone "
                "and must not be projected onto an entire block side."
            ),
            **base_kwargs,
        )

    m = _ASP.match(text)
    if m:
        try:
            start = parse_clock_to_minute(m.group("start"))
            end = parse_clock_to_minute(m.group("end"))
        except ValueError as e:
            return Regulation(
                kind=RestrictionKind.UNKNOWN,
                parse_status=ParseStatus.UNPARSED,
                parse_limitation=f"ASP broom sign has unparseable times: {e}",
                **base_kwargs,
            )
        rest = m.group("rest")
        if _has_unsupported_day_phrase(rest):
            return Regulation(
                kind=RestrictionKind.UNKNOWN,
                parse_status=ParseStatus.UNPARSED,
                parse_limitation=(
                    "ASP broom sign has day-of-week phrasing outside the supported subset "
                    "(EXCEPT SUNDAY / MONDAY-FRIDAY / MONDAY-SATURDAY)."
                ),
                **base_kwargs,
            )
        days = _days_from_rest(rest, default=MON_SAT)
        return Regulation(
            kind=RestrictionKind.ASP_STREET_CLEANING,
            parse_status=ParseStatus.PARSED,
            windows=[DayTimeWindow(days=days, start_minute=start, end_minute=end)],
            **base_kwargs,
        )

    m = _ASP_DAYS_FIRST.match(text)
    if m and "SANITATION BROOM" in text.upper():
        days_text = m.group("days")
        days = _parse_day_set(days_text)
        if days is None or _has_unsupported_day_phrase(days_text):
            return Regulation(
                kind=RestrictionKind.UNKNOWN,
                parse_status=ParseStatus.UNPARSED,
                parse_limitation=(
                    "ASP broom sign day list is outside the supported subset. "
                    "Ambiguous ranges are not inferred (e.g. lists are not treated as inclusive spans)."
                ),
                **base_kwargs,
            )
        try:
            start = parse_clock_to_minute(m.group("start"))
            end = parse_clock_to_minute(m.group("end"))
        except ValueError as e:
            return Regulation(
                kind=RestrictionKind.UNKNOWN,
                parse_status=ParseStatus.UNPARSED,
                parse_limitation=f"ASP broom sign has unparseable times: {e}",
                **base_kwargs,
            )
        return Regulation(
            kind=RestrictionKind.ASP_STREET_CLEANING,
            parse_status=ParseStatus.PARSED,
            windows=[DayTimeWindow(days=days, start_minute=start, end_minute=end)],
            **base_kwargs,
        )

    # Broom signs that do not match supported patterns must not fall through
    # to generic NO PARKING (which would over-apply day/time defaults).
    if "SANITATION BROOM" in text.upper():
        return Regulation(
            kind=RestrictionKind.UNKNOWN,
            parse_status=ParseStatus.UNPARSED,
            parse_limitation=(
                "Sanitation broom / ASP sign text is outside the supported parser subset."
            ),
            **base_kwargs,
        )

    m = _TRUCK_LOADING.match(text)
    if m:
        rest = m.group("rest")
        days = _days_from_rest(rest, default=MON_FRI)
        tr = _time_range_from_rest(rest)
        if not tr:
            return Regulation(
                kind=RestrictionKind.UNKNOWN,
                parse_status=ParseStatus.UNPARSED,
                parse_limitation="Truck loading sign recognized but times could not be parsed.",
                **base_kwargs,
            )
        # For passenger MVP: loading-only periods prohibit passenger parking/standing.
        return Regulation(
            kind=RestrictionKind.NO_STANDING,
            parse_status=ParseStatus.PARSED,
            windows=[DayTimeWindow(days=days, start_minute=tr[0], end_minute=tr[1])],
            vehicle_category="passenger",
            parse_limitation="Parsed as passenger prohibition during truck-loading-only hours.",
            metadata={"subtype": "truck_loading_only"},
            **base_kwargs,
        )

    m = _HMP.match(text)
    if m:
        hours = int(m.group("hours"))
        rest = m.group("rest")
        days = _days_from_rest(rest, default=MON_SAT)
        tr = _time_range_from_rest(rest)
        if not tr:
            return Regulation(
                kind=RestrictionKind.UNKNOWN,
                parse_status=ParseStatus.UNPARSED,
                parse_limitation="HMP sign recognized but times could not be parsed.",
                **base_kwargs,
            )
        return Regulation(
            kind=RestrictionKind.METERED,
            parse_status=ParseStatus.PARSED,
            windows=[DayTimeWindow(days=days, start_minute=tr[0], end_minute=tr[1])],
            max_duration_minutes=hours * 60,
            vehicle_category="passenger",
            **base_kwargs,
        )

    m = _NO_STOPPING.match(text)
    if m:
        return _parse_no_rule(RestrictionKind.NO_STOPPING, m.group("rest"), base_kwargs)

    m = _NO_STANDING.match(text)
    if m:
        return _parse_no_rule(RestrictionKind.NO_STANDING, m.group("rest"), base_kwargs)

    m = _NO_PARKING.match(text)
    if m:
        # Non-broom NO PARKING (broom already handled).
        return _parse_no_rule(RestrictionKind.NO_PARKING, m.group("rest"), base_kwargs)

    return Regulation(
        kind=RestrictionKind.UNKNOWN,
        parse_status=ParseStatus.UNPARSED,
        parse_limitation="Sign description is outside the explicitly supported parser subset.",
        **base_kwargs,
    )


def _parse_no_rule(kind: RestrictionKind, rest: str, base_kwargs: dict) -> Regulation:
    def _unparsed(msg: str) -> Regulation:
        return Regulation(
            kind=RestrictionKind.UNKNOWN,
            parse_status=ParseStatus.UNPARSED,
            parse_limitation=msg,
            **base_kwargs,
        )

    if _has_unsupported_day_phrase(rest):
        return _unparsed(
            f"{kind.value} sign has day-of-week phrasing outside the supported subset; "
            "refusing to default to all days."
        )

    if _ANYTIME.search(rest) or not list(_CLOCK.finditer(rest)):
        if _ANYTIME.search(rest) or not rest.strip() or rest.strip() in {"<->", "-->"}:
            return Regulation(
                kind=kind,
                parse_status=ParseStatus.PARSED,
                windows=[DayTimeWindow(days=ALL_DAYS, anytime=True)],
                **base_kwargs,
            )
        # Has extra unrecognized qualifiers without anytime/times.
        if not _ANYTIME.search(rest) and not list(_CLOCK.finditer(rest)):
            cleaned = re.sub(r"(<->|-->)", "", rest).strip()
            if not cleaned:
                return Regulation(
                    kind=kind,
                    parse_status=ParseStatus.PARSED,
                    windows=[DayTimeWindow(days=ALL_DAYS, anytime=True)],
                    **base_kwargs,
                )
            return _unparsed(f"{kind.value} sign has unsupported qualifiers: {rest.strip()!r}")

    days = _days_from_rest(rest, default=ALL_DAYS)
    tr = _time_range_from_rest(rest)
    if not tr:
        return _unparsed(f"{kind.value} sign times could not be parsed.")
    return Regulation(
        kind=kind,
        parse_status=ParseStatus.PARSED,
        windows=[DayTimeWindow(days=days, start_minute=tr[0], end_minute=tr[1])],
        **base_kwargs,
    )


def parse_meter_hours(
    meter_hours: str,
    *,
    source_id: str,
    source_url: str,
    status: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Regulation:
    """Parse a meter_hours string from dataset 693u-uax6."""
    raw = (meter_hours or "").strip()
    base = dict(
        raw_description=raw,
        source_id=source_id or "",
        source_url=source_url or "",
        # A meter record is point evidence; it does not verify full block-side coverage.
        arrow_indicator=ArrowIndicator.UNKNOWN,
        scope_certainty=ScopeCertainty.UNKNOWN,
        parse_limitation="Meter record applies at the meter coordinates; full block-side coverage is not verified.",
        metadata={"meter_status": status, **(metadata or {})},
    )
    if status and status.strip().lower() not in {"active", ""}:
        base["parse_limitation"] = (
            f"Meter status {status!r} is not Active; not treated as usable metered evidence."
        )
        return Regulation(kind=RestrictionKind.METERED, parse_status=ParseStatus.UNPARSED, **base)
    m = _METER.fullmatch(raw)
    if not m:
        base["parse_limitation"] = (
            "Meter hours string is outside the supported 'NHR Pas Mon-Fri/Sat HHMM-HHMM' subset."
        )
        return Regulation(kind=RestrictionKind.UNKNOWN, parse_status=ParseStatus.UNPARSED, **base)
    days = MON_FRI if m.group("days").lower() == "mon-fri" else MON_SAT
    try:
        start = parse_military_to_minute(m.group("start"))
        end = parse_military_to_minute(m.group("end"))
    except ValueError as e:
        base["parse_limitation"] = f"Meter times unparseable: {e}"
        return Regulation(kind=RestrictionKind.UNKNOWN, parse_status=ParseStatus.UNPARSED, **base)
    return Regulation(
        kind=RestrictionKind.METERED,
        parse_status=ParseStatus.PARSED,
        windows=[DayTimeWindow(days=days, start_minute=start, end_minute=end)],
        max_duration_minutes=int(m.group("hours")) * 60,
        vehicle_category="passenger",
        **base,
    )


def parse_meter_schedule_record(meter: dict[str, Any]) -> list[Regulation]:
    """Parse every slash-delimited meter schedule component conservatively."""
    raw = (meter.get("meter_hours") or "").strip()
    source_id = str(meter.get("meter_number") or meter.get("objectid") or "")
    source_url = meter.get("source") or "https://data.cityofnewyork.us/d/693u-uax6"
    status = meter.get("status")
    metadata = {
        "meter_status": status,
        "pay_by_cell_number": meter.get("pay_by_cell_number"),
        "on_street": meter.get("on_street"),
        "side_of_street": meter.get("side_of_street"),
        "original_meter_hours": raw,
    }
    if status and str(status).strip().lower() not in {"active", ""}:
        return [
            Regulation(
                kind=RestrictionKind.METERED,
                raw_description=raw,
                source_id=source_id,
                source_url=source_url,
                parse_status=ParseStatus.UNPARSED,
                parse_limitation=(
                    f"Meter status {status!r} is not Active; not treated as usable metered evidence."
                ),
                metadata=metadata,
            )
        ]
    if not raw:
        return [
            Regulation(
                kind=RestrictionKind.UNKNOWN,
                raw_description=raw,
                source_id=source_id,
                source_url=source_url,
                parse_status=ParseStatus.UNPARSED,
                parse_limitation="Meter schedule is empty.",
                metadata=metadata,
            )
        ]

    regulations: list[Regulation] = []
    for index, component in enumerate(part.strip() for part in raw.split("/") if part.strip()):
        specs = list(_METER_VEHICLE_SPEC.finditer(component))
        time_match = _METER_TIME_RANGE.search(component)
        if not specs or not time_match or specs[-1].end() > time_match.start():
            regulations.append(
                _unparsed_meter_component(
                    component,
                    source_id,
                    source_url,
                    metadata,
                    index,
                    "Unsupported meter component; expected vehicle duration, days, and HHMM-HHMM.",
                )
            )
            continue
        days_text = component[specs[-1].end() : time_match.start()].strip(" ,")
        days = _parse_meter_days(days_text)
        try:
            start = _parse_meter_boundary(time_match.group("start"), is_end=False)
            end = _parse_meter_boundary(time_match.group("end"), is_end=True)
        except ValueError as exc:
            regulations.append(
                _unparsed_meter_component(
                    component,
                    source_id,
                    source_url,
                    metadata,
                    index,
                    f"Meter times unparseable: {exc}",
                )
            )
            continue
        if days is None:
            regulations.append(
                _unparsed_meter_component(
                    component,
                    source_id,
                    source_url,
                    metadata,
                    index,
                    f"Unsupported meter day expression: {days_text!r}.",
                )
            )
            continue
        for spec in specs:
            vehicle = "passenger" if spec.group("vehicle").lower() == "pas" else "commercial"
            regulations.append(
                Regulation(
                    kind=RestrictionKind.METERED,
                    raw_description=component,
                    source_id=source_id,
                    source_url=source_url,
                    parse_status=ParseStatus.PARSED,
                    windows=[
                        DayTimeWindow(
                            days=days,
                            start_minute=start,
                            end_minute=end,
                        )
                    ],
                    max_duration_minutes=int(spec.group("hours")) * 60,
                    vehicle_category=vehicle,
                    arrow_indicator=ArrowIndicator.UNKNOWN,
                    scope_certainty=ScopeCertainty.UNKNOWN,
                    parse_limitation=(
                        "Meter record applies at associated meter coordinates; "
                        "full street-side coverage is not verified."
                    ),
                    metadata={
                        **metadata,
                        "schedule_component_index": index,
                        "schedule_days_text": days_text,
                    },
                )
            )
    return regulations or [
        _unparsed_meter_component(
            raw,
            source_id,
            source_url,
            metadata,
            0,
            "No meter schedule components were found.",
        )
    ]


def _unparsed_meter_component(
    component: str,
    source_id: str,
    source_url: str,
    metadata: dict[str, Any],
    index: int,
    limitation: str,
) -> Regulation:
    return Regulation(
        kind=RestrictionKind.UNKNOWN,
        raw_description=component,
        source_id=source_id,
        source_url=source_url,
        parse_status=ParseStatus.UNPARSED,
        parse_limitation=limitation,
        metadata={**metadata, "schedule_component_index": index},
    )


def _parse_meter_boundary(value: str, *, is_end: bool) -> int:
    if value == "2400" and is_end:
        return 24 * 60
    return parse_military_to_minute(value)


def _parse_meter_days(text: str) -> frozenset[int] | None:
    normalized = re.sub(r"[,&]+", " ", text.upper())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in {"ALL DAYS", "DAILY", "MON-SUN"}:
        return ALL_DAYS
    if normalized == "MON-FRI":
        return MON_FRI
    if normalized == "MON-SAT":
        return MON_SAT
    if normalized == "SAT-SUN":
        return frozenset({5, 6})
    tokens = normalized.split()
    if not tokens:
        return None
    days: set[int] = set()
    for token in tokens:
        mapped = _DAY_TOKEN.get(token)
        if mapped is None:
            return None
        days.add(mapped)
    return frozenset(days) if days else None


def parse_sign_record(sign: dict[str, Any]) -> Regulation:
    reg = parse_sign_description(
        sign.get("sign_description") or "",
        source_id=str(sign.get("order_number") or sign.get("sign_code") or ""),
        source_url=sign.get("source") or "https://data.cityofnewyork.us/d/nfid-uabd",
        arrow_direction=sign.get("arrow_direction"),
        sign_code=sign.get("sign_code"),
    )
    # Preserve association flags from locations.associate_signs_to_meter.
    # STREET_SIDE_MATCHED ≠ SCOPE_VERIFIED.
    meta = dict(reg.metadata or {})
    if "street_side_matched" in sign:
        meta["street_side_matched"] = bool(sign.get("street_side_matched"))
    if "scope_verified" in sign:
        meta["scope_verified"] = bool(sign.get("scope_verified"))
    if sign.get("association"):
        meta["association"] = sign.get("association")
    if sign.get("association_distance_m") is not None:
        meta["association_distance_m"] = sign.get("association_distance_m")
    if sign.get("street_side_match_reason"):
        meta["street_side_match_reason"] = sign.get("street_side_match_reason")
    for key in ("on_street", "side_of_street", "from_street", "to_street"):
        if sign.get(key) is not None:
            meta[key] = sign.get(key)
    reg.metadata = meta
    return reg


def parse_meter_record(meter: dict[str, Any]) -> Regulation:
    regs = parse_meter_schedule_record(meter)
    if len(regs) == 1:
        return regs[0]
    return Regulation(
        kind=RestrictionKind.UNKNOWN,
        raw_description=meter.get("meter_hours") or "",
        source_id=str(meter.get("meter_number") or meter.get("objectid") or ""),
        source_url=meter.get("source") or "https://data.cityofnewyork.us/d/693u-uax6",
        parse_status=ParseStatus.UNPARSED,
        parse_limitation=(
            "Meter has multiple schedule components; consumers must evaluate "
            "parse_meter_schedule_record() results by vehicle and interval."
        ),
        metadata={
            "meter_status": meter.get("status"),
            "schedule_components": [reg.to_dict() for reg in regs],
        },
    )


def parse_candidate_regulations(candidate: dict[str, Any]) -> list[Regulation]:
    regs: list[Regulation] = []
    for sign in candidate.get("signs") or []:
        regs.append(parse_sign_record(sign))
    for meter in candidate.get("meters") or []:
        regs.extend(parse_meter_schedule_record(meter))
    return regs
