"""
Best-effort reverse geocoding for in-transit cab telemetry and hotel GPS coordinates.

Uses OpenStreetMap Nominatim with a strict short timeout and an in-memory cache so
that telemetry capture never fails or stalls when offline or rate-limited.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from decimal import Decimal
from functools import lru_cache

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
TIMEOUT_SECONDS = 2.5


@lru_cache(maxsize=1024)
def _lookup_cached(lat_key: str, lng_key: str) -> str | None:
    params = urllib.parse.urlencode(
        {
            "format": "jsonv2",
            "lat": lat_key,
            "lon": lng_key,
            "zoom": "16",
            "addressdetails": "1",
        }
    )
    req = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": "SriyatraTravelOps/1.0 (+https://designboxed.com)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            if resp.status != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.debug("Reverse geocode skipped for (%s, %s): %s", lat_key, lng_key, exc)
        return None

    addr = payload.get("address") or {}
    area = (
        addr.get("neighbourhood")
        or addr.get("suburb")
        or addr.get("city_district")
        or addr.get("road")
        or addr.get("hamlet")
    )
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("county")
        or addr.get("state_district")
    )
    state = addr.get("state")

    parts: list[str] = []
    for piece in (area, city, state):
        if piece and piece not in parts:
            parts.append(str(piece).strip())

    if parts:
        return ", ".join(parts)[:255]

    display = payload.get("display_name")
    if isinstance(display, str) and display.strip():
        return display.strip()[:255]
    return None


def reverse_geocode(lat: float | Decimal | None, lng: float | Decimal | None) -> str | None:
    """Resolve (lat, lng) into a readable place name, or None if unavailable."""
    if lat is None or lng is None:
        return None
    try:
        lat_val = round(float(lat), 4)
        lng_val = round(float(lng), 4)
    except (TypeError, ValueError):
        return None
    return _lookup_cached(f"{lat_val:.4f}", f"{lng_val:.4f}")
