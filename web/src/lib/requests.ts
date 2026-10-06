/** Small wordings shared by My requests and Approvals, so the two screens
 *  describe the same request the same way. */

import type { Accent } from '@/components/ui';
import { routeLabel } from '@/lib/places';
import {
  CAB_TRIP_LABELS,
  CAB_TYPE_LABELS,
  PRIORITY_LABELS,
  type CabTrip,
  type CabType,
  type RequestPriority,
  type RequestStatus,
  type TravelRequest,
} from '@/types';

/** The left edge of a request's card: the same reading as its status badge. */
export const REQUEST_ACCENT: Record<RequestStatus, Accent> = {
  DRAFT: 'neutral',
  SUBMITTED: 'info',
  PARTIALLY_APPROVED: 'warning',
  APPROVED: 'success',
  BOOKED: 'success',
  REJECTED: 'danger',
  CANCELLED: 'neutral',
  EXPIRED: 'warning',
};

const dayMonth = (iso: string) =>
  new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso).toLocaleDateString(undefined, {
    day: '2-digit',
    month: 'short',
  });

export const dayTime = (iso: string) =>
  new Date(iso).toLocaleString('en-IN', {
    hour12: true,
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });

/** A stay's nights as dates: "14 Oct – 16 Oct", or the check-in alone. */
export const stayDates = (stay: { check_in: string; check_out: string | null }) =>
  stay.check_out
    ? `${dayMonth(stay.check_in)} – ${dayMonth(stay.check_out)}`
    : dayMonth(stay.check_in);

/** Where and when, in one line: "Pune · 14 Oct – 16 Oct", or a route with its
 *  departure. */
export function itinerary(request: TravelRequest): string {
  if (request.request_type === 'HOTEL') {
    return `${request.hotel_city} · ${stayDates({ check_in: request.check_in!, check_out: request.check_out })}`;
  }
  return `${routeLabel(request)} · ${request.start_at ? dayTime(request.start_at) : ''}`;
}

/** The campaign as a person would name it. A request filed under the fallback
 *  "Other" campaign shows what the requester typed, not the fallback's name. */
export function campaignLabel(
  request: Pick<TravelRequest, 'other_project_name' | 'project_name' | 'project_code'>,
): string {
  return request.other_project_name
    ? `Other: ${request.other_project_name}`
    : request.project_name || request.project_code;
}

/** What a cab asked for, in one line: "Ertiga (7 seats) · Outstation, about
 *  250 km". Null for anything but a cab. */
export function cabAsked(
  request: Pick<TravelRequest, 'request_type' | 'cab_type' | 'cab_trip' | 'cab_distance_km'>,
): string | null {
  if (request.request_type !== 'LOCAL_CAB' || (!request.cab_type && !request.cab_trip)) return null;
  const parts = [CAB_TYPE_LABELS[request.cab_type ?? 'NO_PREFERENCE']];
  if (request.cab_trip) {
    parts.push(
      request.cab_distance_km
        ? `${CAB_TRIP_LABELS[request.cab_trip]}, about ${request.cab_distance_km} km`
        : CAB_TRIP_LABELS[request.cab_trip],
    );
  }
  return parts.join(' · ');
}

/** Field names in the revision diff that read badly with the underscores
 *  simply swapped for spaces. */
const REVISION_FIELDS: Record<string, string> = {
  cab_type: 'cab type',
  cab_trip: 'local or outstation',
  cab_distance_km: 'distance (km)',
};

export function revisionField(field: string): string {
  return REVISION_FIELDS[field] ?? field.replace(/_/g, ' ');
}

/** A value in the revision diff as the form showed it. Priority is stored as
 *  HIGH/MEDIUM/LOW; the person changing it picked High/Medium/Low. A cab's
 *  type and trip are stored as SUV/OUTSTATION; they picked "Ertiga (7 seats)"
 *  and "Outstation". */
export function revisionValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (field === 'priority' && typeof value === 'string' && value in PRIORITY_LABELS) {
    return PRIORITY_LABELS[value as RequestPriority];
  }
  if (field === 'cab_type' && typeof value === 'string' && value in CAB_TYPE_LABELS) {
    return CAB_TYPE_LABELS[value as CabType];
  }
  if (field === 'cab_trip' && typeof value === 'string' && value in CAB_TRIP_LABELS) {
    return CAB_TRIP_LABELS[value as CabTrip];
  }
  return String(value);
}

// --- wall-clock date arithmetic ---------------------------------------------
//
// Trip times are local as the requester typed them ("2026-10-06T09:00:00"), so
// days are moved on the string's own calendar fields - never through a time
// zone, which on a device set elsewhere would shift the day.

const DAY_MS = 24 * 60 * 60 * 1000;

/** "2026-10-06" moved on by `days` calendar days. */
export function addDays(date: string, days: number): string {
  const moved = new Date(`${date.slice(0, 10)}T00:00:00Z`);
  moved.setUTCDate(moved.getUTCDate() + days);
  return moved.toISOString().slice(0, 10);
}

/** Whole calendar days from one date to another. */
export function daysBetween(from: string, to: string): number {
  return Math.round(
    (Date.parse(`${to.slice(0, 10)}T00:00:00Z`) - Date.parse(`${from.slice(0, 10)}T00:00:00Z`)) /
      DAY_MS,
  );
}

/** The date and the "HH:mm" of a wall-clock time, as an input wants them. */
export const datePart = (iso: string) => iso.slice(0, 10);
export const timePart = (iso: string) => iso.slice(11, 16);

/** "Sat, 06 Oct" for a plain date, read the same on any device. */
export function dayLabel(date: string): string {
  return new Date(`${date.slice(0, 10)}T00:00:00Z`).toLocaleDateString('en-IN', {
    weekday: 'short',
    day: '2-digit',
    month: 'short',
    timeZone: 'UTC',
  });
}
