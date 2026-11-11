"""NYC street-parking data clients and helpers for CurbWise."""

from .candidates import find_candidates_near
from .geocode import geocode_destination
from .parking_rules import evaluate_candidate, evaluate_parking_interval

__all__ = [
    "geocode_destination",
    "find_candidates_near",
    "evaluate_candidate",
    "evaluate_parking_interval",
]
