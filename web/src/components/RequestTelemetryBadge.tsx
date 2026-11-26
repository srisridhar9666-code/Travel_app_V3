import { useQuery } from '@tanstack/react-query';
import { Building2, Car, CheckCircle2, Clock } from 'lucide-react';

import { fetchCabTelemetries, fetchHotelVerifications } from '@/lib/api';
import { formatInstant } from '@/lib/time';
import type {
  CabTelemetry,
  HotelStayVerification,
  RequestStatus,
  RequestType,
  TravelMode,
} from '@/types';

interface RequestTelemetryBadgeProps {
  requestId: number;
  requestType: RequestType;
  mode?: TravelMode | null;
  requestStatus: RequestStatus;
}

const LIVE_STATUSES: RequestStatus[] = ['APPROVED', 'BOOKED', 'READY_FOR_VENDOR'];

/**
 * Compact, high-visibility pill showing whether a traveller has actually started
 * their cab trip or checked into their hotel. Shares the React Query cache with
 * CabTelemetryCard and HotelStayVerificationCard so no extra network calls occur.
 */
export function RequestTelemetryBadge({
  requestId,
  requestType,
  mode,
  requestStatus,
}: RequestTelemetryBadgeProps) {
  const isCab = requestType === 'LOCAL_CAB' || mode === 'CAB';
  const isHotel = requestType === 'HOTEL';
  const isLive = LIVE_STATUSES.includes(requestStatus);

  const cabQuery = useQuery<CabTelemetry[]>({
    queryKey: ['cab-telemetry', requestId],
    queryFn: () => fetchCabTelemetries(requestId),
    enabled: isLive && isCab,
    refetchInterval: 15000,
  });

  const hotelQuery = useQuery<HotelStayVerification[]>({
    queryKey: ['hotel-verification', requestId],
    queryFn: () => fetchHotelVerifications(requestId),
    enabled: isLive && isHotel,
    refetchInterval: 15000,
  });

  if (!isLive || (!isCab && !isHotel)) return null;

  if (isCab) {
    if (cabQuery.isLoading) return null;
    const trips = cabQuery.data ?? [];
    const active = trips.find((t) => t.status === 'IN_PROGRESS');
    if (active) {
      return (
        <span
          className="inline-flex items-center gap-1.5 rounded-full bg-sky-500/15 px-2.5 py-0.5 text-2xs font-semibold text-sky-700 dark:text-sky-300 ring-1 ring-inset ring-sky-500/30"
          title={
            active.start_time_ist
              ? `Started at ${formatInstant(active.start_time_ist)}`
              : 'Cab trip currently in progress'
          }
        >
          <span className="h-1.5 w-1.5 rounded-full bg-sky-500 animate-pulse" />
          <Car size={11} />
          Trip Started
          {active.start_odometer_km != null ? ` (${active.start_odometer_km} km)` : ''}
        </span>
      );
    }
    if (trips.length > 0) {
      const latest = trips[trips.length - 1];
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-2xs font-semibold text-emerald-700 dark:text-emerald-300 ring-1 ring-inset ring-emerald-500/30">
          <CheckCircle2 size={11} />
          Trip Completed
          {latest.total_km != null ? ` · ${latest.total_km} km` : ''}
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-2xs font-semibold text-amber-800 dark:text-amber-300 ring-1 ring-inset ring-amber-500/30">
        <Clock size={11} />
        Trip Not Started
      </span>
    );
  }

  if (hotelQuery.isLoading) return null;
  const stays = hotelQuery.data ?? [];
  const activeStay = stays.find((v) => v.status === 'CHECKED_IN');
  if (activeStay) {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full bg-sky-500/15 px-2.5 py-0.5 text-2xs font-semibold text-sky-700 dark:text-sky-300 ring-1 ring-inset ring-sky-500/30"
        title={
          activeStay.check_in_time
            ? `Checked in at ${formatInstant(activeStay.check_in_time)}`
            : 'Employee checked into hotel'
        }
      >
        <span className="h-1.5 w-1.5 rounded-full bg-sky-500 animate-pulse" />
        <Building2 size={11} />
        Checked In{activeStay.room_number ? ` · Rm ${activeStay.room_number}` : ''}
      </span>
    );
  }
  if (stays.length > 0) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-2xs font-semibold text-emerald-700 dark:text-emerald-300 ring-1 ring-inset ring-emerald-500/30">
        <CheckCircle2 size={11} />
        Checked Out
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-2xs font-semibold text-amber-800 dark:text-amber-300 ring-1 ring-inset ring-amber-500/30">
      <Clock size={11} />
      Not Checked In
    </span>
  );
}
