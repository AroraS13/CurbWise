"""Thin Socrata SODA client for NYC Open Data (with pagination)."""

from __future__ import annotations

import copy
import json
import os
import time
from collections import OrderedDict
from threading import RLock
from typing import Any

import httpx

SOCRATA_BASE = "https://data.cityofnewyork.us/resource"
DEFAULT_TIMEOUT = 60.0
USER_AGENT = "CurbWise/0.1 (Columbia Agentic AI; educational research)"
DEFAULT_PAGE_SIZE = 1000
MAX_PAGES = 20  # hard safety bound
CACHE_TTL_SECONDS = 300.0
CACHE_MAX_ENTRIES = 128
_CACHE: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()
_CACHE_LOCK = RLock()


class SocrataError(RuntimeError):
    pass


def _cache_key(dataset_id: str, params: dict[str, Any], timeout: float) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return f"{dataset_id}|{timeout}|{canonical}"


def _cache_get(key: str) -> list[dict] | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        stored_at, rows = entry
        if now - stored_at > CACHE_TTL_SECONDS:
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return copy.deepcopy(rows)


def _cache_put(key: str, rows: list[dict]) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), copy.deepcopy(rows))
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_MAX_ENTRIES:
            _CACHE.popitem(last=False)


def clear_soda_cache() -> None:
    """Clear process-local request cache (primarily for tests and diagnostics)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    token = os.environ.get("NYC_OPENDATA_APP_TOKEN") or os.environ.get("SOCRATA_APP_TOKEN")
    if token:
        headers["X-App-Token"] = token
    return headers


def soda_get(dataset_id: str, params: dict[str, Any] | None = None, *, timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    """GET one page of rows from a Socrata dataset."""
    url = f"{SOCRATA_BASE}/{dataset_id}.json"
    request_params = params or {}
    cache_key = _cache_key(dataset_id, request_params, timeout)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        response = httpx.get(url, params=request_params, headers=_headers(), timeout=timeout)
        if response.status_code == 429:
            # One polite retry on rate limit.
            time.sleep(1.5)
            response = httpx.get(url, params=request_params, headers=_headers(), timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise SocrataError(f"Socrata request failed for {dataset_id}: {type(e).__name__}: {e}") from e
    data = response.json()
    if isinstance(data, dict) and data.get("error"):
        raise SocrataError(f"Socrata error for {dataset_id}: {data}")
    if not isinstance(data, list):
        raise SocrataError(f"Unexpected Socrata payload for {dataset_id}: {type(data)}")
    _cache_put(cache_key, data)
    return copy.deepcopy(data)


def soda_get_paginated(
    dataset_id: str,
    params: dict[str, Any] | None = None,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_rows: int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Fetch rows with $limit/$offset pagination.

    Returns records plus completeness metadata. Does not silently discard rows.
    """
    base = dict(params or {})
    order = base.get("$order") or ":id"
    base["$order"] = order
    page_size = max(1, min(int(page_size), DEFAULT_PAGE_SIZE))

    rows: list[dict] = []
    offset = 0
    pages = 0
    hit_page_cap = False
    while pages < MAX_PAGES:
        limit = page_size
        if max_rows is not None:
            remaining = max_rows - len(rows)
            if remaining <= 0:
                break
            limit = min(page_size, remaining)
        page_params = {**base, "$limit": limit, "$offset": offset}
        page = soda_get(dataset_id, page_params, timeout=timeout)
        pages += 1
        rows.extend(page)
        if len(page) < limit:
            # Last page — complete for this query.
            return {
                "records": rows,
                "pages": pages,
                "page_size": page_size,
                "truncated": False,
                "complete": True,
                "hit_page_cap": False,
            }
        offset += len(page)
        if max_rows is not None and len(rows) >= max_rows:
            # Caller-imposed bound; may still be more rows in SODA.
            return {
                "records": rows,
                "pages": pages,
                "page_size": page_size,
                "truncated": True,
                "complete": False,
                "hit_page_cap": False,
                "bound": max_rows,
            }
    hit_page_cap = True
    return {
        "records": rows,
        "pages": pages,
        "page_size": page_size,
        "truncated": True,
        "complete": False,
        "hit_page_cap": hit_page_cap,
    }


def dataset_page_url(dataset_id: str) -> str:
    return f"https://data.cityofnewyork.us/d/{dataset_id}"
