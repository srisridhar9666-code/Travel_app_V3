"""
Reading a ticket with Gemini (SOW section 4, addendum B3).

The SOW wants the upload to populate the booking "instantly" and set the request
to Booked. This module does the reading and nothing else: it proposes fields and
a confidence for each, and it is incapable of changing a traveller's status. The
booking happens in the confirm endpoint, after a human has looked.

Two things the model is not trusted with:

* **Its own certainty.** Confidence comes back per field and anything under
  `REVIEW_THRESHOLD` is flagged for the reviewer. A model that is confidently
  wrong is the failure mode that matters, so the review screen shows the
  document beside the fields regardless.
* **Its output shape.** The response is parsed defensively - fenced JSON,
  stray prose and missing keys are all expected - and a parse failure is
  recorded as FAILED rather than silently producing an empty booking.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.config import get_settings
from app.services import gemini

logger = logging.getLogger(__name__)
settings = get_settings()

#: Below this, the review screen highlights the field as needing a human read.
REVIEW_THRESHOLD = 0.75

#: The fields we ask for. Kept in one place because the prompt, the parser and
#: the review screen all have to agree on them.
FIELDS = (
    "booking_reference",
    "carrier",
    "service_number",
    "passenger_name",
    "origin",
    "destination",
    "depart_at",
    "arrive_at",
    "hotel_name",
    "check_in",
    "check_out",
    "fare_amount",
    "fare_currency",
)

_PROMPT = """You are reading a travel document for an Indian field-operations team.
It may be an airline ticket, a train or bus ticket, or a hotel booking confirmation.

Extract only what is actually printed on the document. Do not guess, do not infer
from context, and do not fill a field because it seems likely. If something is not
on the document, return null for it.

Return a single JSON object, no prose and no code fence, with exactly these keys:

{
  "booking_reference": "PNR, booking reference or confirmation number, or null",
  "carrier": "airline, railway or hotel chain name, or null",
  "service_number": "flight/train number, or null",
  "passenger_name": "primary passenger or guest name as printed, or null",
  "origin": "departure city or station, or null",
  "destination": "arrival city or station, or null",
  "depart_at": "departure as YYYY-MM-DDTHH:MM, or null",
  "arrive_at": "arrival as YYYY-MM-DDTHH:MM, or null",
  "hotel_name": "hotel name for an accommodation booking, or null",
  "check_in": "YYYY-MM-DD, or null",
  "check_out": "YYYY-MM-DD, or null",
  "fare_amount": "total fare or room rate actually printed, digits only e.g. 4512.00, or null",
  "fare_currency": "three-letter code for that amount, e.g. INR, or null",
  "confidence": {"<field name>": 0.0 to 1.0 for every field above},
  "document_type": "FLIGHT | TRAIN | BUS | HOTEL | UNKNOWN"
}

Confidence must reflect how clearly the value is printed. Use a low value when a
field is smudged, ambiguous, or you are inferring rather than reading.

For fare_amount, return only a total that is printed as a total. Do not add up
components, do not include a fare you are unsure covers the whole booking, and
return null rather than a guess - this number goes into a financial report."""


@dataclass
class Extraction:
    """What the model proposed, already coerced into storable types."""

    ok: bool
    fields: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    document_type: str | None = None
    raw: str | None = None
    model_id: str | None = None
    error: str | None = None

    @property
    def low_confidence_fields(self) -> list[str]:
        """Fields a human should read before confirming."""
        return sorted(
            name
            for name, value in self.confidence.items()
            if self.fields.get(name) is not None and value < REVIEW_THRESHOLD
        )


def _strip_fence(text: str) -> str:
    """Models fence JSON even when told not to. Take what is inside."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    # Otherwise take the outermost braces, so leading prose does not break json.
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text.strip()


def _as_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "").replace(" ", "T", 1)
    for pattern in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:19] if len(text) >= 19 else text, pattern)
        except ValueError:
            continue
    return None


def _as_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _as_amount(value: Any) -> Decimal | None:
    """A fare, or nothing. Never a guess.

    Strips the symbols and separators a ticket prints - "INR 4,512.00", "Rs.
    4512" - and refuses anything left that is not a plain number. A misread fare
    is worse than a blank one: blank gets typed in, wrong goes into a report.
    """
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if amount <= 0 or amount > Decimal("10000000"):
        return None   # a seven-figure domestic fare is a misread, not a fare
    return amount.quantize(Decimal("0.01"))


def _as_currency(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if len(code) == 3 and code.isalpha() else None


def _as_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def parse(raw: str) -> Extraction:
    """Turn a model response into storable fields, or an explained failure.

    Separate from the call so the awkward shapes - fenced JSON, a stray
    sentence, a missing key, a date the model invented a format for - can be
    tested without a network round trip.
    """
    try:
        payload = json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        return Extraction(ok=False, raw=raw, error=f"response was not JSON: {exc}"[:300])

    if not isinstance(payload, dict):
        return Extraction(ok=False, raw=raw, error="response was not a JSON object")

    fields: dict[str, Any] = {
        "booking_reference": _as_text(payload.get("booking_reference"), 120),
        "carrier": _as_text(payload.get("carrier"), 120),
        "service_number": _as_text(payload.get("service_number"), 60),
        "passenger_name": _as_text(payload.get("passenger_name"), 160),
        "origin": _as_text(payload.get("origin"), 160),
        "destination": _as_text(payload.get("destination"), 160),
        "depart_at": _as_datetime(payload.get("depart_at")),
        "arrive_at": _as_datetime(payload.get("arrive_at")),
        "hotel_name": _as_text(payload.get("hotel_name"), 160),
        "check_in": _as_date(payload.get("check_in")),
        "check_out": _as_date(payload.get("check_out")),
        "fare_amount": _as_amount(payload.get("fare_amount")),
        "fare_currency": _as_currency(payload.get("fare_currency")),
    }

    raw_confidence = payload.get("confidence")
    confidence: dict[str, float] = {}
    if isinstance(raw_confidence, dict):
        for name in FIELDS:
            value = raw_confidence.get(name)
            if isinstance(value, (int, float)):
                confidence[name] = max(0.0, min(1.0, float(value)))

    # A response with no reference and no dates read nothing useful. Better to
    # say so than to present an empty form as a successful extraction.
    if not any(fields.values()):
        return Extraction(
            ok=False,
            raw=raw,
            error="no fields could be read from this document",
        )

    return Extraction(
        ok=True,
        fields=fields,
        confidence=confidence,
        document_type=_as_text(payload.get("document_type"), 20),
        raw=raw,
    )


def extract(data: bytes, content_type: str) -> Extraction:
    """Send one document to the model and parse what comes back.

    Never raises: an unreachable model is an outcome the review screen has to
    show, not an exception that loses the upload the admin just made.
    """
    client = gemini.get_client()
    if client is None:
        return Extraction(ok=False, error="extraction is unavailable - check credentials")

    model_id = settings.gemini_extraction_model
    try:
        from google.genai import types

        response = client.models.generate_content(
            model=model_id,
            contents=[
                types.Part.from_bytes(data=data, mime_type=content_type),
                _PROMPT,
            ],
        )
        text = (response.text or "").strip()
    except Exception as exc:
        logger.exception("Ticket extraction call failed")
        return Extraction(
            ok=False, model_id=model_id, error=f"{type(exc).__name__}: {exc}"[:300]
        )

    if not text:
        return Extraction(ok=False, model_id=model_id, error="the model returned nothing")

    result = parse(text)
    result.model_id = model_id
    return result


# ---------------------------------------------------------------------------
# Several files, one booking
# ---------------------------------------------------------------------------


@dataclass
class Combined:
    """What several files say together, as one proposal for one booking.

    An admin often has more than one file for a trip: the e-ticket and the
    agent's invoice, two legs of a connecting flight, one ticket per traveller
    on a group, a hotel voucher and its receipt. Each file is read on its own
    when it is uploaded; this puts the readings together so the booking window
    fills from all of them, not just the first.
    """

    files: int = 0
    files_read: int = 0
    booking_reference: str | None = None
    carrier: str | None = None
    service_number: str | None = None
    depart_at: datetime | None = None
    arrive_at: datetime | None = None
    hotel_name: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    #: The total across the files, counting a booking once: an e-ticket and
    #: the invoice for the same PNR show the same fare, and adding them would
    #: double what was paid. None when no file printed a fare, or the files
    #: are in different currencies.
    fare_total: Decimal | None = None
    fare_currency: str | None = None
    #: Plain-language things the admin should look at before saving.
    notes: list[str] = field(default_factory=list)


def _distinct(values: list[str | None]) -> list[str]:
    """Each value once, first spelling kept, compared without case or spaces."""
    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        if not value or not str(value).strip():
            continue
        key = "".join(str(value).split()).casefold()
        if key not in seen:
            seen.add(key)
            kept.append(str(value).strip())
    return kept


def _joined(values: list[str | None], limit: int) -> str | None:
    kept = _distinct(values)
    return " / ".join(kept)[:limit] if kept else None


def combine(tickets: list) -> Combined:
    """Put the readings of several ticket files together, oldest upload first.

    `tickets` are rows with the extracted fields (TicketDocument). A file the
    model could not read contributes nothing but its count. References,
    carriers and numbers that differ are all kept, joined - two legs, two
    PNRs - and the journey runs from the earliest departure to the latest
    arrival, the stay from the earliest check-in to the latest check-out.
    """
    out = Combined(files=len(tickets))
    read = [t for t in tickets if t.extracted_at is not None and t.extraction_error is None]
    out.files_read = len(read)
    unread = out.files - out.files_read
    if unread:
        out.notes.append(
            "1 file could not be read - check it by eye."
            if unread == 1
            else f"{unread} files could not be read - check them by eye."
        )
    if not read:
        return out

    out.booking_reference = _joined([t.booking_reference for t in read], 120)
    out.carrier = _joined([t.carrier for t in read], 120)
    out.service_number = _joined([t.service_number for t in read], 60)
    out.hotel_name = _joined([t.hotel_name for t in read], 160)
    departs = [t.depart_at for t in read if t.depart_at]
    arrives = [t.arrive_at for t in read if t.arrive_at]
    out.depart_at = min(departs) if departs else None
    out.arrive_at = max(arrives) if arrives else None
    if out.depart_at and out.arrive_at and out.arrive_at < out.depart_at:
        out.arrive_at = None   # files from different trips; let the admin say
    ins = [t.check_in for t in read if t.check_in]
    outs = [t.check_out for t in read if t.check_out]
    out.check_in = min(ins) if ins else None
    out.check_out = max(outs) if outs else None

    # The fare: one per booking reference (the largest, if its files differ),
    # and every file with no reference on its own.
    priced = [t for t in read if t.fare_amount is not None]
    currencies = {(t.fare_currency or "INR").upper() for t in priced}
    if priced and len(currencies) == 1:
        per_booking: dict[str, Decimal] = {}
        loose = Decimal("0")
        for ticket in priced:
            key = "".join((ticket.booking_reference or "").split()).casefold()
            if key:
                per_booking[key] = max(per_booking.get(key, Decimal("0")), ticket.fare_amount)
            else:
                loose += ticket.fare_amount
        out.fare_total = sum(per_booking.values(), Decimal("0")) + loose
        out.fare_currency = currencies.pop()
        if len(priced) > 1:
            out.notes.append(
                f"The cost adds up the fares on {len(priced)} files"
                + (", counting each booking reference once." if per_booking else ".")
            )
    elif len(currencies) > 1:
        out.notes.append(
            f"The files show fares in {', '.join(sorted(currencies))} - enter the cost by hand."
        )

    references = _distinct([t.booking_reference for t in read])
    if len(references) > 1:
        out.notes.append(f"{len(references)} different references: {', '.join(references)}.")
    names = _distinct([t.passenger_name for t in read])
    if len(names) > 1:
        out.notes.append(f"Names on the files: {', '.join(names)}.")
    return out
