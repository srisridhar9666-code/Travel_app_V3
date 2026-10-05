"""
Booking in one step: everything the traveller needs, and one email.

The booking window used to save a car, then a decision, then a cost from
another panel, and each could send its own message - so a manager copied on a
cab for three of their team could get six emails about one car. Here the whole
booking is one call and one transaction:

* the travellers named move to BOOKED, each with the same reference and
  details (a group PNR, the cab they share, rooms on one hotel booking);
* a cab's car is recorded - anyone else already riding who has not just been
  booked is told it changed, nobody else twice;
* every file attached is confirmed and sent: each traveller booked gets their
  own confirmed copy of the row, so My requests lets each of them download it;
* the cost, when given, is the total for all of them, split evenly by
  `costs.split_evenly`, through the same invoice guard as the cost panel;
* **one email**: the travellers booked on the To line, their managers on Cc,
  every file attached. Each traveller and manager gets their in-app notice.

Nothing is written unless everything checks out first, so a refusal never
leaves a half-booked group.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import clock
from app.core.enums import AuditAction, RequestType, TicketStatus, TravellerStatus
from app.models.base import naive_utcnow
from app.models.request import RequestTraveller, TravelRequest
from app.models.ticket import TicketDocument
from app.models.user import User
from app.schemas.request import BookingPayload
from app.services import audit, cabs, cost_entry, costs, decisions, invoices, notifications
from app.services import requests as svc


def _refuse(code: int, detail: str) -> None:
    raise HTTPException(status_code=code, detail=detail)


def _names(people: list[str]) -> str:
    """"Ravi", "Ravi and Sana", "Ravi, Sana and Arun"."""
    if len(people) <= 1:
        return "".join(people)
    return f"{', '.join(people[:-1])} and {people[-1]}"


def _travellers(request: TravelRequest, ids: list[int]) -> list[RequestTraveller]:
    by_id = {t.id: t for t in request.travellers}
    missing = [i for i in ids if i not in by_id]
    if missing:
        _refuse(status.HTTP_404_NOT_FOUND, "Someone named is not on this request.")
    chosen = [by_id[i] for i in ids]
    for traveller in chosen:
        # The transition map is the authority, and its refusals already say
        # what to do ("approve them first").
        decisions.assert_transition(traveller.status, TravellerStatus.BOOKED)
    return chosen


def _tickets(
    db: Session, request: TravelRequest, travellers: list[RequestTraveller], ids: list[int]
) -> list[TicketDocument]:
    """The uploaded files this booking sends: on this request, for someone
    being booked, and neither thrown away nor already used."""
    booked = {t.id for t in travellers}
    found: list[TicketDocument] = []
    for ticket_id in ids:
        ticket = db.get(TicketDocument, ticket_id)
        if (
            ticket is None
            or ticket.tenant_id != request.tenant_id
            or ticket.request_id != request.id
            or ticket.traveller_id not in booked
        ):
            _refuse(
                status.HTTP_404_NOT_FOUND,
                "A file named was not uploaded for these travellers on this request.",
            )
        if ticket.status is TicketStatus.DISCARDED or not ticket.file_path:
            _refuse(
                status.HTTP_409_CONFLICT,
                f"{ticket.file_name or 'A file'} was removed. Upload it again to send it.",
            )
        if ticket.status is TicketStatus.CONFIRMED:
            _refuse(
                status.HTTP_409_CONFLICT,
                f"{ticket.file_name or 'A file'} was already sent with an earlier booking.",
            )
        found.append(ticket)
    return found


def _share_file(
    db: Session, ticket: TicketDocument, traveller: RequestTraveller, actor: User
) -> None:
    """A confirmed copy of a booked file for someone else booked with it, so
    they can download it from My requests. Same stored file, their own row -
    the download rule (a traveller's own tickets) is unchanged."""
    db.add(
        TicketDocument(
            tenant_id=ticket.tenant_id,
            request_id=ticket.request_id,
            traveller_id=traveller.id,
            status=TicketStatus.CONFIRMED,
            file_path=ticket.file_path,
            file_name=ticket.file_name,
            file_size=ticket.file_size,
            content_type=ticket.content_type,
            uploaded_by_id=ticket.uploaded_by_id,
            booking_reference=ticket.booking_reference,
            carrier=ticket.carrier,
            service_number=ticket.service_number,
            confirmed_by_id=actor.id,
            confirmed_at=naive_utcnow(),
            confirmed_reference=traveller.booking_reference,
        )
    )


def _where(request: TravelRequest) -> str:
    if request.request_type is RequestType.HOTEL:
        return request.hotel_city or "your stay"
    return request.route_label(" to ")


def _headline(request: TravelRequest) -> str:
    where = _where(request)
    if request.request_type is RequestType.HOTEL:
        return f"Your stay in {where} is booked."
    if request.request_type is RequestType.LOCAL_CAB:
        return f"Your cab for {where} is booked."
    mode = str(request.mode or "travel").lower()
    return f"Your {mode} from {where} is booked."


def _message(
    request: TravelRequest,
    booked: list[RequestTraveller],
    managers: list[User],
    *,
    note: str,
    files: int,
) -> tuple[str, str]:
    """The in-app line and the email body. Plain text: read on a phone, and a
    PNR has to survive being forwarded as an SMS."""
    first_names = [
        t.user.full_name.split()[0] if t.user.full_name else "there" for t in booked
    ]
    short = _headline(request)
    if request.request_type is RequestType.LOCAL_CAB and request.cab_sent_label:
        short += f" Your cab: {request.cab_sent_label}."
    short += f" Reference {booked[0].booking_reference}."

    lines = [f"Hello {_names(first_names)},", "", _headline(request)]
    if len(booked) > 1:
        lines.append(f"Booked together: {_names([t.user.full_name for t in booked])}.")
    if request.extends_request_id:
        lines.append(f"This carries on your booking on request {request.extends_request_id}.")
    if request.request_type is RequestType.LOCAL_CAB:
        car = cabs.car_lines(request)
        if car:
            lines += ["", *car]
        lines += [
            f"  Pickup       {clock.time_label(request.start_at) if request.start_at else 'to be set'}",
            f"  Until        {clock.time_label(request.end_at) if request.end_at else 'no end time'}",
        ]
    elif request.request_type is RequestType.HOTEL:
        lines.append("")
        if request.check_in:
            lines.append(f"  Check in     {request.check_in:%d %b %Y}")
        if request.check_out:
            lines.append(f"  Check out    {request.check_out:%d %b %Y}")
    booking = decisions.booking_lines(booked[0])
    if booking:
        lines += ["", "Your booking:", *booking]
    lines += ["", f"Note from the travel desk: {note}"]
    if files:
        link = f"{get_settings().frontend_base_url.rstrip('/')}/requests"
        attached = "Your ticket is attached" if files == 1 else f"{files} files are attached"
        lines += [
            "",
            f"{attached} to this email. You can also download "
            f"{'it' if files == 1 else 'them'} any time from My requests: {link}",
        ]
    if request.request_type is RequestType.LONG_DISTANCE:
        lines.append("Carry photo ID that matches the name on the booking.")
    if request.project:
        lines += ["", f"Campaign: {request.project.code} - {request.project.name}"]
    if managers:
        names = _names([m.full_name for m in managers])
        lines += ["", f"{names} {'is' if len(managers) == 1 else 'are'} copied on this email."]
    return short, "\n".join(lines)


def _tell(
    db: Session,
    *,
    request: TravelRequest,
    booked: list[RequestTraveller],
    admin: User,
    note: str,
    attachments: list[notifications.AttachedFile],
    send_email: bool,
) -> list[int]:
    """One email to everyone booked, their managers copied; an in-app notice
    for each of them, and one for each manager naming their own people."""
    people = [t.user for t in booked]
    managers: list[User] = []
    for person in people:
        manager = person.active_manager
        if (
            manager is not None
            and all(manager.id != m.id for m in managers)
            and all(manager.id != p.id for p in people)
        ):
            managers.append(manager)

    short, body = _message(request, booked, managers, note=note, files=len(attachments))
    where = _where(request)
    rows = notifications.notify_group(
        db,
        tenant_id=request.tenant_id,
        people=people,
        kind="REQUEST_BOOKED",
        title="Your request was booked",
        body=short,
        request_id=request.id,
        email_subject=f"Travel request booked - {where}",
        email_body=body,
        cc_users=managers,
        attachments=attachments,
        send_email=send_email,
        deliver_now=False,
    )

    for manager in managers:
        theirs = [p.full_name for p in people if p.manager_id == manager.id]
        notifications.notify(
            db,
            tenant_id=request.tenant_id,
            user=manager,
            kind="DECISION_COPY",
            title=f"{_names(theirs)}'s booking is confirmed"[:200],
            body=(
                f"{_names(theirs)}'s {str(request.request_type).replace('_', ' ').lower()} "
                f"for {where} was booked by {admin.full_name}. "
                f"Reference {booked[0].booking_reference}."
            ),
            request_id=request.id,
            send_email=False,
        )
    return svc.queued_emails(rows)


def book(
    db: Session,
    *,
    request: TravelRequest,
    admin: User,
    payload: BookingPayload,
    http_request: Request | None = None,
) -> list[int]:
    """Book the travellers named. Returns the email ids queued for after the
    response. The caller owns the commit."""
    booked = _travellers(request, payload.traveller_ids)
    is_cab = request.request_type is RequestType.LOCAL_CAB
    if payload.cab is not None and not is_cab:
        _refuse(status.HTTP_400_BAD_REQUEST, "A car is only recorded on a cab request.")
    reference = payload.booking_reference or (
        payload.cab.vehicle_number if payload.cab is not None else None
    )
    if not reference:
        _refuse(
            status.HTTP_400_BAD_REQUEST,
            "Marking someone booked needs a ticket or booking reference.",
        )
    tickets = _tickets(db, request, booked, payload.ticket_ids)

    # Money is checked before anything moves: a cost billed on an approved
    # invoice cannot change, and finding that out half way would be too late.
    vendor_sent = "vendor_id" in payload.model_fields_set
    priced = payload.cost_amount is not None or vendor_sent
    if priced:
        vendor = cost_entry.vendor_choice(
            db, request.tenant_id, booked, sent=vendor_sent, vendor_id=payload.vendor_id
        )
        shares = (
            costs.split_evenly(payload.cost_amount, len(booked))
            if payload.cost_amount is not None
            else [t.cost_amount for t in booked]
        )
        invoices.guard_cost_change(
            db, [(t, share, vendor.id_for(t)) for t, share in zip(booked, shares, strict=True)]
        )

    # --- the car -------------------------------------------------------------
    queued: list[int] = []
    car_changed = False
    first_car = False
    if payload.cab is not None:
        car_changes, first_car = cabs.set_car(request, admin, payload.cab)
        car_changed = bool(car_changes)
        if car_changed:
            audit.record(
                db,
                action=AuditAction.UPDATE,
                entity_type="travel_request",
                entity_id=request.id,
                summary=cabs.car_summary(request, admin, first=first_car),
                changes=car_changes,
                tenant_id=request.tenant_id,
                actor=admin,
                request=http_request,
            )

    # --- the decisions -------------------------------------------------------
    details = payload.booking_details.stored() if payload.booking_details else None
    for traveller in booked:
        decisions.apply(
            db,
            tenant_id=request.tenant_id,
            actor=admin,
            request=request,
            traveller=traveller,
            decision=decisions.Decision(
                traveller_id=traveller.id,
                to_status=TravellerStatus.BOOKED,
                reason=payload.note,
                booking_reference=reference,
                booking_details=details,
            ),
            http_request=http_request,
            notify=False,   # one message for the whole booking, below
        )

    # --- the files -----------------------------------------------------------
    attachments: list[notifications.AttachedFile] = []
    owners = {t.id: t for t in booked}
    for ticket in tickets:
        sent = decisions.confirm_ticket_for_booking(
            db, ticket, traveller=owners[ticket.traveller_id], actor=admin,
            tenant_id=request.tenant_id, http_request=http_request,
        )
        if sent is not None:
            attachments.append(sent)
        for traveller in booked:
            if traveller.id != ticket.traveller_id:
                _share_file(db, ticket, traveller, admin)

    # --- the cost ------------------------------------------------------------
    if priced:
        note = (
            "Booked" if len(booked) == 1 else f"Booked together, split {len(booked)} ways"
        ) if payload.cost_amount is not None else None
        changes = [
            cost_entry.apply(
                db, traveller=traveller, amount=share,
                note=note if payload.cost_amount is not None else traveller.cost_note,
                actor=admin, vendor=vendor,
            )
            for traveller, share in zip(booked, shares, strict=True)
        ]
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="travel_request",
            entity_id=request.id,
            summary=(
                f"{admin.full_name} recorded the cost of booking request {request.id}"
                + (
                    f": {costs.to_money(payload.cost_amount)} for {len(booked)} traveller(s)"
                    if payload.cost_amount is not None
                    else ""
                )
                + cost_entry.paid_to(vendor)
            ),
            changes={"costs": changes},
            tenant_id=request.tenant_id,
            actor=admin,
            request=http_request,
        )
        queued += invoices.follow_costs(db, booked, actor=admin, http_request=http_request)

    db.flush()

    # --- telling them --------------------------------------------------------
    queued += _tell(
        db,
        request=request,
        booked=booked,
        admin=admin,
        note=payload.note,
        attachments=attachments,
        send_email=payload.notify,
    )
    if car_changed and payload.notify:
        # Riding already, and not in this booking: they need the new plate.
        others = [
            t.user for t in cabs.riders(request)
            if t.id not in owners and t.status is TravellerStatus.BOOKED
        ]
        if others:
            queued += cabs.tell_riders(db, request=request, people=others, first=first_car)
    return queued
