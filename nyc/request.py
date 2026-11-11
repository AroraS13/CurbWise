"""Normalize CurbWise parking research requests (America/New_York)."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .parking_rules import NYC_TZ

# Search radius bounds (meters). Frontend offers a subset; API accepts any finite value in range.
RADIUS_M_MIN = 100.0
RADIUS_M_MAX = 1500.0
RADIUS_M_DEFAULT = 500.0


class InvalidRadiusError(ValueError):
    """Raised when radius_m is missing, non-finite, or outside [100, 1500] meters."""


def validate_radius_m(radius_m: Any, *, default_if_none: bool = True) -> float:
    """Accept a finite radius in [100, 1500] meters; reject otherwise (no silent clamp)."""
    if radius_m is None:
        if default_if_none:
            return RADIUS_M_DEFAULT
        raise InvalidRadiusError("radius_m is required")
    try:
        value = float(radius_m)
    except (TypeError, ValueError) as exc:
        raise InvalidRadiusError("radius_m must be a finite number of meters") from exc
    if not math.isfinite(value):
        raise InvalidRadiusError("radius_m must be a finite number of meters")
    if value < RADIUS_M_MIN or value > RADIUS_M_MAX:
        raise InvalidRadiusError(
            f"radius_m must be between {RADIUS_M_MIN:g} and {RADIUS_M_MAX:g} meters (got {value:g})"
        )
    return value

_DATE = re.compile(r"\b(?P<y>20\d{2})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\b")
_DATE_LONG = re.compile(
    r"\b(?P<month>January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>20\d{2}))?\b",
    re.I,
)
_TIME = re.compile(r"\b(?P<h>\d{1,2})(?::(?P<min>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)\b", re.I)
_TIME_RANGE = re.compile(
    r"\bfrom\s+(?P<t1>\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))\s+"
    r"(?:to|-)\s+(?P<t2>\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))\b",
    re.I,
)
_DURATION_HOURS = re.compile(r"\b(?P<n>\d+(?:\.\d+)?)\s*(?:hour|hours|hr|hrs)\b", re.I)
_NEAR = re.compile(r"\b(?:near|at|around|by)\s+(?P<place>.+?)(?:\s+on\s+|\s+from\s+|\s+for\s+|[?.!]|$)", re.I)

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


@dataclass
class ParkingRequest:
    destination: str | None = None
    arrival: datetime | None = None
    departure: datetime | None = None
    duration_minutes: int | None = None
    vehicle_category: str = "passenger"
    radius_m: float = RADIUS_M_DEFAULT
    max_candidates: int = 18
    raw_question: str = ""
    missing_fields: list[str] = field(default_factory=list)
    clarification_questions: list[str] = field(default_factory=list)
    parse_notes: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields and self.arrival is not None and self.departure is not None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["arrival"] = self.arrival.isoformat() if self.arrival else None
        d["departure"] = self.departure.isoformat() if self.departure else None
        d["timezone"] = "America/New_York"
        d["is_complete"] = self.is_complete
        return d


def normalize_parking_request(
    question: str,
    *,
    destination: str | None = None,
    arrival_date: str | None = None,
    arrival_time: str | None = None,
    duration_hours: float | None = None,
    duration_minutes: int | None = None,
    departure_time: str | None = None,
    radius_m: float | None = None,
    max_candidates: int | None = None,
    vehicle_category: str = "passenger",
) -> ParkingRequest:
    """Build a structured request. Never invents missing date/time/duration."""
    q = (question or "").strip()
    req = ParkingRequest(
        raw_question=q,
        vehicle_category=(vehicle_category or "passenger").strip().lower() or "passenger",
        radius_m=validate_radius_m(radius_m),
        max_candidates=int(max_candidates) if max_candidates is not None else 18,
    )

    dest = (destination or "").strip() or _extract_destination(q)
    if dest:
        req.destination = dest
    else:
        req.missing_fields.append("destination")
        req.clarification_questions.append(
            "What NYC destination or street address should I research near?"
        )

    arrival_date = (arrival_date or "").strip() or None
    arrival_time = (arrival_time or "").strip() or None
    parsed_date = _parse_date(arrival_date) if arrival_date else _extract_date(q)
    parsed_time = _parse_clock(arrival_time) if arrival_time else None

    range_times = _extract_time_range(q)
    if parsed_time is None and range_times:
        parsed_time = range_times[0]
        req.parse_notes.append("Arrival time taken from 'from … to …' phrase in the question.")

    if parsed_date is None:
        req.missing_fields.append("arrival_date")
        req.clarification_questions.append(
            "What arrival date should I use (YYYY-MM-DD, America/New_York)?"
        )
    if parsed_time is None:
        req.missing_fields.append("arrival_time")
        req.clarification_questions.append(
            "What arrival time should I use (e.g. 10:00 AM, America/New_York)?"
        )

    dur_min = duration_minutes
    if dur_min is None and duration_hours is not None:
        dur_min = int(round(float(duration_hours) * 60))
    if dur_min is None and range_times and parsed_date is not None:
        # Duration from explicit from-to on the same date.
        start_m, end_m = range_times
        if end_m > start_m:
            dur_min = end_m - start_m
            req.parse_notes.append("Duration derived from the stated from/to times.")
    if dur_min is None:
        extracted = _DURATION_HOURS.search(q)
        if extracted:
            dur_min = int(round(float(extracted.group("n")) * 60))
            req.parse_notes.append("Duration parsed from hour phrasing in the question.")
    if dur_min is None and departure_time and parsed_date and parsed_time is not None:
        dep = _parse_clock(departure_time)
        if dep is not None and dep > parsed_time:
            dur_min = dep - parsed_time

    if dur_min is None or dur_min <= 0:
        req.missing_fields.append("duration")
        req.clarification_questions.append(
            "How long do you need to park (duration in hours/minutes, or a departure time)?"
        )

    if parsed_date is not None and parsed_time is not None and dur_min and dur_min > 0:
        arrival = datetime(
            parsed_date[0],
            parsed_date[1],
            parsed_date[2],
            parsed_time // 60,
            parsed_time % 60,
            tzinfo=NYC_TZ,
        )
        req.arrival = arrival
        req.duration_minutes = int(dur_min)
        req.departure = arrival + timedelta(minutes=int(dur_min))

    # De-dupe missing/clarifications while preserving order
    req.missing_fields = list(dict.fromkeys(req.missing_fields))
    req.clarification_questions = list(dict.fromkeys(req.clarification_questions))
    return req


def clarification_message(req: ParkingRequest) -> str:
    lines = [
        "I need a bit more detail before researching NYC street parking.",
        "I will not invent a missing date, time, or duration.",
        "",
    ]
    for q in req.clarification_questions:
        lines.append(f"- {q}")
    if req.destination:
        lines.append(f"- Destination so far: {req.destination}")
    lines.extend(
        [
            "",
            "Example: park near Columbia University on 2026-11-18 from 10:00 AM for 2 hours "
            "(passenger vehicle, ~500m search radius).",
            "",
            "This is research assistance only — not a determination that parking is available or legal. "
            "Physical signs and temporary restrictions take precedence.",
        ]
    )
    return "\n".join(lines)


def _extract_destination(question: str) -> str | None:
    m = _NEAR.search(question)
    if m:
        place = m.group("place").strip(" .,")
        # Cut trailing date/time clauses if captured.
        place = re.split(r"\bon\b|\bfrom\b|\bfor\b", place, maxsplit=1, flags=re.I)[0].strip(" .,")
        if len(place) >= 3:
            return place
    return None


def _extract_date(question: str) -> tuple[int, int, int] | None:
    m = _DATE.search(question)
    if m:
        return int(m.group("y")), int(m.group("m")), int(m.group("d"))
    m = _DATE_LONG.search(question)
    if m:
        year = int(m.group("year") or datetime.now(NYC_TZ).year)
        return year, _MONTHS[m.group("month").lower()], int(m.group("day"))
    return None


def _parse_date(text: str) -> tuple[int, int, int] | None:
    text = text.strip()
    m = _DATE.fullmatch(text) or _DATE.search(text)
    if m:
        return int(m.group("y")), int(m.group("m")), int(m.group("d"))
    m = _DATE_LONG.search(text)
    if m:
        year = int(m.group("year") or datetime.now(NYC_TZ).year)
        return year, _MONTHS[m.group("month").lower()], int(m.group("day"))
    return None


def _parse_clock(text: str) -> int | None:
    m = _TIME.search(text.strip())
    if not m:
        return None
    hour = int(m.group("h"))
    minute = int(m.group("min") or 0)
    ampm = re.sub(r"\.", "", m.group("ampm")).lower()
    if hour < 1 or hour > 12 or minute > 59:
        return None
    if ampm.startswith("a"):
        hour = 0 if hour == 12 else hour
    else:
        hour = 12 if hour == 12 else hour + 12
    return hour * 60 + minute


def _extract_time_range(question: str) -> tuple[int, int] | None:
    m = _TIME_RANGE.search(question)
    if not m:
        return None
    t1 = _parse_clock(m.group("t1"))
    t2 = _parse_clock(m.group("t2"))
    if t1 is None or t2 is None:
        return None
    return t1, t2
