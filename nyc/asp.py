"""Alternate Side Parking suspensions from NYC DOT (and optional 311 today status)."""

from __future__ import annotations

import re
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .socrata import USER_AGENT

NYC_TZ = ZoneInfo("America/New_York")
DOT_ASP_URL = "https://www.nyc.gov/html/dot/html/motorist/alternate-side-parking.shtml"
NYC311_TODAY_URL = "https://portal.311.nyc.gov/home-cal/"


class AspError(RuntimeError):
    pass


class _TableParser(HTMLParser):
    """Collect text cells from HTML tables."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._in_cell = True

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell and self._row is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append(text)
            self._cell = None
            self._in_cell = False
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._in_cell and self._cell is not None:
            self._cell.append(data)


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


def fetch_asp_suspensions(*, year: int | None = None) -> dict[str, Any]:
    """Parse the official DOT ASP suspensions calendar page.

    Returns scheduled holiday suspensions. Weather/emergency suspensions are
    not reliably listed here; see fetch_asp_status_today() for live 311 status.
    """
    year = year or datetime.now(NYC_TZ).year
    try:
        response = httpx.get(
            DOT_ASP_URL,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=60.0,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise AspError(f"DOT ASP page fetch failed: {type(e).__name__}: {e}") from e

    parser = _TableParser()
    parser.feed(response.text)
    suspensions: list[dict[str, Any]] = []
    for table in parser.tables:
        if not table:
            continue
        header = [c.lower() for c in table[0]]
        if not (any("date" in h for h in header) and any("holiday" in h for h in header)):
            continue
        for row in table[1:]:
            if len(row) < 2:
                continue
            parsed = _parse_holiday_row(row[0], row[1], year)
            if parsed:
                suspensions.append(parsed)

    # Deduplicate same date+holiday
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in suspensions:
        key = (item["date"], item["holiday"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    unique.sort(key=lambda x: x["date"])

    return {
        "source": DOT_ASP_URL,
        "year": year,
        "count": len(unique),
        "suspensions": unique,
        "notes": [
            "Holiday calendar from NYC DOT ASP page.",
            "Days marked with * are major legal holidays; DOT notes broader meter/rule effects.",
            "Weather and emergency ASP suspensions may not appear on this calendar.",
            "On-site signs and temporary postings take precedence.",
        ],
    }


def asp_suspended_on(day: date, suspensions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Check whether a calendar date appears on the DOT ASP holiday suspension list."""
    if suspensions is None:
        payload = fetch_asp_suspensions(year=day.year)
        suspensions = payload["suspensions"]
        source = payload["source"]
    else:
        source = DOT_ASP_URL
    matches = [s for s in suspensions if s["date"] == day.isoformat()]
    return {
        "date": day.isoformat(),
        "suspended_on_holiday_calendar": bool(matches),
        "matches": matches,
        "source": source,
        "uncertainty": (
            None
            if matches
            else "No holiday suspension listed for this date; weather/emergency suspensions may still apply."
        ),
    }


def fetch_asp_status_today() -> dict[str, Any]:
    """Fetch today's ASP status from the NYC 311 home calendar endpoint.

    This endpoint historically returns 'today' only. Treat as live status evidence,
    not a full historical calendar.
    """
    try:
        response = httpx.get(
            NYC311_TODAY_URL,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html"},
            timeout=30.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        text = response.text.strip()
        # Endpoint sometimes returns JSON with a text/html content-type.
        payload = httpx.Response(200, text=text).json() if text.startswith("{") else None
        if payload is None:
            import json

            payload = json.loads(text)
    except Exception as e:
        raise AspError(f"NYC 311 calendar fetch failed: {type(e).__name__}: {e}") from e

    asp = None
    for item in payload.get("results") or []:
        name = (item.get("CalendarName") or item.get("CalendarTypeRecordName") or "").lower()
        if "alternate side" in name:
            asp = item
            break

    now = datetime.now(NYC_TZ)
    weekday = now.weekday()  # Mon=0
    if asp is None:
        status = None
        details = None
    elif weekday == 5:
        status = asp.get("SaturdayRecordName")
        details = asp.get("SaturdayContentFormat")
    elif weekday == 6:
        status = asp.get("SundayRecordName")
        details = asp.get("SundayContentFormat")
    else:
        status = asp.get("WeekDayRecordName")
        details = asp.get("WeekDayContentFormat")
        # Holiday overrides may appear in detail fields.
        if asp.get("CalendarDetailStatus"):
            status = asp.get("CalendarDetailStatus") or status
            details = asp.get("CalendarDetailMessage") or details

    return {
        "source": NYC311_TODAY_URL,
        "as_of_local": now.isoformat(),
        "timezone": "America/New_York",
        "status": status,
        "details": details,
        "raw_date_field": payload.get("date"),
        "caveat": "311 home-cal reflects current-day status; do not use it as a multi-day schedule API.",
    }


def _parse_holiday_row(date_text: str, holiday: str, year: int) -> dict[str, Any] | None:
    # Examples: "Thursday, January 1" or "Monday, February 16"
    m = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})",
        date_text,
        flags=re.I,
    )
    if not m:
        return None
    month = _MONTHS[m.group(1).lower()]
    day = int(m.group(2))
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    major = holiday.strip().endswith("*")
    return {
        "date": d.isoformat(),
        "holiday": holiday.strip(),
        "major_legal_holiday": major,
        "date_text": date_text.strip(),
        "source": DOT_ASP_URL,
    }
