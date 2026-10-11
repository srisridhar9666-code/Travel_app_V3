import {
  ArrowRight,
  BedDouble,
  Bus,
  CalendarClock,
  Car,
  MapPin,
  Plane,
  Train,
} from 'lucide-react';

import { routeParts } from '@/lib/places';
import { dayTime, daysBetween, stayDates } from '@/lib/requests';
import { cn } from '@/lib/utils';
import {
  CAB_TYPE_LABELS,
  TRAVEL_MODE_LABELS,
  type TravelRequest,
} from '@/types';

export interface RequestTripHighlightProps {
  request: Pick<
    TravelRequest,
    | 'request_type'
    | 'mode'
    | 'origin'
    | 'destination'
    | 'pickup_city'
    | 'drop_city'
    | 'start_at'
    | 'end_at'
    | 'hotel_city'
    | 'hotel_state'
    | 'check_in'
    | 'check_out'
    | 'cab_type'
  >;
  variant?: 'inline' | 'banner';
  className?: string;
}

function getModeDetails(request: RequestTripHighlightProps['request']) {
  if (request.request_type === 'HOTEL') {
    return {
      label: 'Hotel',
      icon: BedDouble,
      badgeStyle:
        'bg-purple-50 text-purple-700 border-purple-200 dark:bg-purple-950/40 dark:text-purple-300 dark:border-purple-800',
    };
  }

  if (request.request_type === 'LOCAL_CAB') {
    const cabTypeLabel = request.cab_type ? CAB_TYPE_LABELS[request.cab_type] : null;
    return {
      label: cabTypeLabel ? `Cab (${cabTypeLabel})` : 'Cab',
      icon: Car,
      badgeStyle:
        'bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800',
    };
  }

  // LONG_DISTANCE
  if (request.mode === 'FLIGHT') {
    return {
      label: 'Flight',
      icon: Plane,
      badgeStyle:
        'bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950/40 dark:text-sky-300 dark:border-sky-800',
    };
  }
  if (request.mode === 'TRAIN') {
    return {
      label: 'Train',
      icon: Train,
      badgeStyle:
        'bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-950/40 dark:text-indigo-300 dark:border-indigo-800',
    };
  }
  if (request.mode === 'BUS') {
    return {
      label: 'Bus',
      icon: Bus,
      badgeStyle:
        'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-800',
    };
  }
  if (request.mode === 'CAB') {
    return {
      label: 'Cab',
      icon: Car,
      badgeStyle:
        'bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800',
    };
  }

  return {
    label: request.mode ? TRAVEL_MODE_LABELS[request.mode] ?? 'Travel' : 'Flight',
    icon: Plane,
    badgeStyle:
      'bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950/40 dark:text-sky-300 dark:border-sky-800',
  };
}

/**
 * Prominent trip highlight pill or banner showing mode of travel, origin/destination
 * (or hotel city/state), and date/time or stay nights across approval queues, review cards,
 * and booking confirmation dialogs.
 */
export function RequestTripHighlight({
  request,
  variant = 'inline',
  className,
}: RequestTripHighlightProps) {
  const mode = getModeDetails(request);
  const ModeIcon = mode.icon;

  const isHotel = request.request_type === 'HOTEL';
  const { origin, destination } = routeParts(request);

  const hotelNights =
    isHotel && request.check_in && request.check_out
      ? Math.max(1, daysBetween(request.check_in, request.check_out))
      : 0;

  if (variant === 'banner') {
    return (
      <div
        className={cn(
          'rounded-lg border border-border bg-surface-sunken p-3.5 shadow-sm',
          className,
        )}
      >
        <div className="flex flex-wrap items-center justify-between gap-2.5">
          {/* Mode & Places */}
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={cn(
                'inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-semibold shadow-2xs',
                mode.badgeStyle,
              )}
            >
              <ModeIcon size={14} />
              {mode.label}
            </span>

            {isHotel ? (
              <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-text sm:text-base">
                <MapPin size={15} className="text-purple-600 dark:text-purple-400 shrink-0" />
                <span>{request.hotel_city || 'City not specified'}</span>
                {request.hotel_state && (
                  <span className="text-xs font-normal text-text-muted">
                    ({request.hotel_state})
                  </span>
                )}
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-text sm:text-base">
                <span className="tracking-tight">{origin}</span>
                <ArrowRight size={14} className="text-brand shrink-0" />
                <span className="tracking-tight">{destination}</span>
              </span>
            )}
          </div>

          {/* Date & Time pill */}
          <div className="flex flex-wrap items-center gap-2">
            {isHotel ? (
              request.check_in ? (
                <span
                  className="inline-flex items-center gap-1.5 rounded-md border border-purple-500/30 bg-purple-500/10 px-2.5 py-1 text-xs font-semibold text-purple-900 dark:text-purple-200"
                  title="Check-in and Check-out booking dates"
                >
                  <CalendarClock size={14} className="text-purple-600 dark:text-purple-400 shrink-0" />
                  <span>
                    Check-in: {stayDates({ check_in: request.check_in, check_out: request.check_out })}
                  </span>
                  {hotelNights > 0 && (
                    <span className="ml-1 rounded bg-purple-600/15 px-1.5 py-0.2 text-2xs font-bold">
                      {hotelNights} {hotelNights === 1 ? 'night' : 'nights'}
                    </span>
                  )}
                </span>
              ) : null
            ) : request.start_at ? (
              <div className="flex flex-wrap items-center gap-1.5">
                <span
                  className="inline-flex items-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-xs font-semibold text-amber-900 dark:text-amber-200"
                  title="Departure date and time to book"
                >
                  <CalendarClock size={14} className="text-amber-600 dark:text-amber-400 shrink-0" />
                  <span>{dayTime(request.start_at)}</span>
                </span>
                {request.end_at && (
                  <span className="inline-flex items-center gap-1 text-xs text-text-muted">
                    <span>return</span>
                    <span className="rounded bg-surface px-1.5 py-0.5 font-medium text-text border border-border">
                      {dayTime(request.end_at)}
                    </span>
                  </span>
                )}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  // Inline variant for queue cards and lists
  return (
    <div className={cn('inline-flex flex-wrap items-center gap-2', className)}>
      {/* Mode badge */}
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-2xs font-semibold shadow-2xs',
          mode.badgeStyle,
        )}
      >
        <ModeIcon size={12} />
        {mode.label}
      </span>

      {/* Places */}
      {isHotel ? (
        <span className="inline-flex items-center gap-1 text-sm font-semibold text-text">
          <MapPin size={13} className="text-purple-600 dark:text-purple-400 shrink-0" />
          <span>{request.hotel_city || 'City not specified'}</span>
          {request.hotel_state && (
            <span className="text-xs font-normal text-text-muted">({request.hotel_state})</span>
          )}
        </span>
      ) : (
        <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-text">
          <span>{origin}</span>
          <ArrowRight size={13} className="text-brand shrink-0" />
          <span>{destination}</span>
        </span>
      )}

      {/* Booking Date & Time badge */}
      {isHotel ? (
        request.check_in ? (
          <span
            className="inline-flex items-center gap-1 rounded-md border border-purple-500/30 bg-purple-500/10 px-2 py-0.5 text-xs font-semibold text-purple-900 dark:text-purple-200"
            title="Hotel booking dates"
          >
            <CalendarClock size={12} className="text-purple-600 dark:text-purple-400 shrink-0" />
            <span>
              {stayDates({ check_in: request.check_in, check_out: request.check_out })}
            </span>
            {hotelNights > 0 && (
              <span className="text-2xs font-normal opacity-85">
                ({hotelNights} {hotelNights === 1 ? 'nt' : 'nts'})
              </span>
            )}
          </span>
        ) : null
      ) : request.start_at ? (
        <span
          className="inline-flex items-center gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-xs font-semibold text-amber-900 dark:text-amber-200"
          title="Booking date and departure time"
        >
          <CalendarClock size={12} className="text-amber-600 dark:text-amber-400 shrink-0" />
          <span>{dayTime(request.start_at)}</span>
        </span>
      ) : null}
    </div>
  );
}
