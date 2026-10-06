"""
Ticket upload, extraction and the human step before booking (addendum B3).

The flow is deliberately four moves, not one:

    upload -> extract -> admin review & confirm -> Booked -> notify

The SOW compresses that to "the system populates the fields instantly and sets
status to Booked", then emails the traveller. One model misparse under that
design silently books a wrong PNR and mails it out, and nobody finds out until
someone is at an airport. So extraction writes a *proposal* onto a ticket row and
can do nothing else; only `confirm` moves a traveller to BOOKED, and only a
person can call it.

Ticket files carry a PNR and a full name, so they are stored and served exactly
like an ID proof scan - outside any static mount, admin-only, never a public URL.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import clock
from app.core.deps import AdminUser, CurrentUser, DbSession
from app.core.enums import (
    AuditAction,
    NotificationChannel,
    RequestType,
    TicketStatus,
    TravellerStatus,
)
from app.models.base import naive_utcnow
from app.models.request import RequestTraveller, TravelRequest
from app.models.ticket import TicketDocument
from app.models.user import User
from app.schemas.ticket import CombinedRead, ConfirmPayload, TicketRead
from app.services import audit, costs, extraction, notifications, storage
from app.services import decisions

router = APIRouter(tags=["tickets"])


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------


def _mismatches(ticket: TicketDocument) -> list[str]:
    """Where the ticket disagrees with what was asked for.

    Advisory only. A ticket that does not match the request is usually a real
    problem - the wrong leg, the wrong day - but occasionally it is a deliberate
    change the admin already knows about, so this informs the reviewer rather
    than blocking them.
    """
    request = ticket.request
    if request is None:
        return []

    found: list[str] = []

    def differs(a: str | None, b: str | None) -> bool:
        return bool(a and b and a.strip().casefold() not in b.strip().casefold()
                    and b.strip().casefold() not in a.strip().casefold())

    if request.request_type is RequestType.HOTEL:
        if ticket.check_in and request.check_in and ticket.check_in != request.check_in:
            found.append(
                f"check-in on the ticket is {ticket.check_in}, the request asked for "
                f"{request.check_in}"
            )
        if ticket.check_out and request.check_out and ticket.check_out != request.check_out:
            found.append(
                f"check-out on the ticket is {ticket.check_out}, the request asked for "
                f"{request.check_out}"
            )
    else:
        if differs(ticket.origin, request.origin):
            found.append(f"ticket departs {ticket.origin}, the request asked for {request.origin}")
        if differs(ticket.destination, request.destination):
            found.append(
                f"ticket arrives {ticket.destination}, the request asked for "
                f"{request.destination}"
            )
        if ticket.depart_at and request.start_at and ticket.depart_at.date() != request.start_at.date():
            found.append(
                f"ticket departs on {ticket.depart_at.date()}, the request asked for "
                f"{request.start_at.date()}"
            )

    # The name on the ticket is the one that has to match at the gate.
    traveller = ticket.traveller
    if ticket.passenger_name and traveller is not None and traveller.user is not None:
        ticket_name = ticket.passenger_name.strip().casefold()
        real_name = traveller.user.full_name.strip().casefold()
        surname = real_name.split()[-1] if real_name else ""
        if surname and surname not in ticket_name:
            found.append(
                f"the ticket is in the name of {ticket.passenger_name}, "
                f"but this is {traveller.user.full_name}"
            )

    return found


def _to_read(ticket: TicketDocument) -> TicketRead:
    confidence = ticket.confidence or {}
    needs_review = sorted(
        name
        for name, score in confidence.items()
        if getattr(ticket, name, None) is not None
        and isinstance(score, (int, float))
        and score < extraction.REVIEW_THRESHOLD
    )

    return TicketRead(
        id=ticket.id,
        request_id=ticket.request_id,
        traveller_id=ticket.traveller_id,
        traveller_name=(
            ticket.traveller.user.full_name
            if ticket.traveller and ticket.traveller.user
            else ""
        ),
        status=ticket.status,
        file_name=ticket.file_name,
        file_size=ticket.file_size,
        content_type=ticket.content_type,
        uploaded_by_name=ticket.uploaded_by.full_name if ticket.uploaded_by else None,
        created_at=ticket.created_at,
        booking_reference=ticket.booking_reference,
        carrier=ticket.carrier,
        service_number=ticket.service_number,
        passenger_name=ticket.passenger_name,
        origin=ticket.origin,
        destination=ticket.destination,
        depart_at=ticket.depart_at,
        arrive_at=ticket.arrive_at,
        hotel_name=ticket.hotel_name,
        check_in=ticket.check_in,
        check_out=ticket.check_out,
        fare_amount=ticket.fare_amount,
        fare_currency=ticket.fare_currency,
        confidence=confidence or None,
        needs_review=needs_review,
        model_id=ticket.model_id,
        extraction_error=ticket.extraction_error,
        extracted_at=ticket.extracted_at,
        confirmed_by_name=ticket.confirmed_by.full_name if ticket.confirmed_by else None,
        confirmed_at=ticket.confirmed_at,
        confirmed_reference=ticket.confirmed_reference,
        mismatches=_mismatches(ticket),
    )


def _load_request(db: Session, request_id: int, user: User) -> TravelRequest:
    row = db.get(TravelRequest, request_id)
    if row is None or row.tenant_id != user.tenant_id or row.is_draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    return row


def _load_ticket(db: Session, ticket_id: int, user: User) -> TicketDocument:
    ticket = db.get(TicketDocument, ticket_id)
    if ticket is None or ticket.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found.")
    return ticket


# ---------------------------------------------------------------------------
# Upload and extract
# ---------------------------------------------------------------------------


@router.post(
    "/requests/{request_id}/tickets",
    response_model=TicketRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_ticket(
    request_id: int,
    traveller_id: Annotated[int, Query(description="Whose ticket this is")],
    file: UploadFile,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
) -> TicketRead:
    """Attach a ticket to one traveller and read it.

    Extraction runs inline: a ticket takes a few seconds and the admin who
    uploaded it is sitting there waiting to review. It cannot fail the upload -
    a model that is unreachable leaves a FAILED row the admin can retry or fill
    in by hand.
    """
    row = _load_request(db, request_id, actor)

    traveller = next((t for t in row.travellers if t.id == traveller_id), None)
    if traveller is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That traveller is not on this request."
        )
    if traveller.status is TravellerStatus.REJECTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This traveller was rejected, so there is nothing to book.",
        )
    if traveller.status is TravellerStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This traveller has been cancelled off the request.",
        )

    data = await file.read()
    extension = storage.validate(file, data)
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    path = storage.save_in("tickets", row.id, data, extension)

    ticket = TicketDocument(
        tenant_id=actor.tenant_id,
        request_id=row.id,
        traveller_id=traveller.id,
        uploaded_by_id=actor.id,
        file_path=path,
        file_name=(file.filename or "ticket")[:255],
        file_size=len(data),
        content_type=content_type,
        status=TicketStatus.UPLOADED,
    )
    db.add(ticket)
    db.flush()

    audit.record(
        db,
        action=AuditAction.UPLOAD,
        entity_type="ticket_document",
        entity_id=ticket.id,
        summary=(
            f"{actor.full_name} uploaded a ticket for "
            f"{traveller.user.full_name} on request {row.id}"
        ),
        changes={"file_name": ticket.file_name, "size": ticket.file_size},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )

    # The model takes seconds. Off the event loop, so several files uploaded
    # at once are read side by side and nobody else's request waits on them.
    result = await run_in_threadpool(extraction.extract, data, content_type)
    _apply_extraction(db, ticket, result, actor, http_request)
    db.commit()
    db.refresh(ticket)
    return _to_read(ticket)


def _run_extraction(
    db: Session,
    ticket: TicketDocument,
    data: bytes,
    content_type: str,
    actor: User,
    http_request: Request | None,
) -> None:
    """Ask the model, then record what it said."""
    _apply_extraction(db, ticket, extraction.extract(data, content_type), actor, http_request)


def _apply_extraction(
    db: Session,
    ticket: TicketDocument,
    result: extraction.Extraction,
    actor: User,
    http_request: Request | None,
) -> None:
    """Write the model's proposal onto the row and audit what it said.

    This function is the only thing that writes extracted values, and it never
    touches a traveller's status - that separation is the whole of B3.
    """
    ticket.model_id = result.model_id
    ticket.extracted_at = naive_utcnow()

    if not result.ok:
        ticket.status = TicketStatus.FAILED
        ticket.extraction_error = (result.error or "extraction failed")[:500]
        ticket.raw_response = result.raw
        audit.record(
            db,
            action=AuditAction.EXTRACT,
            entity_type="ticket_document",
            entity_id=ticket.id,
            summary=f"Extraction failed for ticket {ticket.id}: {ticket.extraction_error}",
            tenant_id=ticket.tenant_id,
            actor=actor,
            request=http_request,
        )
        return

    for name, value in result.fields.items():
        setattr(ticket, name, value)
    ticket.confidence = result.confidence or None
    ticket.raw_response = result.raw
    ticket.extraction_error = None
    ticket.status = TicketStatus.EXTRACTED

    audit.record(
        db,
        action=AuditAction.EXTRACT,
        entity_type="ticket_document",
        entity_id=ticket.id,
        summary=(
            f"{result.model_id} proposed {result.fields.get('booking_reference') or 'no reference'} "
            f"for ticket {ticket.id} - awaiting review"
        ),
        # The proposal is audited in full: a booking that turns out wrong has to
        # be traceable to what was actually suggested.
        changes={
            "proposed": result.fields,
            "confidence": result.confidence,
            "low_confidence": result.low_confidence_fields,
        },
        tenant_id=ticket.tenant_id,
        actor=actor,
        request=http_request,
    )


@router.post("/tickets/{ticket_id}/extract", response_model=TicketRead)
def reextract(
    ticket_id: int, actor: AdminUser, http_request: Request, db: DbSession
) -> TicketRead:
    """Read the document again. For a failed extraction, or a retry after an outage."""
    ticket = _load_ticket(db, ticket_id, actor)
    if ticket.status is TicketStatus.CONFIRMED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This ticket has already been confirmed; re-reading it would change nothing.",
        )
    if not ticket.file_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This ticket has no file to read."
        )

    data = storage.read(ticket.file_path)
    _run_extraction(db, ticket, data, ticket.content_type or "application/pdf", actor, http_request)
    db.commit()
    db.refresh(ticket)
    return _to_read(ticket)


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


@router.get("/requests/{request_id}/tickets", response_model=list[TicketRead])
def list_tickets(request_id: int, actor: AdminUser, db: DbSession) -> list[TicketRead]:
    _load_request(db, request_id, actor)
    rows = (
        db.execute(
            select(TicketDocument)
            .where(TicketDocument.request_id == request_id)
            .order_by(TicketDocument.id.desc())
        )
        .scalars()
        .unique()
        .all()
    )
    return [_to_read(t) for t in rows]


@router.get("/requests/{request_id}/tickets/combined", response_model=CombinedRead)
def read_together(
    request_id: int,
    ids: Annotated[list[int], Query(min_length=1, max_length=20)],
    actor: AdminUser,
    db: DbSession,
) -> CombinedRead:
    """What several uploaded files say together - the booking window fills
    from all of them, not just the first. Files on this request only, and
    none thrown away."""
    _load_request(db, request_id, actor)
    rows = db.execute(
        select(TicketDocument)
        .where(
            TicketDocument.request_id == request_id,
            TicketDocument.id.in_(ids),
            TicketDocument.status != TicketStatus.DISCARDED,
        )
        .order_by(TicketDocument.id)
    ).scalars().unique().all()
    if len(rows) != len(set(ids)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="A file named is not on this request, or was removed.",
        )
    return CombinedRead(**asdict(extraction.combine(list(rows))))


@router.get("/tickets/pending", response_model=list[TicketRead])
def pending_review(actor: AdminUser, db: DbSession) -> list[TicketRead]:
    """Everything the model has read and nobody has looked at yet."""
    rows = (
        db.execute(
            select(TicketDocument)
            .where(
                TicketDocument.tenant_id == actor.tenant_id,
                TicketDocument.status.in_([TicketStatus.EXTRACTED, TicketStatus.FAILED]),
            )
            .order_by(TicketDocument.id.desc())
            .limit(100)
        )
        .scalars()
        .unique()
        .all()
    )
    return [_to_read(t) for t in rows]


@router.get("/tickets/{ticket_id}", response_model=TicketRead)
def get_ticket(ticket_id: int, actor: AdminUser, db: DbSession) -> TicketRead:
    return _to_read(_load_ticket(db, ticket_id, actor))


@router.get("/tickets/{ticket_id}/file")
def download_ticket(ticket_id: int, actor: AdminUser, db: DbSession) -> Response:
    """The document itself, so the reviewer can read it beside the fields.

    Fetched through the API rather than linked: the file carries a PNR and a
    passenger name, and it is never reachable without a token.
    """
    ticket = _load_ticket(db, ticket_id, actor)
    if not ticket.file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No file on this ticket.")

    data = storage.read(ticket.file_path)
    return Response(
        content=data,
        media_type=ticket.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{ticket.file_name or "ticket"}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _may_download(db: Session, request_id: int, traveller_id: int, user: User) -> bool:
    """The traveller themself, whoever raised the request (they often book for
    a group), and admins."""
    row = db.get(TravelRequest, request_id)
    traveller = (
        next((t for t in row.travellers if t.id == traveller_id), None)
        if row is not None and row.tenant_id == user.tenant_id
        else None
    )
    return traveller is not None and (
        user.is_admin or user.id in (traveller.user_id, row.requester_id)
    )


def _confirmed_file(ticket: TicketDocument) -> Response:
    return Response(
        content=storage.read(ticket.file_path),
        media_type=ticket.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{ticket.file_name or "ticket"}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/requests/{request_id}/travellers/{traveller_id}/tickets/{ticket_id}")
def my_ticket_file(
    request_id: int, traveller_id: int, ticket_id: int, user: CurrentUser, db: DbSession
) -> Response:
    """One of the files sent with a traveller's booking - a booking can carry
    several. Same people as below, and only a confirmed file."""
    ticket = db.get(TicketDocument, ticket_id) if _may_download(
        db, request_id, traveller_id, user
    ) else None
    if (
        ticket is None
        or ticket.request_id != request_id
        or ticket.traveller_id != traveller_id
        or ticket.status is not TicketStatus.CONFIRMED
        or not ticket.file_path
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such file to download.")
    return _confirmed_file(ticket)


def _unique_name(name: str, taken: set[str]) -> str:
    """"ticket.pdf", then "ticket (2).pdf" - a zip cannot hold two of a name."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    candidate, n = name, 2
    while candidate.casefold() in taken:
        candidate = f"{stem} ({n}){dot}{ext}"
        n += 1
    taken.add(candidate.casefold())
    return candidate


@router.get("/requests/{request_id}/travellers/{traveller_id}/tickets.zip")
def my_tickets_zip(
    request_id: int, traveller_id: int, user: CurrentUser, db: DbSession
) -> Response:
    """Every file on a traveller's booking in one download - for a booking
    with several, so nobody has to fetch them one by one. Same people as the
    single download, confirmed files only."""
    rows = (
        db.execute(
            select(TicketDocument)
            .where(
                TicketDocument.request_id == request_id,
                TicketDocument.traveller_id == traveller_id,
                TicketDocument.status == TicketStatus.CONFIRMED,
                TicketDocument.file_path.is_not(None),
            )
            .order_by(TicketDocument.id)
        ).scalars().unique().all()
        if _may_download(db, request_id, traveller_id, user)
        else []
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No tickets to download yet.")
    buffer = io.BytesIO()
    taken: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for ticket in rows:
            name = _unique_name((ticket.file_name or "ticket").replace("/", "_"), taken)
            bundle.writestr(name, storage.read(ticket.file_path))
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="request-{request_id}-tickets.zip"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/requests/{request_id}/travellers/{traveller_id}/ticket")
def my_ticket(request_id: int, traveller_id: int, user: CurrentUser, db: DbSession) -> Response:
    """A traveller's confirmed ticket, for the traveller themself.

    Also for whoever raised the request (they often book for a group) and for
    admins. Only a confirmed ticket - one an admin has checked and booked
    against - is ever handed out; one still under review is not theirs yet.
    """
    ticket = (
        db.execute(
            select(TicketDocument)
            .where(
                TicketDocument.request_id == request_id,
                TicketDocument.traveller_id == traveller_id,
                TicketDocument.status == TicketStatus.CONFIRMED,
                TicketDocument.file_path.is_not(None),
            )
            .order_by(TicketDocument.id.desc())
        ).scalars().first()
        if _may_download(db, request_id, traveller_id, user)
        else None
    )
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No ticket to download yet.")
    return _confirmed_file(ticket)


# ---------------------------------------------------------------------------
# Confirm - the only path to BOOKED
# ---------------------------------------------------------------------------


def details_from_ticket(ticket: TicketDocument) -> dict | None:
    """The booking details a confirmed ticket gives, in the shape
    RequestTraveller.booking_details keeps (see schemas.request.BookingDetails)."""
    details = {
        "carrier": ticket.carrier,
        "service_number": ticket.service_number,
        "depart_at": ticket.depart_at.isoformat() if ticket.depart_at else None,
        "arrive_at": ticket.arrive_at.isoformat() if ticket.arrive_at else None,
        "hotel_name": ticket.hotel_name,
    }
    if ticket.check_in or ticket.check_out:
        stay = " to ".join(d.strftime("%d %b %Y") for d in (ticket.check_in, ticket.check_out) if d)
        details["notes"] = f"Stay {stay}"
    details = {k: v for k, v in details.items() if v}
    return details or None


@router.post("/tickets/{ticket_id}/confirm", response_model=TicketRead)
def confirm_ticket(
    ticket_id: int,
    payload: ConfirmPayload,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
) -> TicketRead:
    """A human accepts the ticket; only now does anyone get booked or emailed.

    What the admin saves is stored beside what the model proposed rather than
    over it, so a later question about a wrong booking can separate "the model
    misread it" from "the admin changed it".
    """
    ticket = _load_ticket(db, ticket_id, actor)

    if ticket.status is TicketStatus.CONFIRMED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This ticket is already confirmed."
        )
    if ticket.status is TicketStatus.DISCARDED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This ticket was discarded."
        )

    row = ticket.request
    traveller = ticket.traveller
    if row is None or traveller is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="This ticket has lost its request."
        )
    if row.is_cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This request has been cancelled."
        )

    reference = payload.booking_reference.strip()

    # The traveller has to be approved first. Booking is the second step, and
    # confirming a ticket does not get to skip the approval - the transition map
    # is the authority and it refuses.
    decisions.apply(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        request=row,
        traveller=traveller,
        decision=decisions.Decision(
            traveller_id=traveller.id,
            to_status=TravellerStatus.BOOKED,
            booking_reference=reference,
            # Every decision carries a reason now; here the ticket is the reason.
            # Without one this endpoint refused every confirmation.
            reason="Booked from the uploaded ticket",
        ),
        http_request=http_request,
        notify=False,   # the richer confirmation below replaces the generic notice
    )

    if payload.cost_amount is not None:
        traveller.cost_amount = costs.to_money(payload.cost_amount)
        traveller.cost_currency = costs.DEFAULT_CURRENCY
        traveller.cost_note = "Confirmed from the ticket"
        traveller.cost_entered_by_id = actor.id
        traveller.cost_entered_at = naive_utcnow()

    ticket.status = TicketStatus.CONFIRMED
    ticket.confirmed_by_id = actor.id
    ticket.confirmed_at = naive_utcnow()
    ticket.confirmed_reference = reference
    if payload.carrier:
        ticket.carrier = payload.carrier.strip()
    if payload.service_number:
        ticket.service_number = payload.service_number.strip()
    # The traveller's own record of the booking, so My requests can show it
    # without anyone opening the ticket file.
    traveller.booking_details = details_from_ticket(ticket) or traveller.booking_details

    corrected = (ticket.booking_reference or "") != reference
    audit.record(
        db,
        action=AuditAction.BOOK,
        entity_type="ticket_document",
        entity_id=ticket.id,
        summary=(
            f"{actor.full_name} confirmed ticket {ticket.id} for "
            f"{traveller.user.full_name} as {reference}"
            + (" (corrected from the extraction)" if corrected else "")
        ),
        changes={
            "proposed_reference": ticket.booking_reference,
            "confirmed_reference": reference,
            "corrected_by_hand": corrected,
            "proposed_fare": str(ticket.fare_amount) if ticket.fare_amount else None,
            "confirmed_cost": (
                str(traveller.cost_amount) if traveller.cost_amount is not None else None
            ),
        },
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )

    if payload.notify:
        _send_confirmation(db, ticket=ticket, request=row, traveller=traveller, actor=actor,
                           http_request=http_request)

    db.commit()
    db.refresh(ticket)
    return _to_read(ticket)


def _confirmation_body(ticket: TicketDocument, request: TravelRequest, name: str) -> str:
    """The email the traveller actually receives.

    Plain text on purpose: field staff read this on a phone, often on a bad
    connection, and a PNR has to survive being forwarded as a text message.
    """
    lines = [f"Hello {name.split()[0] if name else 'there'},", ""]

    if request.request_type is RequestType.HOTEL:
        lines.append(f"Your accommodation in {request.hotel_city} is booked.")
        lines.append("")
        if ticket.hotel_name:
            lines.append(f"  Hotel        {ticket.hotel_name}")
        lines.append(f"  Confirmation {ticket.confirmed_reference}")
        if request.check_in:
            lines.append(f"  Check in     {request.check_in}")
        if request.check_out:
            lines.append(f"  Check out    {request.check_out}")
    else:
        lines.append(f"Your travel from {request.route_label(' to ')} is booked.")
        lines.append("")
        if ticket.carrier:
            lines.append(f"  Operator     {ticket.carrier}")
        if ticket.service_number:
            lines.append(f"  Service      {ticket.service_number}")
        lines.append(f"  Reference    {ticket.confirmed_reference}")
        if request.start_at:
            lines.append(f"  Departs      {clock.time_label(request.start_at)}")
        if request.end_at:
            lines.append(f"  Arrives      {clock.time_label(request.end_at)}")

    if request.project:
        lines += ["", f"Campaign: {request.project.code} - {request.project.name}"]
    link = f"{get_settings().frontend_base_url.rstrip('/')}/requests"
    lines += [
        "",
        "Your ticket is attached to this email. You can also download it any time "
        f"from My requests: {link}",
        "Carry photo ID that matches the name on the booking.",
    ]
    return "\n".join(lines)


def _send_confirmation(
    db: Session,
    *,
    ticket: TicketDocument,
    request: TravelRequest,
    traveller: RequestTraveller,
    actor: User,
    http_request: Request | None,
) -> None:
    """Tell the traveller, in app and by email, and record both attempts."""
    person = traveller.user
    if person is None:
        return

    where = (
        request.hotel_city
        if request.request_type is RequestType.HOTEL
        else request.route_label(" to ")
    )
    rows = notifications.notify(
        db,
        tenant_id=ticket.tenant_id,
        user=person,
        kind="BOOKING_CONFIRMED",
        title=f"Booked: {where}",
        body=(
            f"Your booking for {where} is confirmed. "
            f"Reference {ticket.confirmed_reference}."
        ),
        request_id=request.id,
        email_subject=f"Booking confirmed - {where}",
        email_body=_confirmation_body(ticket, request, person.full_name),
        # The same Cc as every other decision on this traveller: their manager
        # sees the booking land.
        cc_users=[person.active_manager] if person.active_manager is not None else None,
        attachment=(
            notifications.AttachedFile(
                path=ticket.file_path,
                name=ticket.file_name or "ticket",
                content_type=ticket.content_type,
            )
            if ticket.file_path
            else None
        ),
    )

    decisions.copy_manager(
        db,
        tenant_id=ticket.tenant_id,
        person=person,
        request=request,
        title=f"{person.full_name}'s booking is confirmed",
        body=(
            f"{person.full_name}'s booking for {where} is confirmed. "
            f"Reference {ticket.confirmed_reference}."
        ),
    )

    mail = next((r for r in rows if r.channel is NotificationChannel.EMAIL), None)
    audit.record(
        db,
        action=AuditAction.NOTIFY,
        entity_type="ticket_document",
        entity_id=ticket.id,
        summary=(
            f"{person.full_name} notified of booking {ticket.confirmed_reference}"
            + (f" (email {mail.status})" if mail else " (in app only)")
        ),
        changes={
            "channel": "EMAIL" if mail else "IN_APP",
            "status": str(mail.status) if mail else "SENT",
            "error": mail.last_error if mail else None,
        },
        tenant_id=ticket.tenant_id,
        actor=actor,
        request=http_request,
    )


@router.post("/tickets/{ticket_id}/discard", response_model=TicketRead)
def discard_ticket(
    ticket_id: int, actor: AdminUser, http_request: Request, db: DbSession
) -> TicketRead:
    """Set aside the wrong file. Keeps the row and the audit trail; drops the document."""
    ticket = _load_ticket(db, ticket_id, actor)
    if ticket.status is TicketStatus.CONFIRMED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A confirmed ticket cannot be discarded - cancel the traveller instead.",
        )

    storage.delete(ticket.file_path)
    ticket.file_path = None
    ticket.status = TicketStatus.DISCARDED

    audit.record(
        db,
        action=AuditAction.DELETE,
        entity_type="ticket_document",
        entity_id=ticket.id,
        summary=f"{actor.full_name} discarded ticket {ticket.id}",
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
    db.refresh(ticket)
    return _to_read(ticket)
