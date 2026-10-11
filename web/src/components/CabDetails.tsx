import { CalendarClock, Car } from 'lucide-react';

import { PhoneBadge } from '@/components/PhoneBadge';
import { Field, Input } from '@/components/ui';
import { dayTime } from '@/lib/requests';
import { cn, mobileDigits } from '@/lib/utils';
import {
  BOOKED_CAB_TYPES,
  CAB_TYPE_LABELS,
  type CabType,
  type TravelRequest,
} from '@/types';

/**
 * The car that went, and any ask to keep it a day longer - the same blocks on
 * My requests, where a traveller looks for the plate at the kerb, and on
 * Approvals, where an admin checks what they recorded.
 */

export function CabSent({
  request,
  title = 'Your cab',
  className,
}: {
  request: TravelRequest;
  title?: string;
  className?: string;
}) {
  if (!request.cab_vehicle_number) return null;
  return (
    <div className={cn('rounded-md border border-border bg-surface-sunken px-3 py-2.5', className)}>
      <p className="flex items-center gap-1.5 text-xs font-semibold">
        <Car size={13} />
        {title}
      </p>
      <dl className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-4">
        <div className="min-w-0">
          <dt className="text-2xs text-text-subtle">Cab</dt>
          <dd className="font-medium">
            {request.booked_cab_type ? CAB_TYPE_LABELS[request.booked_cab_type] : 'Cab'}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-2xs text-text-subtle">Vehicle number</dt>
          <dd className="font-mono font-medium tracking-wide">{request.cab_vehicle_number}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-2xs text-text-subtle">Driver</dt>
          <dd className="truncate font-medium">{request.cab_driver_name}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-2xs text-text-subtle">Driver&rsquo;s phone</dt>
          <dd>
            {request.cab_driver_phone && (
              <PhoneBadge
                phone={request.cab_driver_phone}
                name={request.cab_driver_name || 'Driver'}
                size="xs"
              />
            )}
          </dd>
        </div>
      </dl>
    </div>
  );
}

/** Where the latest "one more day" stands, in the words its reader needs. */
export function CabExtensionNote({
  request,
  className,
}: {
  request: TravelRequest;
  className?: string;
}) {
  const status = request.cab_extension_status;
  const days = request.cab_extended_days;
  if (!status && days === 0) return null;
  const until = request.end_at ? dayTime(request.end_at) : null;
  const extended =
    days > 0
      ? `Extended by ${days} ${days === 1 ? 'day' : 'days'}${until ? ` — now until ${until}` : ''}.`
      : null;

  if (status === 'PENDING') {
    return (
      <div className={cn('rounded-md bg-warning-soft px-3 py-2 text-xs', className)}>
        <p className="flex items-center gap-1.5 font-semibold text-warning">
          <CalendarClock size={13} />
          One more day asked for — waiting for an admin
        </p>
        <p className="mt-0.5 text-text-muted">
          {request.cab_extension_requested_by_name && `${request.cab_extension_requested_by_name}: `}
          {request.cab_extension_reason}
        </p>
        {extended && <p className="mt-0.5 text-text-subtle">{extended}</p>}
      </div>
    );
  }

  if (status === 'REJECTED') {
    return (
      <div className={cn('rounded-md bg-danger-soft px-3 py-2 text-xs', className)}>
        <p className="flex items-center gap-1.5 font-semibold text-danger">
          <CalendarClock size={13} />
          One more day was not approved
        </p>
        {request.cab_extension_comment && (
          <p className="mt-0.5 text-text-muted">
            {request.cab_extension_decided_by_name && `${request.cab_extension_decided_by_name}: `}
            {request.cab_extension_comment}
          </p>
        )}
        {extended && <p className="mt-0.5 text-text-subtle">{extended}</p>}
      </div>
    );
  }

  return (
    <div className={cn('rounded-md bg-success-soft px-3 py-2 text-xs', className)}>
      <p className="flex items-center gap-1.5 font-semibold text-success">
        <CalendarClock size={13} />
        {extended ?? 'One more day approved.'}
      </p>
      {request.cab_extension_comment && (
        <p className="mt-0.5 text-text-muted">
          {request.cab_extension_decided_by_name && `${request.cab_extension_decided_by_name}: `}
          {request.cab_extension_comment}
        </p>
      )}
    </div>
  );
}

// --- recording the car sent ----------------------------------------------

export interface CabDraft {
  booked_cab_type: CabType | '';
  vehicle_number: string;
  driver_name: string;
  driver_phone: string;
}

/** The form starts from what was recorded, else from the size asked for. */
export function cabDraftFrom(request: TravelRequest): CabDraft {
  const asked = request.cab_type && request.cab_type !== 'NO_PREFERENCE' ? request.cab_type : '';
  return {
    booked_cab_type: request.booked_cab_type ?? asked,
    vehicle_number: request.cab_vehicle_number ?? '',
    driver_name: request.cab_driver_name ?? '',
    driver_phone: request.cab_driver_phone ?? '',
  };
}

export const cabDraftComplete = (draft: CabDraft) =>
  Boolean(
    draft.booked_cab_type &&
      draft.vehicle_number.trim().length >= 4 &&
      draft.driver_name.trim().length >= 2 &&
      draft.driver_phone.trim(),
  );

/** Nothing typed beyond the size the form suggested. */
export const cabDraftEmpty = (draft: CabDraft) =>
  !draft.vehicle_number.trim() && !draft.driver_name.trim() && !draft.driver_phone.trim();

/** Whether saving would change anything - the server ignores a repeat, but
 *  the screen should not send one. */
export function cabDraftChanged(draft: CabDraft, request: TravelRequest): boolean {
  const tidy = (value: string) => value.trim().replace(/\s+/g, ' ');
  return (
    draft.booked_cab_type !== (request.booked_cab_type ?? '') ||
    tidy(draft.vehicle_number).toUpperCase() !== (request.cab_vehicle_number ?? '') ||
    tidy(draft.driver_name) !== (request.cab_driver_name ?? '') ||
    tidy(draft.driver_phone) !== (request.cab_driver_phone ?? '')
  );
}

export function CabBookingFields({
  draft,
  onChange,
  idPrefix,
  asked,
}: {
  draft: CabDraft;
  onChange: (next: CabDraft) => void;
  idPrefix: string;
  /** What the requester asked for, shown beside the choice. */
  asked?: string | null;
}) {
  return (
    <div className="space-y-3">
      <Field
        label="Cab sent"
        required
        hint={asked ? `Asked for: ${asked}.` : undefined}
      >
        <div className="grid grid-cols-2 gap-2" role="group" aria-label="Cab sent">
          {BOOKED_CAB_TYPES.map((type) => {
            const active = draft.booked_cab_type === type;
            return (
              <button
                key={type}
                type="button"
                aria-pressed={active}
                onClick={() => onChange({ ...draft, booked_cab_type: type })}
                className={cn(
                  'rounded-md border px-2 py-2 text-xs transition-colors',
                  active
                    ? 'border-primary bg-surface-sunken font-medium text-text'
                    : 'border-border text-text-muted hover:border-border-strong hover:text-text',
                )}
              >
                {CAB_TYPE_LABELS[type]}
              </button>
            );
          })}
        </div>
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Vehicle number" htmlFor={`${idPrefix}-vehicle`} required>
          <Input
            id={`${idPrefix}-vehicle`}
            value={draft.vehicle_number}
            maxLength={20}
            autoCapitalize="characters"
            onChange={(e) => onChange({ ...draft, vehicle_number: e.target.value })}
            placeholder="TS 09 EA 1234"
          />
        </Field>
        <Field label="Driver’s name" htmlFor={`${idPrefix}-driver`} required>
          <Input
            id={`${idPrefix}-driver`}
            value={draft.driver_name}
            maxLength={120}
            onChange={(e) => onChange({ ...draft, driver_name: e.target.value })}
            placeholder="Suresh Reddy"
          />
        </Field>
        <Field label="Driver’s phone" htmlFor={`${idPrefix}-phone`} required>
          <Input
            id={`${idPrefix}-phone`}
            type="tel"
            inputMode="numeric"
            value={draft.driver_phone}
            onChange={(e) => onChange({ ...draft, driver_phone: mobileDigits(e.target.value) })}
            placeholder="9876543210"
          />
        </Field>
      </div>
    </div>
  );
}
