"""
A manager's recommendation on their team's trips: the first of two levels.

The product owner's rule is that a traveller's manager gives a view, with a
comment, and an admin then makes the final decision, with a comment of their
own, seeing what the manager said. Three things follow from that and are held
here rather than in the router:

* **Advice, never a gate.** Nothing in `decisions.apply` waits for a
  recommendation. An admin may decide before the manager answers, or against
  them; the queue shows which, so it is done knowingly. A manager on leave must
  not be able to stall someone's travel.
* **One manager, one level, looked up live.** The traveller's manager is
  whoever they report to now (`User.active_manager`), not whoever they reported
  to when the request was raised - the person answerable today is the one asked.
* **Changeable until decided.** While the traveller is still pending the
  manager may change their mind; each version is a RECOMMEND row in the
  activity log, so the ledger shows the history the traveller row does not.
"""
from __future__ import annotations

from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.enums import (
    ADMIN_ROLES,
    OPS_ADMIN_ROLES,
    AuditAction,
    CabType,
    HotelOccupancy,
    ManagerRecommendation,
    RequestType,
    Role,
    TravellerStatus,
)
from app.models.base import naive_utcnow
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.services import audit, notifications
from app.services.requests import queued_emails, trip_summary

LABELS = {
    ManagerRecommendation.RECOMMENDED: "Recommended",
    ManagerRecommendation.NOT_RECOMMENDED: "Not recommended",
}


def describe(traveller: RequestTraveller) -> str | None:
    """"Recommended - needed for the Pune audit", or None if nothing was said."""
    if traveller.manager_recommendation is None:
        return None
    label = LABELS[traveller.manager_recommendation]
    return f"{label} - {traveller.manager_comment}" if traveller.manager_comment else label


def _reports_to(traveller: RequestTraveller, manager: User) -> bool:
    return traveller.user is not None and traveller.user.manager_id == manager.id


def _answers_for(traveller: RequestTraveller, reader: User) -> bool:
    """Whose recommendations this reader works with: every traveller's, for an
    admin; their own team's, for a manager."""
    return traveller.user is not None and (reader.is_admin or _reports_to(traveller, reader))


def waits_on(request: TravelRequest, reader: User) -> bool:
    """Someone on this request is still waiting for their manager's view - for
    a manager, someone on their own team."""
    return any(t.awaits_manager and _answers_for(t, reader) for t in request.travellers)


def reviewed_by(request: TravelRequest, reader: User) -> bool:
    """A manager has already given their view on someone here - for a
    manager, given it themself."""
    return any(
        t.manager_recommendation is not None
        and (reader.is_admin or t.manager_reviewed_by_id == reader.id)
        for t in request.travellers
    )


def _choose(
    request: TravelRequest, manager: User, traveller_ids: list[int] | None
) -> list[RequestTraveller]:
    """The travellers this recommendation covers, or a refusal that says why not."""
    if request.is_draft:
        # Unreachable through _load, which hides other people's drafts, but a
        # manager's own draft is not in front of an admin yet either.
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="This request has not been submitted yet.",
        )
    if request.is_cancelled:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="This request has been cancelled, so there is nothing to recommend.",
        )

    if traveller_ids is None:
        chosen = [
            t for t in request.travellers
            if _reports_to(t, manager) and t.status is TravellerStatus.PENDING
        ]
        if not chosen:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=(
                    "Nobody from your team on this request is still waiting for a "
                    "decision. An admin has already decided."
                ),
            )
        return chosen

    if not traveller_ids:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Choose at least one of your team members on this request.",
        )
    by_id = {t.id: t for t in request.travellers}
    wanted = set(traveller_ids)
    for traveller_id in wanted:
        traveller = by_id.get(traveller_id)
        if traveller is None or not _reports_to(traveller, manager):
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="You can only recommend your own team members on this request.",
            )
        if traveller.status is not TravellerStatus.PENDING:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=(
                    f"An admin has already decided {traveller.user.full_name}, so a "
                    "recommendation would change nothing."
                ),
            )
    # In the request's own order, so notices and the log list people the way
    # every screen does.
    return [t for t in request.travellers if t.id in wanted]


def record(
    db: Session,
    *,
    request: TravelRequest,
    manager: User,
    recommendation: ManagerRecommendation,
    comment: str,
    traveller_ids: list[int] | None = None,
    hotel_occupancy: HotelOccupancy | None = None,
    cab_type: CabType | None = None,
    http_request=None,
) -> list[int]:
    """Save a manager's view on their team members' part of a request, log it,
    and tell the admins.

    The caller owns the commit. Returns the ids of the admin emails queued, for
    the caller to send once the response is on its way.
    """
    chosen = _choose(request, manager, traveller_ids)
    verb = "recommended" if recommendation is ManagerRecommendation.RECOMMENDED else "did not recommend"
    request.updated_at = naive_utcnow()

    if cab_type is not None and request.request_type == RequestType.LOCAL_CAB:
        before_cab = request.cab_type
        if before_cab != cab_type:
            request.cab_type = cab_type
            audit.record(
                db,
                action=AuditAction.UPDATE,
                entity_type="travel_request",
                entity_id=request.id,
                summary=(
                    f"{manager.full_name} evaluated and set car category to {cab_type.value} "
                    f"on request {request.id}"
                ),
                changes={"cab_type": {"from": str(before_cab), "to": str(cab_type)}},
                reason=comment,
                tenant_id=request.tenant_id,
                actor=manager,
                request=http_request,
            )

    if hotel_occupancy is not None and request.request_type == RequestType.HOTEL:
        before_occ = request.hotel_occupancy
        if before_occ != hotel_occupancy:
            request.hotel_occupancy = hotel_occupancy
            audit.record(
                db,
                action=AuditAction.UPDATE,
                entity_type="travel_request",
                entity_id=request.id,
                summary=(
                    f"{manager.full_name} set hotel occupancy to {hotel_occupancy.value} "
                    f"during L1 evaluation on request {request.id}"
                ),
                changes={"hotel_occupancy": {"from": str(before_occ), "to": str(hotel_occupancy)}},
                reason=comment,
                tenant_id=request.tenant_id,
                actor=manager,
                request=http_request,
            )

    for traveller in chosen:
        before = traveller.manager_recommendation
        changes = {
            "recommendation": {
                "from": str(before) if before else None,
                "to": str(recommendation),
            },
        }
        if before is not None and traveller.manager_comment != comment:
            changes["comment"] = {"from": traveller.manager_comment, "to": comment}

        traveller.manager_recommendation = recommendation
        traveller.manager_comment = comment
        traveller.manager_reviewed_by_id = manager.id
        traveller.manager_reviewed_at = naive_utcnow()

        audit.record(
            db,
            action=AuditAction.RECOMMEND,
            entity_type="request_traveller",
            entity_id=traveller.id,
            summary=(
                f"{manager.full_name} {verb} {traveller.user.full_name} on request "
                f"{request.id}" + (" (changed their recommendation)" if before else "")
            ),
            changes=changes,
            reason=comment,
            tenant_id=request.tenant_id,
            actor=manager,
            request=http_request,
        )

    db.flush()
    return _tell_admins(
        db, request=request, manager=manager, travellers=chosen,
        recommendation=recommendation, comment=comment,
    )


def _tell_admins(
    db: Session,
    *,
    request: TravelRequest,
    manager: User,
    travellers: list[RequestTraveller],
    recommendation: ManagerRecommendation,
    comment: str,
) -> list[int]:
    """Operational admins hear the manager's view in one group email, comment included.

    Super Admin never receives emails for manager recommendations (in-app only).
    """
    ops_admins = (
        db.execute(
            select(User).where(
                User.tenant_id == request.tenant_id,
                User.role.in_(OPS_ADMIN_ROLES),
                User.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )
    super_admins = (
        db.execute(
            select(User).where(
                User.tenant_id == request.tenant_id,
                User.role == Role.SUPER_ADMIN,
                User.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )
    if not ops_admins and not super_admins:
        return []

    label = LABELS[recommendation]
    names = ", ".join(t.user.full_name for t in travellers)
    summary = trip_summary(request)
    link = f"{get_settings().frontend_base_url.rstrip('/')}/approvals"
    title = f"{manager.full_name}: {label.lower()} for {names}"
    body = f"{summary}. {label} - {comment}"

    lines = [
        "Hello Travel Desk,",
        "",
        f"{manager.full_name} gave their recommendation on a request for {names}.",
        "",
        summary,
        f"Manager's recommendation: {label}",
        f"Their comment: {comment}",
        "",
        "The final decision is yours.",
        f"Decide it on Approvals: {link}",
    ]

    queued: list[int] = []
    if ops_admins:
        rows = notifications.notify_group(
            db,
            tenant_id=request.tenant_id,
            people=list(ops_admins),
            kind="MANAGER_RECOMMENDED",
            title=title[:200],
            body=body,
            request_id=request.id,
            email_subject=f"{label} by {manager.full_name}: {summary}"[:255],
            email_body="\n".join(lines),
            cc_users=[manager] if manager.is_active else None,
            deliver_now=False,
        )
        queued += queued_emails(rows)

    for sa_user in super_admins:
        notifications.notify(
            db,
            tenant_id=request.tenant_id,
            user=sa_user,
            kind="MANAGER_RECOMMENDED",
            title=title[:200],
            body=body,
            request_id=request.id,
            send_email=False,
        )
    return queued
