"""Structured parking-regulation models for deterministic evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class RestrictionKind(str, Enum):
    NO_PARKING = "NO_PARKING"
    NO_STANDING = "NO_STANDING"
    NO_STOPPING = "NO_STOPPING"
    ASP_STREET_CLEANING = "ASP_STREET_CLEANING"
    METERED = "METERED"
    UNKNOWN = "UNKNOWN"
    INFORMATIONAL = "INFORMATIONAL"  # e.g. pay-by-cell locator; not a curb rule


class ParseStatus(str, Enum):
    PARSED = "PARSED"
    UNPARSED = "UNPARSED"
    INFORMATIONAL = "INFORMATIONAL"


class ArrowIndicator(str, Enum):
    """Arrow glyph / field evidence on a sign plate (separate from geographic coverage)."""

    BOTH = "BOTH"  # explicit <-> bidirectional marker
    SINGLE = "SINGLE"  # explicit --> or arrow_direction field
    UNKNOWN = "UNKNOWN"  # no arrow glyph and no arrow_direction


class ScopeCertainty(str, Enum):
    """Plate-level coverage *cue* — not verified geographic applicability.

    ArrowIndicator / DOUBLE_ARROW / STREET_SIDE_MATCHED must NEVER be treated as
    SCOPE_VERIFIED. A <-> glyph means the plate regulates both directions; it does
    not establish curb-zone boundaries or prove a nearby meter is covered.
    """

    DOUBLE_ARROW = "DOUBLE_ARROW"  # <-> bidirectional plate cue (not scope-verified)
    ARROW_PARTIAL = "ARROW_PARTIAL"  # single-arrow / partial curb cue
    UNKNOWN = "UNKNOWN"  # no usable arrow cue
    # Legacy alias kept for older fixtures/tests; identical to DOUBLE_ARROW.
    FULL_SEGMENT = "DOUBLE_ARROW"


class ParkingStatus(str, Enum):
    POTENTIALLY_PERMITTED = "POTENTIALLY_PERMITTED"
    PROHIBITED = "PROHIBITED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


# Monday=0 ... Sunday=6 (datetime.weekday)
ALL_DAYS = frozenset(range(7))
MON_FRI = frozenset(range(0, 5))
MON_SAT = frozenset(range(0, 6))


@dataclass(frozen=True)
class DayTimeWindow:
    """A recurring weekly window in local civil time (America/New_York).

    end_minute may be <= start_minute to mean overnight (crosses midnight).
    anytime=True means the whole day for each listed weekday.
    """

    days: frozenset[int]
    start_minute: int = 0  # minutes from local midnight
    end_minute: int = 24 * 60
    anytime: bool = False

    def __post_init__(self) -> None:
        if self.anytime:
            return
        if not (0 <= self.start_minute < 24 * 60):
            raise ValueError("start_minute out of range")
        if not (0 < self.end_minute <= 24 * 60) and self.end_minute != self.start_minute:
            # end_minute == 24*60 is end-of-day; overnight uses end <= start
            if not (0 <= self.end_minute < 24 * 60):
                raise ValueError("end_minute out of range")


@dataclass
class Regulation:
    kind: RestrictionKind
    raw_description: str
    source_id: str
    source_url: str
    parse_status: ParseStatus
    windows: list[DayTimeWindow] = field(default_factory=list)
    max_duration_minutes: int | None = None
    vehicle_category: str | None = None  # e.g. "passenger" for meters
    arrow_direction: str | None = None
    arrow_indicator: ArrowIndicator = ArrowIndicator.UNKNOWN
    scope_certainty: ScopeCertainty = ScopeCertainty.UNKNOWN
    sign_code: str | None = None
    parse_limitation: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["parse_status"] = self.parse_status.value
        d["arrow_indicator"] = self.arrow_indicator.value
        d["scope_certainty"] = self.scope_certainty.value
        d["windows"] = [
            {
                "days": sorted(w.days),
                "start_minute": w.start_minute,
                "end_minute": w.end_minute,
                "anytime": w.anytime,
            }
            for w in self.windows
        ]
        return d


@dataclass
class AspSuspension:
    date: str  # YYYY-MM-DD
    holiday: str
    major_legal_holiday: bool = False
    source: str | None = None


@dataclass
class EvaluationContext:
    """Flags and side-channel evidence for a classification."""

    signs_truncated: bool = False
    meters_truncated: bool = False
    closures_truncated: bool = False
    has_sign_records: bool = False
    has_meter_records: bool = False
    # True only when curb coverage for applied regs is independently established.
    # Meter coordinates alone must NOT set this. STREET_SIDE_MATCHED ≠ SCOPE_VERIFIED.
    location_scope_established: bool = False
    # None = unknown completeness (not the same as complete).
    signs_complete: bool | None = None
    meters_complete: bool | None = None
    # When True, unknown/incomplete sign retrieval blocks POTENTIALLY_PERMITTED.
    require_sign_retrieval_completeness: bool = False
    asp_suspensions: list[AspSuspension] = field(default_factory=list)
    # Only used when the evaluated interval is "today" in America/New_York.
    asp_status_today: str | None = None
    asp_status_today_source: str | None = None
    evaluate_date_is_today: bool = False
    construction_permits: list[dict[str, Any]] = field(default_factory=list)
    vehicle_category: str = "passenger"
    candidate_kind: str | None = None


@dataclass
class ParkingDecision:
    status: ParkingStatus
    explanation: str
    applied_regulations: list[Regulation] = field(default_factory=list)
    unknown_regulations: list[Regulation] = field(default_factory=list)
    supporting_sources: list[dict[str, str]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    interval: dict[str, str] = field(default_factory=dict)
    candidate: dict[str, Any] = field(default_factory=dict)
    rule_findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "explanation": self.explanation,
            "applied_regulations": [r.to_dict() for r in self.applied_regulations],
            "unknown_regulations": [r.to_dict() for r in self.unknown_regulations],
            "supporting_sources": self.supporting_sources,
            "limitations": self.limitations,
            "interval": self.interval,
            "candidate": self.candidate,
            "rule_findings": self.rule_findings,
        }
