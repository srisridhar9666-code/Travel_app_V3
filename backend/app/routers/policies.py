"""
Corporate Travel Policy API (SOW Section 4.2).

Exposes city tier classifications, lodging rate ceilings, food allowance caps,
and corporate compliance parameters.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import CurrentUser
from app.core.enums import (
    CityTier,
    CITY_TIER_LABELS,
    HotelOccupancy,
    FOOD_ALLOWANCE_PER_DAY,
    LOCAL_TRANSIT_UNBILLED_MAX,
)
from app.services import policy

router = APIRouter(prefix="/policies", tags=["policies"])


class LodgingCeilingResponse(BaseModel):
    city: str
    tier: CityTier
    tier_label: str
    occupancy: HotelOccupancy
    limit_per_night: int


class PolicyOverviewResponse(BaseModel):
    food_allowance_per_day: int
    local_transit_unbilled_max: int
    lodging_limits: dict[str, dict[str, int]]
    city_tier_labels: dict[str, str]


@router.get("/overview", response_model=PolicyOverviewResponse)
def get_policy_overview(user: CurrentUser) -> PolicyOverviewResponse:
    """Retrieve full corporate policy parameters, lodging matrices, and expense caps."""
    return PolicyOverviewResponse(
        food_allowance_per_day=FOOD_ALLOWANCE_PER_DAY,
        local_transit_unbilled_max=LOCAL_TRANSIT_UNBILLED_MAX,
        lodging_limits={
            tier.value: {occ.value: limit for occ, limit in occ_map.items()}
            for tier, occ_map in policy.LODGING_LIMITS.items()
        },
        city_tier_labels={tier.value: label for tier, label in CITY_TIER_LABELS.items()},
    )


@router.get("/lodging-ceiling", response_model=LodgingCeilingResponse)
def get_lodging_ceiling(
    user: CurrentUser,
    city: str = Query(..., description="Target city name"),
    occupancy: HotelOccupancy = Query(HotelOccupancy.SINGLE, description="Hotel room occupancy grade"),
) -> LodgingCeilingResponse:
    """Look up the corporate lodging limit and city tier for a destination city and occupancy grade."""
    tier = policy.classify_city(city)
    limit = policy.lodging_ceiling(city, occupancy)
    return LodgingCeilingResponse(
        city=city,
        tier=tier,
        tier_label=CITY_TIER_LABELS.get(tier, tier.value),
        occupancy=occupancy,
        limit_per_night=limit,
    )
