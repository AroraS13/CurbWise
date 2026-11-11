"""Structured evidence aggregation, classification, leads, and writer packets."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .asp import asp_suspended_on, fetch_asp_status_today, fetch_asp_suspensions
from .candidates import find_candidates_near, summarize_candidate_blocks
from .closures import fetch_closures_near
from .diagnostics import candidate_diagnostic
from .leads import LeadStatus, assess_lead, rank_leads
from .locations import associate_signs_to_meter
from .models import AspSuspension, ParkingStatus
from .parse_regs import parse_meter_schedule_record, parse_sign_record
from .parking_rules import NYC_TZ, evaluate_candidate
from .request import ParkingRequest
from .signs import fetch_signs_near_point


def discover_candidates(req: ParkingRequest) -> dict[str, Any]:
    """Geocode and list nearby approximate street-side candidates."""
    if not req.destination:
        return {"found": False, "error": "destination_required"}
    bundle = find_candidates_near(
        req.destination,
        radius_m=req.radius_m,
        sign_limit=None,  # paginate to completion within safety caps
        meter_limit=250,
        closure_limit=50,
        max_candidates=req.max_candidates,
        prefer_meter_points=False,
    )
    if not bundle.get("found"):
        return bundle
    candidates = bundle.get("candidates") or []
    return {
        "found": True,
        "request": req.to_dict(),
        "destination": bundle.get("destination"),
        "geocode": {
            "query": (bundle.get("geocode") or {}).get("query"),
            "found": True,
            "best": bundle.get("destination"),
            "confidence": (bundle.get("destination") or {}).get("confidence"),
            "source": (bundle.get("geocode") or {}).get("source"),
        },
        "radius_m": bundle.get("radius_m"),
        "counts": bundle.get("counts"),
        "truncated": bundle.get("truncated"),
        "sources": bundle.get("sources"),
        "limitations": bundle.get("limitations"),
        "candidates": candidates,
        "candidate_summaries": summarize_candidate_blocks(candidates, top_n=req.max_candidates),
        "overlapping_stretches": bundle.get("overlapping_stretches"),
        "all_candidate_count": (bundle.get("counts") or {}).get("street_side_candidates")
        or len(candidates),
        "capped": False,
    }


def research_signs_for_candidates(
    candidates: list[dict[str, Any]],
    *,
    center: dict[str, Any],
    radius_m: float,
) -> dict[str, Any]:
    """Per-candidate sign research. Meter points get a local fetch; stretches keep attached signs."""
    out_candidates = []
    parsed_all = []
    any_trunc = False
    for cand in candidates:
        c = dict(cand)
        retrieval = dict(c.get("retrieval") or {})
        if c.get("candidate_kind") == "METER_POINT" and c.get("lat") is not None and c.get("lon") is not None:
            local = fetch_signs_near_point(c["lat"], c["lon"], radius_m=min(80.0, radius_m))
            c = associate_signs_to_meter(c, local["records"], max_distance_m=60.0)
            retrieval["signs_truncated"] = bool(local.get("truncated"))
            # Unknown completeness stays None — never coerce unknown to complete.
            if local.get("truncated"):
                retrieval["signs_complete"] = False
            elif local.get("complete") is True:
                retrieval["signs_complete"] = True
            elif local.get("complete") is False:
                retrieval["signs_complete"] = False
            else:
                retrieval["signs_complete"] = None
            retrieval["signs_fetched"] = local.get("count")
            retrieval["signs_matched"] = len(c.get("signs") or [])
            retrieval["signs_unmatched_nearby"] = len(c.get("nearby_signs_unmatched") or [])
            if local.get("truncated"):
                any_trunc = True
        elif c.get("candidate_kind") == "STREET_SIDE":
            # Discovery performs one complete area query and conservative segment-side join.
            retrieval.setdefault("signs_truncated", False)
            retrieval.setdefault("signs_complete", None)
            retrieval["signs_matched"] = len(c.get("signs") or [])
            retrieval["signs_unmatched_nearby"] = len(c.get("nearby_signs_unmatched") or [])
        else:
            # Legacy stretch: completeness unknown unless separately queried.
            retrieval.setdefault("signs_truncated", False)
            retrieval.setdefault("signs_complete", None)
        c["retrieval"] = retrieval
        parsed = []
        for s in c.get("signs") or []:
            reg = parse_sign_record(s)
            parsed.append(reg.to_dict())
            parsed_all.append(reg.to_dict())
        c["parsed_signs"] = parsed
        out_candidates.append(c)

    return {
        "specialty": "signs",
        "source": "https://data.cityofnewyork.us/d/nfid-uabd",
        "truncated": any_trunc,
        "fetched_count": sum(len(c.get("signs") or []) for c in out_candidates),
        "candidates": out_candidates,
        "parsed_regulations": parsed_all,
        "gaps": _sign_gaps(parsed_all, any_trunc),
        "note": "Sign truncation is tracked per candidate; one incomplete area does not invalidate all leads.",
    }


def research_meters_for_candidates(
    candidates: list[dict[str, Any]],
    *,
    center: dict[str, Any],
    radius_m: float,
) -> dict[str, Any]:
    """Parse meters already associated with candidates and flag completeness."""
    out_candidates = []
    parsed = []
    fetched_count = 0
    for cand in candidates:
        c = dict(cand)
        retrieval = dict(c.get("retrieval") or {})
        # Only touch meter fields — do not reset signs_complete / signs_truncated.
        retrieval["meters_complete"] = True
        retrieval["meters_truncated"] = False
        c["retrieval"] = retrieval
        # Avoid wiping richer sign-research fields on merge: only emit meter-specific keys.
        cand_parsed = []
        for m in c.get("meters") or []:
            fetched_count += 1
            for reg in parse_meter_schedule_record(m):
                d = reg.to_dict()
                cand_parsed.append(d)
                parsed.append(d)
        c["parsed_meters"] = cand_parsed
        out_candidates.append(
            {
                "candidate_id": c.get("candidate_id"),
                "block_key": c.get("block_key"),
                "metering_classification": c.get("metering_classification") or (
                    "METERED" if c.get("meters") else "UNKNOWN"
                ),
                "metering_basis": c.get("metering_basis"),
                "meters": c.get("meters") or [],
                "parsed_meters": cand_parsed,
                "retrieval": {
                    "meters_complete": True,
                    "meters_truncated": False,
                },
            }
        )
    return {
        "specialty": "meters",
        "source": "https://data.cityofnewyork.us/d/693u-uax6",
        "truncated": False,
        "fetched_count": fetched_count,
        "candidates": out_candidates,
        "parsed_regulations": parsed,
        "gaps": [
            "Meter records are point evidence; they do not prove full block-side coverage.",
            "A missing associated meter record does not establish free or legal parking.",
        ],
    }


def research_exceptions(
    req: ParkingRequest,
    *,
    center: dict[str, Any],
    radius_m: float,
) -> dict[str, Any]:
    if not req.arrival:
        return {"specialty": "exceptions", "error": "arrival_required"}

    year = req.arrival.year
    asp = fetch_asp_suspensions(year=year)
    day = req.arrival.date()
    day_status = asp_suspended_on(day, asp["suspensions"])

    today = datetime.now(NYC_TZ).date()
    evaluate_is_today = day == today and (req.departure.date() == today if req.departure else True)
    live = None
    live_note = None
    if evaluate_is_today:
        try:
            live = fetch_asp_status_today()
        except Exception as e:
            live_note = f"Could not fetch live 311 ASP status: {type(e).__name__}: {e}"
    else:
        live_note = (
            "Today-only 311 ASP status was not fetched/applied because the requested "
            "arrival date is not today in America/New_York."
        )

    closures = fetch_closures_near(center["lat"], center["lon"], radius_m=radius_m, limit=50)
    relevant_closures = [
        c
        for c in closures["records"]
        if _permit_overlaps_request(c, req)
    ]
    advisories = [
        {**c, "evidence_type": "possible_restriction_only", "verified_active_closure": False}
        for c in relevant_closures
    ]
    suspensions = [
        AspSuspension(
            date=s["date"],
            holiday=s["holiday"],
            major_legal_holiday=bool(s.get("major_legal_holiday")),
            source=s.get("source"),
        ).__dict__
        for s in asp["suspensions"]
    ]
    return {
        "specialty": "exceptions",
        "asp_calendar": {
            "source": asp["source"],
            "year": year,
            "count": asp["count"],
            "notes": asp["notes"],
            "suspensions": suspensions,
            "arrival_day": day_status,
        },
        "asp_live_today": live,
        "asp_live_note": live_note,
        "closures": {
            "source": closures["source"],
            "truncated": closures.get("truncated", False),
            "caveat": closures.get("caveat"),
            "advisories": advisories,
            "excluded_outside_interval": len(closures["records"]) - len(relevant_closures),
        },
        "gaps": [
            "Holiday ASP calendar does not cover weather/emergency suspensions.",
            "Construction permits are advisories, not verified active closures; permits outside the requested date are excluded.",
            *([live_note] if live_note else []),
        ],
    }


def merge_research_evidence(
    req: ParkingRequest,
    discovery: dict[str, Any],
    sign_research: dict[str, Any] | None = None,
    meter_research: dict[str, Any] | None = None,
    exception_research: dict[str, Any] | None = None,
) -> dict[str, Any]:
    by_id: dict[str, dict[str, Any]] = {}
    for c in discovery.get("candidates") or []:
        key = c.get("candidate_id") or c.get("block_key")
        by_id[key] = dict(c)

    def _merge(research: dict[str, Any] | None) -> None:
        if not research:
            return
        for c in research.get("candidates") or []:
            key = c.get("candidate_id") or c.get("block_key")
            if key not in by_id:
                continue
            existing = by_id[key]
            for k, v in c.items():
                if v is None:
                    continue
                if k == "retrieval" and isinstance(v, dict):
                    # Deep-merge retrieval so meters research cannot wipe signs_complete.
                    merged_ret = dict(existing.get("retrieval") or {})
                    for rk, rv in v.items():
                        if rv is not None:
                            merged_ret[rk] = rv
                    existing["retrieval"] = merged_ret
                else:
                    existing[k] = v

    _merge(sign_research)
    _merge(meter_research)

    if exception_research:
        advisories = ((exception_research.get("closures") or {}).get("advisories")) or []
        for c in by_id.values():
            on = (c.get("on_street") or "").upper()
            matched = [a for a in advisories if (a.get("on_street") or "").upper() == on]
            if matched:
                c["closures"] = matched

    # Global truncated flag is informational only; per-candidate retrieval drives legality.
    global_trunc = {
        "signs": bool((sign_research or {}).get("truncated") or (discovery.get("truncated") or {}).get("signs")),
        "meters": bool((discovery.get("truncated") or {}).get("meters")),
        "closures": bool(((exception_research or {}).get("closures") or {}).get("truncated")),
    }

    gaps: list[str] = []
    gaps.extend(discovery.get("limitations") or [])
    for block in (sign_research, meter_research, exception_research):
        if block:
            gaps.extend(block.get("gaps") or [])

    return {
        "request": req.to_dict(),
        "geocode": discovery.get("geocode") or discovery.get("destination"),
        "coordinates": discovery.get("destination"),
        "candidates": list(by_id.values()),
        "sign_research": _public_research(sign_research),
        "meter_research": _public_research(meter_research),
        "exception_research": exception_research,
        "retrieval_completeness": {
            "truncated": global_trunc,
            "complete": not any(global_trunc.values()),
            "per_candidate": True,
        },
        "sources": discovery.get("sources") or {},
        "evidence_gaps": list(dict.fromkeys(g for g in gaps if g)),
        "overlapping_stretches": discovery.get("overlapping_stretches") or [],
        "notes": [
            "CSCL-derived physical street sides are the primary investigation units.",
            "Displayed street-side geometry is an approximate centerline offset, not an exact curb boundary.",
            "Meters are secondary point evidence and missing meters do not imply free parking.",
            "Legal status and research-lead status are separate assessments.",
            "Missing records do not imply permission to park.",
        ],
    }


def classify_evidence_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Legal classification + independent lead ranking."""
    req = bundle.get("request") or {}
    arrival = req.get("arrival")
    departure = req.get("departure")
    if not arrival or not departure:
        return {"error": "incomplete_request", "classifications": [], "leads": []}

    arrival_dt = datetime.fromisoformat(arrival)
    departure_dt = datetime.fromisoformat(departure)
    if arrival_dt.tzinfo is None:
        arrival_dt = arrival_dt.replace(tzinfo=NYC_TZ)
    if departure_dt.tzinfo is None:
        departure_dt = departure_dt.replace(tzinfo=NYC_TZ)

    suspensions = ((bundle.get("exception_research") or {}).get("asp_calendar") or {}).get("suspensions") or []
    live = (bundle.get("exception_research") or {}).get("asp_live_today") or {}
    vehicle = req.get("vehicle_category") or "passenger"

    classifications = []
    lead_rows = []
    for cand in bundle.get("candidates") or []:
        retrieval = cand.get("retrieval") or {}
        # Per-candidate truncation only — do not inherit unrelated area truncation.
        signs_trunc = bool(retrieval.get("signs_truncated"))
        meters_trunc = bool(retrieval.get("meters_truncated"))
        # Meter coordinates establish an investigation anchor, NOT verified curb scope.
        # STREET_SIDE_MATCHED ≠ SCOPE_VERIFIED; never force location_scope_established here.
        signs_complete = retrieval.get("signs_complete")  # may be True/False/None

        decision = evaluate_candidate(
            cand,
            arrival_dt,
            departure_dt,
            asp_suspensions=suspensions,
            signs_truncated=signs_trunc,
            meters_truncated=meters_trunc,
            closures_truncated=False,
            asp_status_today=(live.get("status") if live else None),
            asp_status_today_source=(live.get("source") if live else None),
            location_scope_established=None,  # infer from FULL_SEGMENT curb regs only
            signs_complete=signs_complete,
            meters_complete=retrieval.get("meters_complete"),
            require_sign_retrieval_completeness=(cand.get("candidate_kind") == "METER_POINT"),
        )
        row = {
            "candidate_id": cand.get("candidate_id") or cand.get("block_key"),
            "candidate_kind": cand.get("candidate_kind"),
            "block_key": cand.get("block_key"),
            "status": decision.status.value,
            "decision": decision.to_dict(),
            "retrieval": retrieval,
            "diagnostic": candidate_diagnostic(cand, decision),
        }
        classifications.append(row)

        lead = assess_lead(
            cand,
            legal_status=decision.status.value,
            arrival=arrival_dt,
            departure=departure_dt,
            vehicle_category=vehicle,
        )
        lead_rows.append(lead)

    status_counts = {s.value: 0 for s in ParkingStatus}
    for row in classifications:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

    lead_counts = {s.value: 0 for s in LeadStatus}
    for lead in lead_rows:
        lead_counts[lead.status.value] = lead_counts.get(lead.status.value, 0) + 1

    top_leads = rank_leads(lead_rows, top_n=5)

    return {
        "classifications": classifications,
        "status_counts": status_counts,
        "leads": [a.to_dict() for a in lead_rows],
        "lead_counts": lead_counts,
        "top_leads": [a.to_dict() for a in top_leads],
        "classifier": "nyc.parking_rules.evaluate_candidate",
        "lead_ranker": "nyc.leads.assess_lead",
        "note": (
            "Legal statuses are produced by the deterministic rule engine. "
            "Lead statuses are research priorities only — not legal determinations. "
            "POTENTIALLY_PERMITTED / PROMISING_LEAD are not guarantees of legality or availability."
        ),
    }


def build_writer_packet(bundle: dict[str, Any], classifications: dict[str, Any]) -> dict[str, Any]:
    """Deterministic recommendation cards + constraints the Writer must follow."""
    class_by_id = {
        (c.get("candidate_id") or c.get("block_key")): c for c in classifications.get("classifications") or []
    }
    lead_by_id = {a.get("candidate_id"): a for a in classifications.get("leads") or []}

    cards = []
    for lead in classifications.get("top_leads") or []:
        cid = lead.get("candidate_id")
        legal = class_by_id.get(cid) or {}
        cand = next(
            (c for c in (bundle.get("candidates") or []) if (c.get("candidate_id") or c.get("block_key")) == cid),
            {},
        )
        exact_sign_ids = {
            sign.get("order_number")
            for sign in (cand.get("signs") or [])
            if sign.get("block_key") == cand.get("block_key")
        }
        rule_findings = (legal.get("decision") or {}).get("rule_findings") or []
        if cand.get("candidate_kind") == "STREET_SIDE":
            rule_findings = [
                finding
                for finding in rule_findings
                if not str(finding.get("source_id") or "").startswith(("P-", "S-"))
            ]
        elif exact_sign_ids:
            rule_findings = [
                finding for finding in rule_findings if finding.get("source_id") in exact_sign_ids
            ]
        cards.append(
            {
                "rank_status": lead.get("status"),
                "score": lead.get("score"),
                "legal_status": legal.get("status") or lead.get("legal_status"),
                "candidate_id": cid,
                "candidate_kind": cand.get("candidate_kind"),
                "physical_id": cand.get("physical_id"),
                "blockface_id": cand.get("blockface_id"),
                "meter_number": lead.get("meter_number") or cand.get("meter_number"),
                "lat": lead.get("lat"),
                "lon": lead.get("lon"),
                "distance_m": lead.get("distance_m"),
                "on_street": cand.get("on_street"),
                "side_of_street": cand.get("side_of_street"),
                "from_street": cand.get("from_street"),
                "to_street": cand.get("to_street"),
                "geometry_is_approximate": cand.get("geometry_is_approximate"),
                "metering_classification": cand.get("metering_classification") or (
                    "METERED" if cand.get("meters") else "UNKNOWN"
                ),
                "metering_basis": cand.get("metering_basis"),
                "meter_hours": (cand.get("meters") or [{}])[0].get("meter_hours") if cand.get("meters") else cand.get("meter_hours"),
                "rule_findings": rule_findings,
                "reasons": lead.get("reasons"),
                "verification_tasks": lead.get("verification_tasks"),
                "supporting_sources": (legal.get("decision") or {}).get("supporting_sources"),
                "sample_sign_ids": [
                    {
                        "id": s.get("order_number"),
                        "url": s.get("source"),
                        "description": s.get("sign_description"),
                    }
                    for s in (cand.get("signs") or [])[:4]
                ],
            }
        )

    avoid = [a for a in classifications.get("leads") or [] if a.get("status") == LeadStatus.AVOID.value]

    restrictions = []
    for c in classifications.get("classifications") or []:
        cid = c.get("candidate_id")
        cand = next(
            (
                item
                for item in (bundle.get("candidates") or [])
                if (item.get("candidate_id") or item.get("block_key")) == cid
            ),
            {},
        )
        exact_sign_ids = {
            sign.get("order_number")
            for sign in (cand.get("signs") or [])
            if sign.get("block_key") == cand.get("block_key")
        }
        for reg in (c.get("decision") or {}).get("applied_regulations") or []:
            if exact_sign_ids and reg.get("source_id") not in exact_sign_ids:
                continue
            if reg.get("kind") in {
                "NO_PARKING",
                "NO_STANDING",
                "NO_STOPPING",
                "ASP_STREET_CLEANING",
            }:
                restrictions.append(
                    {
                        "candidate_id": c.get("candidate_id"),
                        "kind": reg.get("kind"),
                        "raw_description": reg.get("raw_description"),
                        "source_id": reg.get("source_id"),
                        "source_url": reg.get("source_url"),
                        "scope_certainty": reg.get("scope_certainty"),
                        "on_street": cand.get("on_street"),
                        "side_of_street": cand.get("side_of_street"),
                        "from_street": cand.get("from_street"),
                        "to_street": cand.get("to_street"),
                    }
                )

    nearby_meters = []
    seen_meter_ids = set()
    for cand in bundle.get("candidates") or []:
        for meter in cand.get("meters") or []:
            meter_id = meter.get("meter_number")
            if not meter_id or meter_id in seen_meter_ids:
                continue
            seen_meter_ids.add(meter_id)
            nearby_meters.append(
                {
                    "meter_number": meter_id,
                    "status": meter.get("status"),
                    "meter_hours": meter.get("meter_hours"),
                    "on_street": meter.get("on_street"),
                    "side_of_street": meter.get("side_of_street"),
                    "from_street": meter.get("from_street"),
                    "to_street": meter.get("to_street"),
                    "source_url": meter.get("source"),
                }
            )

    meter_groups: dict[tuple, dict[str, Any]] = {}
    for meter in nearby_meters:
        key = (
            meter.get("status"),
            meter.get("meter_hours"),
            meter.get("on_street"),
            meter.get("side_of_street"),
            meter.get("from_street"),
            meter.get("to_street"),
        )
        group = meter_groups.setdefault(
            key,
            {
                "meter_numbers": [],
                "status": meter.get("status"),
                "meter_hours": meter.get("meter_hours"),
                "on_street": meter.get("on_street"),
                "side_of_street": meter.get("side_of_street"),
                "from_street": meter.get("from_street"),
                "to_street": meter.get("to_street"),
                "source_url": meter.get("source_url"),
            },
        )
        group["meter_numbers"].append(meter["meter_number"])

    source_segment_restrictions = []
    seen_sign_rules = set()
    for cand in bundle.get("candidates") or []:
        for sign in cand.get("signs") or []:
            reg = parse_sign_record(sign)
            if reg.kind.value not in {
                "NO_PARKING",
                "NO_STANDING",
                "NO_STOPPING",
                "ASP_STREET_CLEANING",
            }:
                continue
            key = (sign.get("order_number"), sign.get("sign_description"))
            if key in seen_sign_rules:
                continue
            seen_sign_rules.add(key)
            source_segment_restrictions.append(
                {
                    "source_id": sign.get("order_number"),
                    "source_url": sign.get("source"),
                    "kind": reg.kind.value,
                    "raw_description": sign.get("sign_description"),
                    "scope_certainty": reg.scope_certainty.value,
                    "on_street": sign.get("on_street"),
                    "side_of_street": sign.get("side_of_street"),
                    "from_street": sign.get("from_street"),
                    "to_street": sign.get("to_street"),
                }
            )

    packet = {
        "request": bundle.get("request"),
        "destination": bundle.get("coordinates"),
        "direct_answer_constraints": {
            "must_not_claim_no_legal_parking_from_insufficient_evidence": True,
            "insufficient_evidence_means": (
                "Legality could not be verified from available digital records — "
                "not that no parking exists."
            ),
            "lead_status_is_not_legal_status": True,
            "promising_lead_is_not_permission": True,
        },
        "status_counts": classifications.get("status_counts"),
        "lead_counts": classifications.get("lead_counts"),
        "recommendation_cards": cards,
        "avoid_locations": avoid[:8],
        "restrictions_to_watch": source_segment_restrictions[:40],
        "source_segment_restrictions": source_segment_restrictions,
        "nearby_meter_records": nearby_meters,
        "meter_schedule_groups": list(meter_groups.values()),
        "unverified": bundle.get("evidence_gaps"),
        "overlapping_stretches": bundle.get("overlapping_stretches"),
        "retrieval_completeness": bundle.get("retrieval_completeness"),
        "asp": {
            "arrival_day": ((bundle.get("exception_research") or {}).get("asp_calendar") or {}).get("arrival_day"),
            "live_note": (bundle.get("exception_research") or {}).get("asp_live_note"),
            "calendar_source": ((bundle.get("exception_research") or {}).get("asp_calendar") or {}).get("source"),
        },
        "report_outline": [
            "1. Direct answer: whether actionable investigation leads exist (not whether parking is guaranteed legal).",
            "2. Best street sides to investigate: use recommendation_cards only; describe known stay overlaps in plain English.",
            "3. Restrictions to watch: use restrictions_to_watch / avoid_locations.",
            "4. What remains unverified: concise, decision-relevant only.",
            "5. Evidence: cite source_id + source_url from cards.",
        ],
        "disclaimer": (
            "CurbWise is a research aid. Lead recommendations are investigation priorities, "
            "not determinations of legality or availability. Physical signs and temporary "
            "restrictions take precedence."
        ),
        "classifier_note": classifications.get("note"),
    }
    packet["validation"] = validate_writer_packet(packet)
    return packet


def validate_writer_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Deterministic checks the app can enforce / Writer must respect."""
    issues = []
    for card in packet.get("recommendation_cards") or []:
        if card.get("legal_status") == ParkingStatus.PROHIBITED.value and card.get("rank_status") in {
            LeadStatus.PROMISING_LEAD.value,
            LeadStatus.CONDITIONAL_LEAD.value,
        }:
            issues.append(f"Lead {card.get('candidate_id')} contradicts PROHIBITED legal status.")
        if card.get("rank_status") == LeadStatus.AVOID.value:
            issues.append(f"AVOID location listed as recommendation card: {card.get('candidate_id')}")
    banned_phrases_guidance = [
        "there are no legal street parking options",
        "no legal or guaranteed street parking",
        "no guaranteed or recommended safe street-parking",
    ]
    return {
        "ok": not issues,
        "issues": issues,
        "banned_conclusion_phrases": banned_phrases_guidance,
        "required_sections": packet.get("report_outline"),
    }


def validate_writer_text(markdown: str, packet: dict[str, Any]) -> dict[str, Any]:
    """Post-Writer guard: flag contradictory conclusions."""
    text = (markdown or "").lower()
    violations = []
    for phrase in (packet.get("validation") or {}).get("banned_conclusion_phrases") or []:
        if phrase in text:
            # Only illegal if we actually have leads or only insufficient evidence.
            leads = packet.get("recommendation_cards") or []
            counts = packet.get("status_counts") or {}
            if leads or counts.get("INSUFFICIENT_EVIDENCE", 0) > 0:
                if counts.get("PROHIBITED", 0) == sum(counts.values()) and not leads:
                    continue
                violations.append(f"Writer used banned overclaim phrase: {phrase!r}")
    for card in packet.get("recommendation_cards") or []:
        if card.get("legal_status") == "PROHIBITED" and card.get("meter_number"):
            # If writer praises a prohibited meter id as safe — soft check
            mid = str(card.get("meter_number"))
            if mid and mid in (markdown or "") and "promis" in text and "avoid" not in text:
                violations.append(f"Writer may be promoting prohibited meter {mid}.")
    return {"ok": not violations, "violations": violations}


def _sign_gaps(parsed: list[dict[str, Any]], truncated: bool) -> list[str]:
    gaps = []
    if truncated:
        gaps.append("At least one per-candidate sign query was truncated.")
    unparsed = sum(1 for p in parsed if p.get("parse_status") == "UNPARSED")
    if unparsed:
        gaps.append(f"{unparsed} sign description(s) were outside the supported parser subset.")
    unknown_scope = sum(
        1
        for p in parsed
        if p.get("scope_certainty") in {"UNKNOWN", "ARROW_PARTIAL", "DOUBLE_ARROW", "FULL_SEGMENT"}
    )
    if unknown_scope:
        gaps.append(
            f"{unknown_scope} sign regulation(s) have unverified curb applicability "
            "(arrow cues including <-> DOUBLE_ARROW are not SCOPE_VERIFIED)."
        )
    return gaps


def _permit_overlaps_request(permit: dict[str, Any], req: ParkingRequest) -> bool:
    """Keep dated permits only when their calendar span intersects the requested stay."""
    if not req.arrival or not req.departure:
        return True

    def _date(value: Any):
        if not value:
            return None
        text = str(value).strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            try:
                return datetime.strptime(text[:10], "%Y-%m-%d").date()
            except ValueError:
                return None

    start = _date(permit.get("work_start_date"))
    end = _date(permit.get("work_end_date"))
    if start is None and end is None:
        return True
    start = start or end
    end = end or start
    assert start is not None and end is not None
    return start <= req.departure.date() and end >= req.arrival.date()


def _public_research(block: dict[str, Any] | None) -> dict[str, Any] | None:
    if not block:
        return None
    return {
        "specialty": block.get("specialty"),
        "source": block.get("source"),
        "truncated": block.get("truncated"),
        "fetched_count": block.get("fetched_count"),
        "gaps": block.get("gaps"),
        "note": block.get("note"),
    }


def run_offline_pipeline(req: ParkingRequest) -> dict[str, Any]:
    discovery = discover_candidates(req)
    if not discovery.get("found"):
        return {"discovery": discovery, "error": "geocode_or_discovery_failed"}
    center = discovery["destination"]
    signs = research_signs_for_candidates(discovery["candidates"], center=center, radius_m=req.radius_m)
    meters = research_meters_for_candidates(discovery["candidates"], center=center, radius_m=req.radius_m)
    exceptions = research_exceptions(req, center=center, radius_m=req.radius_m)
    bundle = merge_research_evidence(req, discovery, signs, meters, exceptions)
    classifications = classify_evidence_bundle(bundle)
    packet = build_writer_packet(bundle, classifications)
    return {
        "discovery": discovery,
        "bundle": bundle,
        "classifications": classifications,
        "writer_packet": packet,
    }
