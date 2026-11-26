"""
Corporate Travel Policy Engine (SOW Section 4.2).

Governs city categorization, lodging limits, daily food allowances,
and commercial policy compliance.
"""
from __future__ import annotations

import re
from typing import Any

from app.core.enums import (
    CityTier,
    HotelOccupancy,
    FOOD_ALLOWANCE_PER_DAY,
    LOCAL_TRANSIT_UNBILLED_MAX,
)

# ---------------------------------------------------------------------------
# SOW Section 4.2: City Categorization
# ---------------------------------------------------------------------------

CATEGORY_A_CITIES: frozenset[str] = frozenset({
    "mumbai",
    "bombay",
    "delhi",
    "new delhi",
    "ncr",
    "kolkata",
    "calcutta",
    "chennai",
    "madras",
    "bengaluru",
    "bangalore",
    "hyderabad",
    "secunderabad",
})

CATEGORY_B_CITIES: frozenset[str] = frozenset({
    "amravati",
    "itanagar",
    "dispur",
    "patna",
    "raipur",
    "panaji",
    "panjim",
    "gandhinagar",
    "chandigarh",
    "shimla",
    "ranchi",
    "thiruvananthapuram",
    "trivandrum",
    "bhopal",
    "imphal",
    "shillong",
    "aizawl",
    "guwahati",
    "kohima",
    "bhubaneswar",
    "jaipur",
    "gangtok",
    "agartala",
    "lucknow",
    "dehradun",
    # Common tier-2 hubs
    "pune",
    "ahmedabad",
    "surat",
    "nagpur",
    "indore",
    "visakhapatnam",
    "vizag",
    "vadodara",
    "kochi",
    "cochin",
    "coimbatore",
})

# ---------------------------------------------------------------------------
# SOW Section 4.2: Lodging Caps (Exclusive of Taxes, per room per night)
# ---------------------------------------------------------------------------

LODGING_LIMITS: dict[CityTier, dict[HotelOccupancy, int]] = {
    CityTier.CATEGORY_A: {
        HotelOccupancy.SINGLE: 1500,
        HotelOccupancy.DOUBLE: 2000,
        HotelOccupancy.TRIPLE: 2500,
    },
    CityTier.CATEGORY_B: {
        HotelOccupancy.SINGLE: 1000,
        HotelOccupancy.DOUBLE: 1500,
        HotelOccupancy.TRIPLE: 2000,
    },
    CityTier.CATEGORY_C: {
        HotelOccupancy.SINGLE: 750,
        HotelOccupancy.DOUBLE: 1200,
        HotelOccupancy.TRIPLE: 1500,
    },
}


def normalize_city_name(city: str | None) -> str:
    """Normalize city string for policy lookup (lowercased, punctuation removed)."""
    if not city:
        return ""
    # Strip whitespace, punctuation, commas
    cleaned = re.sub(r"[^\w\s]", " ", city).lower()
    return " ".join(cleaned.split())


def classify_city(city: str | None) -> CityTier:
    """Classify a city into Category A, B, or C according to SOW Section 4.2."""
    norm = normalize_city_name(city)
    if not norm:
        return CityTier.CATEGORY_C

    # Direct match
    if norm in CATEGORY_A_CITIES:
        return CityTier.CATEGORY_A
    if norm in CATEGORY_B_CITIES:
        return CityTier.CATEGORY_B

    # Token-level match (e.g., "South Delhi", "Bengaluru Central", "Lucknow Cantt")
    tokens = set(norm.split())
    if tokens & CATEGORY_A_CITIES:
        return CityTier.CATEGORY_A
    if tokens & CATEGORY_B_CITIES:
        return CityTier.CATEGORY_B

    return CityTier.CATEGORY_C


def lodging_ceiling(city: str | None, occupancy: HotelOccupancy = HotelOccupancy.SINGLE) -> int:
    """Get the maximum nightly lodging rate cap (exclusive of taxes) for the given city & occupancy."""
    tier = classify_city(city)
    tier_limits = LODGING_LIMITS.get(tier, LODGING_LIMITS[CityTier.CATEGORY_C])
    return tier_limits.get(occupancy, tier_limits[HotelOccupancy.SINGLE])


def validate_lodging_rate(
    city: str | None,
    nightly_rate: float | int,
    occupancy: HotelOccupancy = HotelOccupancy.SINGLE,
) -> dict[str, Any]:
    """Validate a proposed hotel rate against corporate policy limits."""
    tier = classify_city(city)
    limit = lodging_ceiling(city, occupancy)
    is_compliant = nightly_rate <= limit
    excess = max(0.0, float(nightly_rate) - float(limit))

    return {
        "city": city or "Unknown",
        "tier": tier,
        "occupancy": occupancy,
        "rate": float(nightly_rate),
        "limit": limit,
        "is_compliant": is_compliant,
        "excess": excess,
        "message": (
            f"Within policy limit of ₹{limit}/night for {tier.value}"
            if is_compliant
            else f"Exceeds {tier.value} policy cap of ₹{limit}/night by ₹{excess:.2f}"
        ),
    }
