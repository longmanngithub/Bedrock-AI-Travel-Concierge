"""Reverse geocoding: browser coordinates -> a short "City, Country" string.

Uses Google's Geocoding API (the same Maps Platform key as crew/tools.py's
places_lookup — enable "Geocoding API" on that key in the console) via raw
httpx, consistent with how this codebase talks to every other third-party API.
`unset GOOGLE_PLACES_API_KEY` is a normal, expected state: callers check
`is_configured()` first and raise a clean E_GEOCODE_UNAVAILABLE.
"""
from __future__ import annotations

import httpx

from .config import get_settings
from .errors import AppError, ErrorCode

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def is_configured() -> bool:
    return bool(get_settings().google_places_api_key)


def _extract_locality(components: list[dict]) -> str | None:
    for comp in components:
        if "locality" in comp.get("types", []):
            return comp.get("long_name")
    # Fall back to the next-broadest administrative area (covers cities
    # Google doesn't tag as a "locality", e.g. some city-states/districts).
    for comp in components:
        if "administrative_area_level_1" in comp.get("types", []):
            return comp.get("long_name")
    return None


def _extract_country(components: list[dict]) -> str | None:
    for comp in components:
        if "country" in comp.get("types", []):
            return comp.get("long_name")
    return None


def reverse_geocode(lat: float, lon: float) -> str:
    """Raises AppError(E_GEOCODE_UNAVAILABLE | E_GEOCODE_FAILED) on any
    failure; never lets a Google response/exception reach the caller raw."""
    settings = get_settings()
    if not settings.google_places_api_key:
        raise AppError(ErrorCode.E_GEOCODE_UNAVAILABLE, log_detail="GOOGLE_PLACES_API_KEY unset")

    try:
        resp = httpx.get(
            _GEOCODE_URL,
            params={
                "latlng": f"{lat},{lon}",
                "result_type": "locality|administrative_area_level_1",
                "key": settings.google_places_api_key,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise AppError(ErrorCode.E_GEOCODE_FAILED, log_detail=f"geocode request failed: {exc}") from exc

    if data.get("status") != "OK":
        raise AppError(ErrorCode.E_GEOCODE_FAILED, log_detail=f"geocode status: {data.get('status')}")

    results = data.get("results") or []
    if not results:
        raise AppError(ErrorCode.E_GEOCODE_FAILED, log_detail="geocode returned no results")

    components = results[0].get("address_components", [])
    city = _extract_locality(components)
    country = _extract_country(components)
    place = ", ".join(p for p in (city, country) if p)
    if not place:
        raise AppError(ErrorCode.E_GEOCODE_FAILED, log_detail="geocode result had no locality/country")
    return place
