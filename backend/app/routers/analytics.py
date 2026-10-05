"""
Cost entry and reporting (SOW sections 2 and 6, addendum B5 / C1).

Both SOW sections were unbuildable as written because nothing said where a
number comes from. C1's recommendation is what is built here:

* an **admin** enters the cost, at booking, pre-filled from the ticket;
* the currency is **INR**;
* a shared cab or room **splits evenly**, with a manual override.

Cost is admin-only to read as well as to write. A ground-staff member seeing
what a colleague's flight cost is a personnel problem nobody asked for, and
nothing in section 6 needs it.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core import clock
from app.core.deps import AdminUser, DbSession
from app.core.enums import AuditAction
from app.models.request import RequestTraveller, TravelRequest
from app.schemas.analytics import (
    AnalyticsBundle,
    CampaignSpend,
    CostEntry,
    CostPreview,
    CostPreviewRow,
    CostSplit,
    DeploymentRow,
    Overview,
    PersonSpend,
    PlaceSpend,
    TrendPoint,
    TypeSpend,
    UncostedRow,
)
from app.routers.insights import ReportFilters
from app.schemas.request import RequestRead
from app.services import analytics, audit, cost_entry, costs, invoices, notifications
from app.services import requests as svc

router = APIRouter(tags=["analytics"])


def _load(db: Session, request_id: int, tenant_id: str) -> TravelRequest:
    row = db.get(TravelRequest, request_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    return row


def _travellers(row: TravelRequest, ids: list[int]) -> list[RequestTraveller]:
    by_id = {t.id: t for t in row.travellers}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Traveller(s) {', '.join(str(m) for m in missing)} are not on this request.",
        )
    return [by_id[i] for i in ids]


# ---------------------------------------------------------------------------
# Cost entry
# ---------------------------------------------------------------------------


@router.post("/requests/{request_id}/costs", response_model=RequestRead)
def set_costs(
    request_id: int,
    payload: CostEntry,
    actor: AdminUser,
    http_request: Request,
    background: BackgroundTasks,
    db: DbSession,
) -> RequestRead:
    """Record what each person's travel cost, and who was paid - the explicit,
    override path.

    Also the ordinary path for a single traveller, where "splitting" a cost one
    way would be a strange way to describe typing a number in. A cost billed on
    an approved invoice is locked; one on an invoice still being prepared
    carries its new amount onto that invoice.
    """
    row = _load(db, request_id, actor.tenant_id)

    ids = [a.traveller_id for a in payload.amounts]
    if len(ids) != len(set(ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The same traveller appears twice in this payload.",
        )
    travellers = _travellers(row, ids)
    for traveller in travellers:
        cost_entry.assert_costable(traveller)
    vendor = cost_entry.vendor_choice(
        db, actor.tenant_id, travellers,
        sent="vendor_id" in payload.model_fields_set, vendor_id=payload.vendor_id,
    )
    invoices.guard_cost_change(
        db,
        [
            (t, costs.to_money(e.amount) if e.amount is not None else None, vendor.id_for(t))
            for e, t in zip(payload.amounts, travellers, strict=True)
        ],
    )

    changes = [
        cost_entry.apply(db, traveller=traveller, amount=entry.amount, note=entry.note, actor=actor,
               vendor=vendor)
        for entry, traveller in zip(payload.amounts, travellers, strict=True)
    ]

    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="travel_request",
        entity_id=row.id,
        summary=f"{actor.full_name} recorded cost on request {row.id}{cost_entry.paid_to(vendor)}",
        changes={"costs": changes},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    queued = invoices.follow_costs(db, travellers, actor=actor, http_request=http_request)
    db.commit()
    background.add_task(notifications.deliver_queued, queued)
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=actor.tenant_id, viewer=actor)


@router.post("/requests/{request_id}/costs/preview", response_model=CostPreview)
def preview_split(
    request_id: int, payload: CostSplit, actor: AdminUser, db: DbSession
) -> CostPreview:
    """What an even split would come to, without saving it.

    Exists so the admin sees the actual paise before committing - including the
    odd one on the first row, which otherwise looks like a bug the first time
    someone divides a thousand rupees by three.
    """
    row = _load(db, request_id, actor.tenant_id)
    travellers = _travellers(row, payload.traveller_ids)
    shares = costs.split_evenly(payload.total_amount, len(travellers))

    return CostPreview(
        total_amount=costs.to_money(payload.total_amount),
        rows=[
            CostPreviewRow(
                traveller_id=t.id, traveller_name=t.user.full_name, amount=share
            )
            for t, share in zip(travellers, shares, strict=True)
        ],
        sums_to_total=sum(shares) == costs.to_money(payload.total_amount),
    )


@router.post("/requests/{request_id}/costs/split", response_model=RequestRead)
def split_cost(
    request_id: int,
    payload: CostSplit,
    actor: AdminUser,
    http_request: Request,
    background: BackgroundTasks,
    db: DbSession,
) -> RequestRead:
    """Share one total evenly across several people and save it.

    The shared cab and the shared room, which is the case C1 specifically asks
    about. The apportionment sums to exactly the total; the first traveller
    absorbs any odd paisa.
    """
    row = _load(db, request_id, actor.tenant_id)

    if len(payload.traveller_ids) != len(set(payload.traveller_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The same traveller appears twice, so the split would be wrong.",
        )
    travellers = _travellers(row, payload.traveller_ids)
    for traveller in travellers:
        cost_entry.assert_costable(traveller)

    shares = costs.split_evenly(payload.total_amount, len(travellers))
    note = payload.note or f"Shared cost, split {len(travellers)} ways"
    vendor = cost_entry.vendor_choice(
        db, actor.tenant_id, travellers,
        sent="vendor_id" in payload.model_fields_set, vendor_id=payload.vendor_id,
    )
    invoices.guard_cost_change(
        db,
        [(t, share, vendor.id_for(t)) for t, share in zip(travellers, shares, strict=True)],
    )

    changes = [
        cost_entry.apply(db, traveller=traveller, amount=share, note=note, actor=actor, vendor=vendor)
        for traveller, share in zip(travellers, shares, strict=True)
    ]

    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="travel_request",
        entity_id=row.id,
        summary=(
            f"{actor.full_name} split {costs.to_money(payload.total_amount)} "
            f"across {len(travellers)} traveller(s) on request {row.id}{cost_entry.paid_to(vendor)}"
        ),
        changes={"total": str(costs.to_money(payload.total_amount)), "costs": changes},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    queued = invoices.follow_costs(db, travellers, actor=actor, http_request=http_request)
    db.commit()
    background.add_task(notifications.deliver_queued, queued)
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=actor.tenant_id, viewer=actor)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@router.get("/analytics", response_model=AnalyticsBundle)
def bundle(actor: AdminUser, db: DbSession, filters: ReportFilters) -> AnalyticsBundle:
    """Everything the cost page shows, in one round trip, for one slice.

    One call rather than eight, and one read of the rows shared by every
    section, because the figures have to agree with each other on screen: the
    spend by state adds up to the spend at the top. Every section follows the
    same filters as the dashboard.
    """
    rows = analytics.rows_for(db, actor.tenant_id, filters)
    trend = analytics.trend(
        rows, since=filters.since, until=filters.until, today=clock.local_today()
    )
    tenant = actor.tenant_id
    return AnalyticsBundle(
        since=trend["since"],
        until=trend["until"],
        grain=trend["grain"],
        overview=Overview(**analytics.overview(db, tenant, rows=rows)),
        trend=[TrendPoint(**p) for p in trend["points"]],
        by_campaign=[CampaignSpend(**r) for r in analytics.by_campaign(db, tenant, rows=rows)],
        by_type=[TypeSpend(**r) for r in analytics.by_type(db, tenant, rows=rows)],
        by_person=[PersonSpend(**r) for r in analytics.by_person(db, tenant, rows=rows)],
        by_state=[PlaceSpend(**r) for r in analytics.by_state(db, tenant, rows=rows)],
        by_city=[PlaceSpend(**r) for r in analytics.by_city(db, tenant, rows=rows)],
        deployment=[DeploymentRow(**r) for r in analytics.deployment(db, tenant, rows=rows)],
        deployed_people=analytics.deployed_people(db, tenant, rows=rows),
        uncosted=[UncostedRow(**r) for r in analytics.uncosted_bookings(db, tenant, rows=rows)],
    )


@router.get("/analytics/campaigns", response_model=list[CampaignSpend])
def campaign_spend(actor: AdminUser, db: DbSession, filters: ReportFilters) -> list[CampaignSpend]:
    """Campaign Financials (SOW section 2) on their own. All time unless filtered."""
    return [CampaignSpend(**r) for r in analytics.by_campaign(db, actor.tenant_id, filters)]


@router.get("/analytics/uncosted", response_model=list[UncostedRow])
def uncosted(actor: AdminUser, db: DbSession, filters: ReportFilters) -> list[UncostedRow]:
    """Booked travellers with no cost recorded - the admin's worklist."""
    return [UncostedRow(**r) for r in analytics.uncosted_bookings(db, actor.tenant_id, filters)]
