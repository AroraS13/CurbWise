"""CurbWise: NYC street-parking deep research agent.

Preserves the professor's Lead → parallel Researchers (fresh contexts) → Writer
pattern, Gemini/Vertex via LiteLLM, SSE streaming, and context panel.
Parking legality is classified only by nyc.parking_rules (deterministic).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from context_panel import AgentContexts, PanelHooks, add_context_panel
from nyc.chat_adapter import (
    ChatRequest,
    ChatResponse,
    append_turn,
    build_chat_research_inputs,
    get_or_create_chat_session,
    session_persistence_note,
    tool_calls_to_records,
    update_session_parking,
)
from nyc.evidence import (
    build_writer_packet,
    classify_evidence_bundle,
    discover_candidates,
    merge_research_evidence,
    research_exceptions,
    research_meters_for_candidates,
    research_signs_for_candidates,
    validate_writer_text,
)
from nyc.frontend_payload import build_frontend_results
from nyc.geocode import geocode_destination
from nyc.request import (
    InvalidRadiusError,
    clarification_message,
    normalize_parking_request,
    validate_radius_m,
)

load_dotenv(Path(__file__).parent / ".env")

set_tracing_disabled(True)

MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash", api_key="unused")
RESEARCHER_MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash-lite", api_key="unused")
SETTINGS = ModelSettings(extra_args={"vertex_location": "global", "timeout": 90, "num_retries": 2})

MAX_RESEARCHERS = 6
LEAD_MAX_TURNS = 12
RESEARCHER_MAX_TURNS = 8


def _unsupported_question_message(question: str) -> str | None:
    """Return a deterministic explanation for questions outside CurbWise's data coverage."""
    text = (question or "").strip()
    occupancy = (
        re.search(r"\b(empty|vacant|occupied|occupancy)\b", text, re.I)
        and re.search(r"\b(space|spaces|spot|spots|parking)\b", text, re.I)
        and re.search(r"\b(now|currently|live|real[- ]time)\b", text, re.I)
    )
    if occupancy:
        return (
            "CurbWise cannot determine whether street-parking spaces are empty or occupied in "
            "real time because its NYC datasets do not contain live curb occupancy. A parking "
            "meter's status describes the meter equipment, not whether a space is vacant. I can "
            "investigate the parking regulations instead if you provide the parking date, time, "
            "and duration."
        )

    explicitly_outside_nyc = re.search(r"\boutside (?:NYC|New York City)\b", text, re.I)
    non_ny_state = re.search(r",\s*(?!NY(?:\s|,|$))[A-Z]{2}(?:\s|,|$)", text, re.I)
    if explicitly_outside_nyc or non_ny_state:
        return (
            "CurbWise currently covers New York City only. Its NYC datasets cannot support a "
            "parking-rule answer for that location. Please provide an NYC destination."
        )
    return None


@dataclass
class Research:
    """One run's state, shared by tools. The model never sees this object directly."""

    hooks: PanelHooks
    researchers: int = 0
    findings: list[str] = field(default_factory=list)
    request: dict[str, Any] = field(default_factory=dict)
    discovery: dict[str, Any] = field(default_factory=dict)
    sign_research: dict[str, Any] | None = None
    meter_research: dict[str, Any] | None = None
    exception_research: dict[str, Any] | None = None
    evidence_bundle: dict[str, Any] | None = None
    classifications: dict[str, Any] | None = None
    writer_packet: dict[str, Any] | None = None


def _json(data: Any) -> str:
    return json.dumps(data, default=str)


# --- Tools (real NYC data / deterministic code; not model memory) ---


@function_tool
def prepare_parking_request(
    ctx: RunContextWrapper[Research],
    question: str,
    destination: str = "",
    arrival_date: str = "",
    arrival_time: str = "",
    duration_hours: float = 0.0,
    radius_m: float = 500.0,
    max_candidates: int = 18,
    vehicle_category: str = "passenger",
) -> str:
    """Normalize the user's parking request. Does not invent missing date/time/duration.

    Args:
        question: The user's free-text question.
        destination: Optional NYC destination override.
        arrival_date: Optional YYYY-MM-DD or month day year.
        arrival_time: Optional time like '10:00 AM'.
        duration_hours: Optional stay length in hours; 0 means unspecified.
        radius_m: Search radius in meters.
        max_candidates: Max distinct block sides to keep.
        vehicle_category: Passenger or commercial vehicle category.
    """
    req = normalize_parking_request(
        question,
        destination=destination or None,
        arrival_date=arrival_date or None,
        arrival_time=arrival_time or None,
        duration_hours=duration_hours if duration_hours and duration_hours > 0 else None,
        radius_m=radius_m,
        max_candidates=max_candidates,
        vehicle_category=vehicle_category,
    )
    payload = req.to_dict()
    if not req.is_complete:
        payload["clarification"] = clarification_message(req)
    ctx.context.request = payload
    if not req.is_complete:
        return _json({"complete": False, "request": payload, "clarification": payload["clarification"]})
    return _json({"complete": True, "request": payload})


@function_tool
def geocode_and_list_candidates(ctx: RunContextWrapper[Research]) -> str:
    """Geocode the prepared destination and list nearby distinct block-side candidates."""
    req_dict = ctx.context.request
    if not req_dict:
        return _json({"error": "Call prepare_parking_request first."})
    req = normalize_parking_request(
        req_dict.get("raw_question") or "",
        destination=req_dict.get("destination"),
        arrival_date=(req_dict.get("arrival") or "")[:10] or None,
        arrival_time=_time_from_iso(req_dict.get("arrival")),
        duration_minutes=req_dict.get("duration_minutes"),
        radius_m=req_dict.get("radius_m"),
        max_candidates=req_dict.get("max_candidates"),
        vehicle_category=req_dict.get("vehicle_category") or "passenger",
    )
    if not req.is_complete:
        return _json({"error": "incomplete_request", "request": req.to_dict()})
    discovery = discover_candidates(req)
    ctx.context.discovery = discovery
    if not discovery.get("found"):
        return _json(discovery)
    # Return a compact view for the Lead (full records stay in context.discovery).
    return _json(
        {
            "found": True,
            "destination": discovery.get("destination"),
            "counts": discovery.get("counts"),
            "truncated": discovery.get("truncated"),
            "capped": discovery.get("capped"),
            "candidate_summaries": discovery.get("candidate_summaries"),
            "sources": discovery.get("sources"),
            "limitations": discovery.get("limitations"),
        }
    )


def _sign_tool_impl(ctx: Research, block_keys: list[str] | None = None) -> dict[str, Any]:
    discovery = ctx.discovery or {}
    dest = discovery.get("destination")
    if not dest:
        return {"error": "geocode_and_list_candidates required first"}
    cands = discovery.get("candidates") or []
    if block_keys:
        keyset = set(block_keys)
        cands = [c for c in cands if c.get("block_key") in keyset]
    result = research_signs_for_candidates(cands, center=dest, radius_m=float((ctx.request or {}).get("radius_m") or 400))
    ctx.sign_research = result
    # Compact tool output for the model
    return {
        "specialty": "signs",
        "source": result.get("source"),
        "truncated": result.get("truncated"),
        "fetched_count": result.get("fetched_count"),
        "gaps": result.get("gaps"),
        "candidate_sign_counts": [
            {
                "block_key": c.get("block_key"),
                "sign_count": len(c.get("signs") or []),
                "parsed": [
                    {
                        "source_id": p.get("source_id"),
                        "kind": p.get("kind"),
                        "parse_status": p.get("parse_status"),
                        "arrow_indicator": p.get("arrow_indicator"),
                        "scope_certainty": p.get("scope_certainty"),
                        "raw_description": p.get("raw_description"),
                        "parse_limitation": p.get("parse_limitation"),
                        "source_url": p.get("source_url"),
                    }
                    for p in (c.get("parsed_signs") or [])[:10]
                ],
            }
            for c in (result.get("candidates") or [])
        ],
    }


def _meter_tool_impl(ctx: Research, block_keys: list[str] | None = None) -> dict[str, Any]:
    discovery = ctx.discovery or {}
    dest = discovery.get("destination")
    if not dest:
        return {"error": "geocode_and_list_candidates required first"}
    cands = discovery.get("candidates") or []
    if block_keys:
        keyset = set(block_keys)
        cands = [c for c in cands if c.get("block_key") in keyset]
    result = research_meters_for_candidates(cands, center=dest, radius_m=float((ctx.request or {}).get("radius_m") or 400))
    ctx.meter_research = result
    return {
        "specialty": "meters",
        "source": result.get("source"),
        "truncated": result.get("truncated"),
        "fetched_count": result.get("fetched_count"),
        "gaps": result.get("gaps"),
        "candidate_meter_counts": [
            {
                "block_key": c.get("block_key"),
                "meter_count": len(c.get("meters") or []),
                "parsed": [
                    {
                        "source_id": p.get("source_id"),
                        "kind": p.get("kind"),
                        "parse_status": p.get("parse_status"),
                        "vehicle_category": p.get("vehicle_category"),
                        "max_duration_minutes": p.get("max_duration_minutes"),
                        "windows": p.get("windows"),
                        "raw_description": p.get("raw_description"),
                        "scope_certainty": p.get("scope_certainty"),
                        "source_url": p.get("source_url"),
                    }
                    for p in (c.get("parsed_meters") or [])[:10]
                ],
            }
            for c in (result.get("candidates") or [])
        ],
    }


def _exception_tool_impl(ctx: Research) -> dict[str, Any]:
    discovery = ctx.discovery or {}
    dest = discovery.get("destination")
    req_dict = ctx.request or {}
    if not dest or not req_dict:
        return {"error": "prepare_parking_request and geocode_and_list_candidates required first"}
    req = normalize_parking_request(
        req_dict.get("raw_question") or "",
        destination=req_dict.get("destination"),
        arrival_date=(req_dict.get("arrival") or "")[:10] or None,
        arrival_time=_time_from_iso(req_dict.get("arrival")),
        duration_minutes=req_dict.get("duration_minutes"),
        radius_m=req_dict.get("radius_m"),
        max_candidates=req_dict.get("max_candidates"),
    )
    result = research_exceptions(req, center=dest, radius_m=float(req.radius_m))
    ctx.exception_research = result
    return {
        "specialty": "exceptions",
        "asp_arrival_day": (result.get("asp_calendar") or {}).get("arrival_day"),
        "asp_calendar_source": (result.get("asp_calendar") or {}).get("source"),
        "asp_live_note": result.get("asp_live_note"),
        "closure_advisory_count": len(((result.get("closures") or {}).get("advisories") or [])),
        "closure_source": (result.get("closures") or {}).get("source"),
        "gaps": result.get("gaps"),
        "sample_closures": ((result.get("closures") or {}).get("advisories") or [])[:5],
    }


@function_tool
def fetch_sign_evidence(ctx: RunContextWrapper[Research], block_keys_json: str = "[]") -> str:
    """Query NYC DOT parking regulation signs for the discovered candidates and parse them.

    Args:
        block_keys_json: Optional JSON list of block_key strings; empty means all candidates.
    """
    keys = _parse_keys(block_keys_json)
    return _json(_sign_tool_impl(ctx.context, keys))


@function_tool
def fetch_meter_evidence(ctx: RunContextWrapper[Research], block_keys_json: str = "[]") -> str:
    """Query NYC DOT parking meters for the discovered candidates and parse meter hours.

    Args:
        block_keys_json: Optional JSON list of block_key strings; empty means all candidates.
    """
    keys = _parse_keys(block_keys_json)
    return _json(_meter_tool_impl(ctx.context, keys))


@function_tool
def fetch_exception_evidence(ctx: RunContextWrapper[Research]) -> str:
    """Check ASP holiday suspensions and construction-permit advisories for the stay."""
    return _json(_exception_tool_impl(ctx.context))


@function_tool
def run_deterministic_evaluation(ctx: RunContextWrapper[Research]) -> str:
    """Aggregate researcher evidence and classify each candidate with the rule engine.

    Do not judge parking legality yourself — this tool is the only legality classifier.
    """
    req_dict = ctx.context.request or {}
    discovery = ctx.context.discovery or {}
    if not discovery.get("found"):
        return _json({"error": "No discovery results to evaluate."})
    req = normalize_parking_request(
        req_dict.get("raw_question") or "",
        destination=req_dict.get("destination"),
        arrival_date=(req_dict.get("arrival") or "")[:10] or None,
        arrival_time=_time_from_iso(req_dict.get("arrival")),
        duration_minutes=req_dict.get("duration_minutes"),
        radius_m=req_dict.get("radius_m"),
        max_candidates=req_dict.get("max_candidates"),
    )
    bundle = merge_research_evidence(
        req,
        discovery,
        ctx.context.sign_research,
        ctx.context.meter_research,
        ctx.context.exception_research,
    )
    classifications = classify_evidence_bundle(bundle)
    packet = build_writer_packet(bundle, classifications)
    ctx.context.evidence_bundle = bundle
    ctx.context.classifications = classifications
    ctx.context.writer_packet = packet
    return _json(
        {
            "status_counts": classifications.get("status_counts"),
            "lead_counts": classifications.get("lead_counts"),
            "top_leads": classifications.get("top_leads"),
            "classifier": classifications.get("classifier"),
            "lead_ranker": classifications.get("lead_ranker"),
            "note": classifications.get("note"),
            "candidate_statuses": [
                {
                    "candidate_id": c.get("candidate_id"),
                    "candidate_kind": c.get("candidate_kind"),
                    "block_key": c.get("block_key"),
                    "status": c.get("status"),
                    "explanation": (c.get("decision") or {}).get("explanation"),
                    "rule_findings": (c.get("decision") or {}).get("rule_findings"),
                    "supporting_sources": (c.get("decision") or {}).get("supporting_sources"),
                    "limitations": (c.get("decision") or {}).get("limitations"),
                    "retrieval": c.get("retrieval"),
                }
                for c in (classifications.get("classifications") or [])
            ],
            "evidence_gaps": bundle.get("evidence_gaps"),
        }
    )


# --- Specialized researchers (fresh contexts via research()) ---

sign_researcher = Agent(
    name="Sign Researcher",
    instructions="""\
You research NYC parking signs for CurbWise.

1. Call fetch_sign_evidence once (optionally with block_keys_json if the task names keys).
2. Summarize, for each candidate block side: sign counts, parsed kinds, arrow_indicator,
   scope_certainty, source_id/source_url, and parse limitations.
3. Do NOT decide if parking is legal. Do NOT invent signs.
4. Call out unverified curb extent (UNKNOWN/ARROW_PARTIAL scope) explicitly.
5. Reply with at most 12 short bullets grounded in the tool JSON.""",
    model=RESEARCHER_MODEL,
    model_settings=SETTINGS,
    tools=[fetch_sign_evidence],
)

meter_researcher = Agent(
    name="Meter Researcher",
    instructions="""\
You research NYC parking meters for CurbWise.

1. Call fetch_meter_evidence once.
2. Summarize each parsed passenger/commercial schedule component separately, including days,
   hours, max duration, status, source IDs/URLs, and location-matching uncertainty.
3. Reminder: meters are point evidence; they do not prove full block-side coverage.
4. Missing meter records do not establish free parking. Do NOT decide overall parking legality
   or invent meters.
5. Reply with at most 10 short bullets grounded in the tool JSON.""",
    model=RESEARCHER_MODEL,
    model_settings=SETTINGS,
    tools=[fetch_meter_evidence],
)

exception_researcher = Agent(
    name="Exception Researcher",
    instructions="""\
You research ASP suspensions and construction advisories for CurbWise.

1. Call fetch_exception_evidence once.
2. Report whether the arrival date is on the DOT ASP holiday calendar and if it is a major legal holiday.
3. Never apply a today-only 311 ASP status to a future date; quote asp_live_note.
4. Treat construction permits as possible-restriction advisories only — not verified active closures.
5. Do NOT decide overall parking legality.
6. Reply with at most 10 short bullets grounded in the tool JSON.""",
    model=RESEARCHER_MODEL,
    model_settings=SETTINGS,
    tools=[fetch_exception_evidence],
)

_SPECIALTY_AGENTS = {
    "signs": sign_researcher,
    "meters": meter_researcher,
    "exceptions": exception_researcher,
}


@function_tool
async def research(ctx: RunContextWrapper[Research], specialty: str, task: str) -> str:
    """Delegate one specialty researcher in a fresh context. Prefer calling multiple in one turn.

    Args:
        specialty: One of 'signs', 'meters', or 'exceptions'.
        task: Specific research task for that specialty.
    """
    if ctx.context.researchers >= MAX_RESEARCHERS:
        return "No researchers left. Call run_deterministic_evaluation if needed, then reply DONE."
    key = (specialty or "").strip().lower()
    base = _SPECIALTY_AGENTS.get(key)
    if base is None:
        return _json({"error": f"Unknown specialty {specialty!r}. Use signs, meters, or exceptions."})

    ctx.context.researchers += 1
    agent = base.clone(name=f"{base.name} {ctx.context.researchers}")
    prompt = (
        f"Specialty: {key}\n"
        f"Task: {task}\n"
        f"Request: {_json(ctx.context.request)}\n"
        f"Candidates: {_json((ctx.context.discovery or {}).get('candidate_summaries') or [])}"
    )
    result = await Runner.run(
        agent,
        prompt,
        context=ctx.context,
        hooks=ctx.context.hooks,
        max_turns=RESEARCHER_MAX_TURNS,
    )
    text = str(result.final_output)
    ctx.context.findings.append(f"[{key}] {task}\n\n{text}")
    return text


lead = Agent(
    name="Lead",
    instructions="""\
You lead CurbWise, an NYC street-parking research team.

Hard rules:
- Use tools for all external facts. Do not rely on model memory for regulations.
- Never decide parking legality yourself. Only run_deterministic_evaluation may classify.
- Never invent dates, times, durations, signs, meters, or citations.
- POTENTIALLY_PERMITTED is never a guarantee of legality or availability.
- CurbWise covers NYC only. If the destination cannot be matched by the NYC geocoder, say the
  available data cannot answer the question; do not substitute another location.
- Live curb occupancy is unavailable. Meter status describes equipment condition, not whether a
  parking space is empty. Offer regulation research instead.
- When evidence is partial, preserve useful known rules and state exactly what remains unknown.

Workflow:
1. Call prepare_parking_request with the user's question and any structured fields provided.
   If complete=false, reply with the clarification text ONLY (no DONE).
2. Call geocode_and_list_candidates. Treat physical CSCL street sides as the primary candidates;
   meters are secondary evidence. Approximate centerline-side geometry is not an exact curb boundary.
3. In ONE turn, call research three times in parallel:
   - specialty='signs'
   - specialty='meters'
   - specialty='exceptions'
4. Review findings for gaps. You may call research again for at most two follow-ups total
   across the whole run (MAX researchers is enforced in code).
5. Call run_deterministic_evaluation exactly once after research.
6. Reply DONE and nothing else. A Writer will compose the user-facing report.

Keep tasks specific. Bound exploration to the discovered candidate list.""",
    model=MODEL,
    model_settings=SETTINGS,
    tools=[
        prepare_parking_request,
        geocode_and_list_candidates,
        research,
        run_deterministic_evaluation,
    ],
)

writer = Agent(
    name="Writer",
    instructions="""\
Write a concise markdown parking-research report using ONLY the structured packet.

Follow packet.report_outline exactly:
1) Direct answer — say whether actionable investigation leads exist. If legal status is
   INSUFFICIENT_EVIDENCE, that means legality is unverified, NOT that no parking exists.
   Never write that “no legal parking options exist” merely because evidence is incomplete.
2) Best street sides to investigate — use recommendation_cards only (top leads). State the
   street/side, metered/unmetered/unknown classification, known restriction overlaps, and one
   useful on-site check. Include meter details only when applicable. Do not expose internal scores
   or raw schedule codes. Approximate street-side geometry is not an exact curb boundary.
3) Restrictions to watch — use restrictions_to_watch and avoid_locations.
4) What remains unverified — short and decision-relevant.
5) Evidence — cite source_id + source_url from the packet only.

Hard rules:
- Lead status ≠ legal status. PROMISING_LEAD is not permission to park.
- Never equate rule eligibility, evidence completeness, or physical space availability.
- Live physical space availability is not measured.
- Do not promote AVOID / PROHIBITED locations as places to try.
- Do not invent curb-space pins or block boundaries.
- For block-specific schedules, source_segment_restrictions is authoritative. Keep every source
  record attached to its own on_street, side_of_street, from_street, and to_street fields. Never
  merge time windows from different source records or segments. Do not claim exact curb coverage
  when scope_certainty is unverified.
- When the question asks what nearby meters allow, use meter_schedule_groups. Address every
  directly relevant group on the street named by the user and cite all meter_numbers in that
  group. A commercial-only period is not passenger permission. If the stated passenger period
  does not overlap the request, say so plainly rather than calling passenger eligibility
  unverified.
- One disclaimer at the end (packet.disclaimer). Do not repeat it throughout.
- Plain, specific sentences. No filler.""",
    model=MODEL,
    model_settings=SETTINGS,
)


async def curbwise_research(
    question: str,
    hooks: PanelHooks,
    *,
    destination: str = "",
    arrival_date: str = "",
    arrival_time: str = "",
    duration_hours: float = 0.0,
    radius_m: float = 500.0,
    max_candidates: int = 18,
    vehicle_category: str = "passenger",
) -> dict[str, Any]:
    """Run Lead → researchers → classifier → Writer.

    Returns markdown explanation plus deterministic structured results for the UI.
    """
    unsupported = _unsupported_question_message(question)
    if unsupported:
        hooks.activity("Lead", "identified a question outside available data coverage")
        return {
            "markdown": unsupported,
            "results": None,
            "incomplete": True,
            "request": {"is_complete": False, "unsupported": True},
        }

    started_at = time.perf_counter()
    research_run = Research(hooks=hooks)

    structured = {
        "destination": destination,
        "arrival_date": arrival_date,
        "arrival_time": arrival_time,
        "duration_hours": duration_hours,
        "radius_m": radius_m,
        "max_candidates": max_candidates,
        "vehicle_category": vehicle_category,
    }
    prompt = (
        "User parking question:\n"
        f"{question}\n\n"
        "Optional structured fields (may be empty):\n"
        f"{_json(structured)}\n"
    )
    lead_started = time.perf_counter()
    lead_result = await Runner.run(lead, prompt, context=research_run, hooks=hooks, max_turns=LEAD_MAX_TURNS)
    lead_seconds = time.perf_counter() - lead_started
    lead_out = str(lead_result.final_output or "")

    # Clarification path: Lead returns questions instead of DONE.
    if research_run.request and not research_run.request.get("is_complete"):
        msg = research_run.request.get("clarification") or clarification_message(
            normalize_parking_request(question)
        )
        return {
            "markdown": msg,
            "results": None,
            "incomplete": True,
            "request": research_run.request,
        }
    if lead_out.strip() and "DONE" not in lead_out.upper() and not research_run.classifications:
        # If Lead asked clarification in free text without storing request, pass through.
        if "clarif" in lead_out.lower() or "need" in lead_out.lower() and "date" in lead_out.lower():
            return {
                "markdown": lead_out,
                "results": None,
                "incomplete": True,
                "request": research_run.request,
            }

    if research_run.discovery and not research_run.discovery.get("found"):
        return {
            "markdown": (
                "CurbWise currently covers New York City only. The NYC geocoder could not match "
                "that destination, so the available data cannot support a parking-rule answer. "
                "Please provide a valid NYC address or place."
            ),
            "results": None,
            "incomplete": True,
            "request": research_run.request,
        }

    # Ensure deterministic evaluation ran even if Lead forgot.
    evaluation_seconds = 0.0
    if research_run.discovery.get("found") and not research_run.classifications:
        evaluation_started = time.perf_counter()
        req_dict = research_run.request or {}
        req = normalize_parking_request(
            question,
            destination=req_dict.get("destination"),
            arrival_date=(req_dict.get("arrival") or "")[:10] or None,
            arrival_time=_time_from_iso(req_dict.get("arrival")),
            duration_minutes=req_dict.get("duration_minutes"),
            radius_m=req_dict.get("radius_m") or radius_m,
            max_candidates=req_dict.get("max_candidates") or max_candidates,
            vehicle_category=req_dict.get("vehicle_category") or vehicle_category,
        )
        bundle = merge_research_evidence(
            req,
            research_run.discovery,
            research_run.sign_research,
            research_run.meter_research,
            research_run.exception_research,
        )
        research_run.evidence_bundle = bundle
        research_run.classifications = classify_evidence_bundle(bundle)
        research_run.writer_packet = build_writer_packet(bundle, research_run.classifications)
        hooks.activity("Evaluator", "ran deterministic parking_rules classifier")
        evaluation_seconds = time.perf_counter() - evaluation_started

    findings = "\n\n---\n\n".join(research_run.findings) or "(No researcher findings.)"
    packet = research_run.writer_packet or {
        "warning": "No classifier packet available.",
        "request": research_run.request,
    }
    writer_prompt = (
        f"Question: {question}\n\n"
        f"Structured evidence + classifications JSON:\n{_json(packet)}\n\n"
        f"Researcher findings:\n{findings}\n"
    )
    preliminary_results = None
    if research_run.evidence_bundle and research_run.classifications:
        payload_started = time.perf_counter()
        preliminary_results = build_frontend_results(
            research_run.evidence_bundle,
            research_run.classifications,
            explanation_markdown="",
        )
        payload_seconds = time.perf_counter() - payload_started
        hooks.activity(
            "UI Payload",
            f"structured results ready for {len(preliminary_results.get('candidates') or [])} candidates",
        )
        hooks.emit({"type": "results", "results": preliminary_results})
    else:
        payload_seconds = 0.0

    writer_started = time.perf_counter()
    report = await Runner.run(writer, writer_prompt, hooks=hooks, max_turns=4)
    writer_seconds = time.perf_counter() - writer_started
    text = str(report.final_output)
    check = validate_writer_text(text, packet if isinstance(packet, dict) else {})
    if not check.get("ok"):
        hooks.activity("Validator", f"writer guardrail flags: {check.get('violations')}")
        # Append a corrective note rather than inventing a new narrative.
        text = (
            text
            + "\n\n---\n**Automated consistency note:** "
            + " ".join(check.get("violations") or [])
            + " Treat recommendation_cards and legal statuses in the structured packet as authoritative."
        )

    results = preliminary_results
    if results is not None:
        results["explanation_markdown"] = text
        results["timings_seconds"] = {
            "lead_and_research": round(lead_seconds, 3),
            "deterministic_fallback": round(evaluation_seconds, 3),
            "frontend_payload": round(payload_seconds, 3),
            "writer": round(writer_seconds, 3),
            "total": round(time.perf_counter() - started_at, 3),
        }
        hooks.activity("UI Payload", f"structured results finalized for {len(results.get('candidates') or [])} candidates")

    return {
        "markdown": text,
        "results": results,
        "incomplete": False,
        "request": research_run.request,
    }


def _parse_keys(block_keys_json: str) -> list[str] | None:
    try:
        data = json.loads(block_keys_json or "[]")
    except json.JSONDecodeError:
        return None
    if not data:
        return None
    return [str(x) for x in data]


def _time_from_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        # 2026-11-18T10:00:00-05:00 -> 10:00 AM
        from datetime import datetime

        dt = datetime.fromisoformat(value)
        hour = dt.hour % 12 or 12
        ampm = "AM" if dt.hour < 12 else "PM"
        return f"{hour}:{dt.minute:02d} {ampm}"
    except ValueError:
        return None


# --- FastAPI ---

HERE = Path(__file__).parent
sessions: dict[str, AgentContexts] = {}

app = FastAPI(title="CurbWise")
add_context_panel(app, sessions)


@app.get("/")
def index():
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/geocode/suggest")
def geocode_suggest(q: str = "", size: int = 5):
    """NYC Planning Labs GeoSearch autocomplete for the search form."""
    query = (q or "").strip()
    if len(query) < 3:
        return {"query": query, "results": [], "source": "https://geosearch.planninglabs.nyc/docs/"}
    try:
        geo = geocode_destination(query, size=min(max(size, 1), 8))
        return {
            "query": query,
            "found": geo.get("found"),
            "results": geo.get("results") or [],
            "source": geo.get("source"),
        }
    except Exception as e:
        return JSONResponse(
            {"query": query, "error": f"{type(e).__name__}: {e}", "results": []},
            status_code=502,
        )


@app.get("/run")
async def run(
    question: str = "",
    destination: str = "",
    arrival_date: str = "",
    arrival_time: str = "",
    duration_hours: float = 0.0,
    radius_m: float = 500.0,
    max_candidates: int = 18,
    vehicle_category: str = "passenger",
):
    """Research a parking question and stream agent activity as Server-Sent Events.

    The terminal `done` event includes deterministic `results` for the map UI
    plus Writer `markdown`. The frontend must not parse markdown for coordinates.
    """
    try:
        radius_m = validate_radius_m(radius_m, default_if_none=True)
    except InvalidRadiusError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Build a natural-language question if the UI sends structured fields only.
    if not (question or "").strip() and destination:
        parts = [f"I need to park near {destination}"]
        if arrival_date:
            parts.append(f"on {arrival_date}")
        if arrival_time:
            parts.append(f"at {arrival_time}")
        if duration_hours and duration_hours > 0:
            parts.append(f"for {duration_hours:g} hours")
        question = " ".join(parts) + "."

    run_id = str(uuid.uuid4())
    sessions[run_id] = AgentContexts()
    queue: asyncio.Queue = asyncio.Queue()
    hooks = PanelHooks(sessions[run_id], queue.put_nowait)

    async def work():
        try:
            out = await curbwise_research(
                question,
                hooks,
                destination=destination,
                arrival_date=arrival_date,
                arrival_time=arrival_time,
                duration_hours=duration_hours,
                radius_m=radius_m,
                max_candidates=max_candidates,
                vehicle_category=vehicle_category,
            )
            queue.put_nowait(
                {
                    "type": "done",
                    "markdown": out.get("markdown"),
                    "results": out.get("results"),
                    "incomplete": bool(out.get("incomplete")),
                }
            )
        except Exception as e:
            queue.put_nowait({"type": "error", "message": f"{type(e).__name__}: {str(e)[:500]}"})

    task = asyncio.create_task(work())

    async def events():
        try:
            yield f"data: {json.dumps({'type': 'start', 'run_id': run_id})}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event["type"] in ("done", "error"):
                    break
        finally:
            task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/chat")
async def chat_get_not_allowed():
    """Project 1 contract is POST-only."""
    return JSONResponse(
        {
            "detail": "Use POST /chat with JSON body {message, session_id?}.",
            "session_persistence": session_persistence_note(),
        },
        status_code=405,
        headers={"Allow": "POST"},
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest):
    """Project 1 /chat: run the same Lead→Researchers→Writer pipeline as /run.

    Returns the Writer (or clarification) text plus real tool_calls attributed to agents.
    Sessions are in-memory only and do not survive restarts.
    """
    try:
        session = get_or_create_chat_session(body.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown session_id: {body.session_id}") from None

    inputs = build_chat_research_inputs(session, body.message)
    run_id = str(uuid.uuid4())
    sessions[run_id] = AgentContexts()
    # Collect activity without streaming; /chat returns a single JSON response.
    hooks = PanelHooks(sessions[run_id], lambda _event: None)

    try:
        out = await curbwise_research(
            inputs["question"],
            hooks,
            destination=inputs["destination"],
            arrival_date=inputs["arrival_date"],
            arrival_time=inputs["arrival_time"],
            duration_hours=inputs["duration_hours"],
            radius_m=inputs["radius_m"],
            max_candidates=inputs["max_candidates"],
        )
    except InvalidRadiusError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:500]}"
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        if "timeout" in name or "timeout" in text:
            raise HTTPException(status_code=504, detail=detail) from exc
        if any(k in text for k in ("auth", "credential", "permission", "quota", "429")):
            raise HTTPException(status_code=503, detail=detail) from exc
        if any(k in text for k in ("geocode", "sodapy", "socrata", "http", "connection", "api")):
            raise HTTPException(status_code=502, detail=detail) from exc
        raise HTTPException(status_code=500, detail=detail) from exc

    update_session_parking(session, out.get("request") if isinstance(out, dict) else None)
    response_text = str((out or {}).get("markdown") or "").strip()
    if not response_text:
        response_text = (
            "Research did not produce a usable response. "
            "Please restate destination, date, time, and duration."
        )
    append_turn(session, user=body.message, assistant=response_text)
    tool_calls = tool_calls_to_records(hooks.tool_calls)

    return ChatResponse(
        response=response_text,
        session_id=session.session_id,
        tool_calls=tool_calls,
    )


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
    )

