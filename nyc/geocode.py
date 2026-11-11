"""Destination geocoding via NYC Planning Labs GeoSearch (Pelias)."""

from __future__ import annotations

import copy
import re
import time
from collections import OrderedDict
from threading import RLock
from typing import Any

import httpx

from .socrata import USER_AGENT

GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"
SOURCE_URL = "https://geosearch.planninglabs.nyc/docs/"
_CACHE_TTL_SECONDS = 600.0
_CACHE_MAX_ENTRIES = 64
_CACHE: OrderedDict[tuple[str, int], tuple[float, dict[str, Any]]] = OrderedDict()
_CACHE_LOCK = RLock()


class GeocodeError(RuntimeError):
    pass


def clear_geocode_cache() -> None:
    """Clear process-local cache (primarily for tests and diagnostics)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def geocode_destination(text: str, *, size: int = 5) -> dict[str, Any]:
    """Geocode an NYC address or place name.

    Returns a structured result with the top match and alternatives.
    Does not invent coordinates: empty features become an explicit miss.
    """
    query = (text or "").strip()
    if not query:
        raise GeocodeError("Destination text is empty.")
    cache_key = (re.sub(r"\s+", " ", query).casefold(), int(size))
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] <= _CACHE_TTL_SECONDS:
            _CACHE.move_to_end(cache_key)
            return copy.deepcopy(cached[1])
        if cached:
            _CACHE.pop(cache_key, None)

    try:
        response = httpx.get(
            GEOSEARCH_URL,
            params={"text": query, "size": size},
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as e:
        raise GeocodeError(f"GeoSearch failed: {type(e).__name__}: {e}") from e

    features = payload.get("features") or []
    results = [_feature_to_result(f) for f in features]
    best = results[0] if results else None
    result = {
        "query": query,
        "found": best is not None,
        "best": best,
        "results": results,
        "source": SOURCE_URL,
        "request_url": str(response.url),
    }
    with _CACHE_LOCK:
        _CACHE[cache_key] = (time.monotonic(), copy.deepcopy(result))
        _CACHE.move_to_end(cache_key)
        while len(_CACHE) > _CACHE_MAX_ENTRIES:
            _CACHE.popitem(last=False)
    return result


def _feature_to_result(feature: dict) -> dict[str, Any]:
    props = feature.get("properties") or {}
    coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
    lon, lat = coords[0], coords[1]
    return {
        "label": props.get("label") or props.get("name"),
        "name": props.get("name"),
        "lat": lat,
        "lon": lon,
        "borough": props.get("borough"),
        "neighbourhood": props.get("neighbourhood"),
        "postalcode": props.get("postalcode"),
        "confidence": props.get("confidence"),
        "layer": props.get("layer"),
        "gid": props.get("gid"),
    }
