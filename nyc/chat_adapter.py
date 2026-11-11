"""Project 1 /chat compatibility for CurbWise.

Contract (Studio Copilot / Deep Research assignment):
  Request:  {"message": str, "session_id": str | null}
  Response: {"response": str, "session_id": str, "tool_calls": [
      {"name": str, "args": object, "result": object, "agent": str}
  ]}

Sessions are in-process memory only — they do not survive server restarts.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

from nyc.request import normalize_parking_request

# Bounded local session memory (no external DB).
MAX_CHAT_SESSIONS = 64
MAX_SESSION_TURNS = 24
SESSION_TTL_SECONDS = 60 * 60 * 6  # 6 hours


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None

    @field_validator("message")
    @classmethod
    def message_must_be_nonempty(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("message must be a non-empty string")
        return text

    @field_validator("session_id")
    @classmethod
    def blank_session_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class ToolCallRecord(BaseModel):
    name: str
    args: dict[str, Any] | list[Any] | str | int | float | bool | None = Field(default_factory=dict)
    result: Any = None
    agent: str


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)


@dataclass
class ChatSession:
    session_id: str
    messages: list[dict[str, str]] = field(default_factory=list)
    parking: dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()


# Process-local store. Lost on restart / multi-worker fork.
chat_sessions: dict[str, ChatSession] = {}


def session_persistence_note() -> str:
    return (
        "Chat sessions are stored in process memory only "
        f"(max {MAX_CHAT_SESSIONS} sessions, TTL {SESSION_TTL_SECONDS}s, "
        f"max {MAX_SESSION_TURNS} turns). They do not survive server restarts."
    )


def _evict_stale_sessions(now: float | None = None) -> None:
    now = now or time.time()
    expired = [sid for sid, s in chat_sessions.items() if now - s.updated_at > SESSION_TTL_SECONDS]
    for sid in expired:
        chat_sessions.pop(sid, None)
    if len(chat_sessions) <= MAX_CHAT_SESSIONS:
        return
    # Drop oldest first.
    ordered = sorted(chat_sessions.items(), key=lambda kv: kv[1].updated_at)
    for sid, _ in ordered[: max(0, len(chat_sessions) - MAX_CHAT_SESSIONS)]:
        chat_sessions.pop(sid, None)


def get_or_create_chat_session(session_id: str | None) -> ChatSession:
    """Return an existing session or create a new one. Raises KeyError for unknown ids."""
    _evict_stale_sessions()
    if session_id:
        session = chat_sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        session.touch()
        return session
    sid = str(uuid.uuid4())
    session = ChatSession(session_id=sid)
    chat_sessions[sid] = session
    _evict_stale_sessions()
    return session


def clear_chat_sessions() -> None:
    """Test helper."""
    chat_sessions.clear()


def _arrival_parts(req_dict: dict[str, Any] | None) -> tuple[str, str]:
    if not req_dict:
        return "", ""
    arrival = req_dict.get("arrival")
    if not arrival:
        return "", ""
    text = str(arrival)
    date = text[:10] if len(text) >= 10 else ""
    time_part = ""
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(text)
        hour = dt.hour % 12 or 12
        ampm = "AM" if dt.hour < 12 else "PM"
        time_part = f"{hour}:{dt.minute:02d} {ampm}"
    except ValueError:
        time_part = ""
    return date, time_part


def parking_fields_from_request(req_dict: dict[str, Any] | None) -> dict[str, Any]:
    """Extract durable parking fields from a normalize/tool request dict."""
    if not req_dict:
        return {}
    date, time_part = _arrival_parts(req_dict)
    duration_hours = None
    if req_dict.get("duration_minutes") is not None:
        try:
            duration_hours = float(req_dict["duration_minutes"]) / 60.0
        except (TypeError, ValueError):
            duration_hours = None
    out: dict[str, Any] = {}
    if req_dict.get("destination"):
        out["destination"] = req_dict["destination"]
    if date:
        out["arrival_date"] = date
    if time_part:
        out["arrival_time"] = time_part
    if duration_hours is not None and duration_hours > 0:
        out["duration_hours"] = duration_hours
    if req_dict.get("radius_m") is not None:
        out["radius_m"] = req_dict["radius_m"]
    if req_dict.get("max_candidates") is not None:
        out["max_candidates"] = req_dict["max_candidates"]
    return out


def update_session_parking(session: ChatSession, req_dict: dict[str, Any] | None) -> None:
    session.parking.update(parking_fields_from_request(req_dict))
    session.touch()


def build_chat_research_inputs(session: ChatSession, message: str) -> dict[str, Any]:
    """Merge prior session parking with the new user message. Never invents missing fields."""
    prior = session.parking
    # Seed normalize with known session fields so follow-ups can complete a request
    # without restating destination (and without inventing missing values).
    parsed = normalize_parking_request(
        message,
        destination=prior.get("destination") or None,
        arrival_date=prior.get("arrival_date") or None,
        arrival_time=prior.get("arrival_time") or None,
        duration_hours=prior.get("duration_hours") if prior.get("duration_hours") not in (None, "") else None,
        radius_m=prior.get("radius_m"),
        max_candidates=prior.get("max_candidates"),
    )
    from_parse = parking_fields_from_request(parsed.to_dict())
    merged = dict(prior)
    for key, value in from_parse.items():
        if value in (None, ""):
            continue
        if key == "destination":
            # Never replace a known destination with a weak re-parse from a follow-up.
            if not merged.get("destination"):
                merged[key] = value
            continue
        merged[key] = value

    context_lines: list[str] = []
    if prior:
        context_lines.append("Known parking parameters from earlier turns in this session:")
        for key in ("destination", "arrival_date", "arrival_time", "duration_hours", "radius_m"):
            if prior.get(key) not in (None, ""):
                context_lines.append(f"- {key}: {prior[key]}")
        context_lines.append(
            "Use these only where the latest user message does not replace them. "
            "Do not invent date/time/duration that remain missing."
        )

    if context_lines:
        question = "\n".join(context_lines) + f"\n\nLatest user message:\n{message}"
    else:
        question = message

    duration = merged.get("duration_hours")
    try:
        duration_f = float(duration) if duration not in (None, "") else 0.0
    except (TypeError, ValueError):
        duration_f = 0.0

    return {
        "question": question,
        "destination": str(merged.get("destination") or ""),
        "arrival_date": str(merged.get("arrival_date") or ""),
        "arrival_time": str(merged.get("arrival_time") or ""),
        "duration_hours": duration_f,
        "radius_m": float(merged.get("radius_m") or 500.0),
        "max_candidates": int(merged.get("max_candidates") or 18),
    }


def append_turn(session: ChatSession, *, user: str, assistant: str) -> None:
    session.messages.append({"role": "user", "content": user})
    session.messages.append({"role": "assistant", "content": assistant})
    if len(session.messages) > MAX_SESSION_TURNS * 2:
        session.messages = session.messages[-(MAX_SESSION_TURNS * 2) :]
    session.touch()


def tool_calls_to_records(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize captured tool call dicts for the /chat response."""
    out: list[dict[str, Any]] = []
    for item in raw or []:
        args = item.get("args", {})
        if args is None:
            args = {}
        out.append(
            {
                "name": str(item.get("name") or "unknown"),
                "args": args,
                "result": item.get("result"),
                "agent": str(item.get("agent") or "unknown"),
            }
        )
    return out
