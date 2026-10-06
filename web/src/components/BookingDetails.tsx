import { BedDouble, Plane } from 'lucide-react';
import type { ReactNode } from 'react';

import { Field, Input } from '@/components/ui';
import { dayTime } from '@/lib/requests';
import type { BookingDetails } from '@/types';

/** The form's copy of the details: strings, "" for blank. */
export interface BookingDraft {
  carrier: string;
  service_number: string;
  depart_at: string;
  arrive_at: string;
  seat: string;
  hotel_name: string;
  hotel_address: string;
  notes: string;
}

export const EMPTY_BOOKING: BookingDraft = {
  carrier: '',
  service_number: '',
  depart_at: '',
  arrive_at: '',
  seat: '',
  hotel_name: '',
  hotel_address: '',
  notes: '',
};

/** The draft as the API takes it: blanks dropped, nothing at all as null. */
export function bookingBody(draft: BookingDraft): BookingDetails | null {
  const body: BookingDetails = {};
  for (const [key, value] of Object.entries(draft) as [keyof BookingDraft, string][]) {
    const tidy = value.trim();
    if (tidy) body[key] = key === 'depart_at' || key === 'arrive_at' ? `${tidy}:00`.slice(0, 19) : tidy;
  }
  return Object.keys(body).length ? body : null;
}

/** The fields to fill when marking someone booked: travel or hotel. */
export function BookingFields({
  draft,
  onChange,
  hotel,
}: {
  draft: BookingDraft;
  onChange: (next: BookingDraft) => void;
  hotel: boolean;
}) {
  const set = (key: keyof BookingDraft) => (e: { target: { value: string } }) =>
    onChange({ ...draft, [key]: e.target.value });
  const backwards = Boolean(draft.depart_at && draft.arrive_at && draft.arrive_at < draft.depart_at);

  if (hotel) {
    return (
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Hotel" htmlFor="bk-hotel" className="sm:col-span-2">
          <Input id="bk-hotel" value={draft.hotel_name} onChange={set('hotel_name')} placeholder="Lemon Tree, Hinjewadi" />
        </Field>
        <Field label="Address" htmlFor="bk-address" className="sm:col-span-2">
          <Input id="bk-address" value={draft.hotel_address} onChange={set('hotel_address')} placeholder="Plot 15, Phase 1, Hinjewadi, Pune" />
        </Field>
        <Field label="Notes for the traveller" htmlFor="bk-notes" className="sm:col-span-2">
          <Input id="bk-notes" value={draft.notes} onChange={set('notes')} placeholder="Check-in from 12:00 pm, breakfast included" />
        </Field>
      </div>
    );
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label="Airline / railway / bus operator" htmlFor="bk-carrier">
        <Input id="bk-carrier" value={draft.carrier} onChange={set('carrier')} placeholder="IndiGo" />
      </Field>
      <Field label="Flight / train / bus number" htmlFor="bk-number">
        <Input id="bk-number" value={draft.service_number} onChange={set('service_number')} placeholder="6E 4412" />
      </Field>
      <Field label="Departs" htmlFor="bk-depart">
        <Input id="bk-depart" type="datetime-local" value={draft.depart_at} onChange={set('depart_at')} />
      </Field>
      <Field label="Arrives" htmlFor="bk-arrive" error={backwards ? 'Arrival is before departure.' : undefined}>
        <Input id="bk-arrive" type="datetime-local" value={draft.arrive_at} onChange={set('arrive_at')} />
      </Field>
      <Field label="Seat / coach and berth" htmlFor="bk-seat">
        <Input id="bk-seat" value={draft.seat} onChange={set('seat')} placeholder="14C / B2-36" />
      </Field>
      <Field label="Notes for the traveller" htmlFor="bk-notes">
        <Input id="bk-notes" value={draft.notes} onChange={set('notes')} placeholder="Terminal 1, report 2 hours early" />
      </Field>
    </div>
  );
}

export const bookingDraftValid = (draft: BookingDraft) =>
  !(draft.depart_at && draft.arrive_at && draft.arrive_at < draft.depart_at);

/** The booking as the traveller reads it, under their name on My requests. */
export function BookingSummary({
  reference,
  details,
  title = 'Your booking',
  action,
  footer,
}: {
  reference: string | null;
  details: BookingDetails | null;
  title?: string;
  /** Beside the title - the traveller's "Download all". */
  action?: ReactNode;
  /** Under the details - the files on the booking, one per line. */
  footer?: ReactNode;
}) {
  if (!reference && !details && !action && !footer) return null;
  const d = details ?? {};
  const hotel = Boolean(d.hotel_name || d.hotel_address);
  const rows: [string, string | null | undefined][] = hotel
    ? [
        ['Booking', reference],
        ['Hotel', d.hotel_name],
        ['Address', d.hotel_address],
        ['Notes', d.notes],
      ]
    : [
        ['Booking', reference],
        ['With', [d.carrier, d.service_number].filter(Boolean).join(' ') || null],
        ['Departs', d.depart_at ? dayTime(d.depart_at) : null],
        ['Arrives', d.arrive_at ? dayTime(d.arrive_at) : null],
        ['Seat', d.seat],
        ['Notes', d.notes],
      ];
  const Icon = hotel ? BedDouble : Plane;
  return (
    <div className="mt-2 rounded-md border border-border bg-surface-sunken px-3 py-2">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-x-2">
        <p className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wide text-text-subtle">
          <Icon size={12} /> {title}
        </p>
        {action}
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
        {rows
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-text-subtle">{label}</dt>
              <dd className="font-medium text-text">{value}</dd>
            </div>
          ))}
      </dl>
      {footer}
    </div>
  );
}
