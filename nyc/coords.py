"""NYC State Plane (EPSG:2263, NAD83 / New York Long Island, US survey feet)."""

from __future__ import annotations

import math

# GRS80 ellipsoid
_A = 6378137.0
_F = 1 / 298.257222101
_E2 = 2 * _F - _F * _F
_E = math.sqrt(_E2)

# Lambert Conformal Conic parameters for EPSG:2263
_LAT0 = math.radians(40.16666666666666)
_LON0 = math.radians(-74.0)
_PHI1 = math.radians(40.66666666666666)
_PHI2 = math.radians(41.03333333333333)
_FE = 984250.0
_FN = 0.0
_M_TO_FT = 3937.0 / 1200.0  # meters -> US survey feet


def _t(phi: float) -> float:
    s = math.sin(phi)
    return math.tan(math.pi / 4 - phi / 2) / (((1 - _E * s) / (1 + _E * s)) ** (_E / 2))


def _m(phi: float) -> float:
    return math.cos(phi) / math.sqrt(1 - _E2 * math.sin(phi) ** 2)


def _lcc_constants() -> tuple[float, float, float]:
    m1, m2 = _m(_PHI1), _m(_PHI2)
    t1, t2, t0 = _t(_PHI1), _t(_PHI2), _t(_LAT0)
    n = (math.log(m1) - math.log(m2)) / (math.log(t1) - math.log(t2))
    f = m1 / (n * t1**n)
    rho0 = _A * f * t0**n
    return n, f, rho0


_N, _F_LCC, _RHO0 = _lcc_constants()


def latlon_to_ny_state_plane(lat: float, lon: float) -> tuple[float, float]:
    """Return (x, y) in US survey feet for EPSG:2263."""
    phi = math.radians(lat)
    lam = math.radians(lon)
    rho = _A * _F_LCC * _t(phi) ** _N
    theta = _N * (lam - _LON0)
    x = _FE + (rho * math.sin(theta)) * _M_TO_FT
    y = _FN + (_RHO0 - rho * math.cos(theta)) * _M_TO_FT
    return x, y


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def feet_for_meters(meters: float) -> float:
    return meters * _M_TO_FT
