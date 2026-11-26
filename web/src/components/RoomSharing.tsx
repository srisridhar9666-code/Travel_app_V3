import { useMutation, useQuery } from '@tanstack/react-query';
import { BedDouble, Users } from 'lucide-react';
import toast from 'react-hot-toast';

import { Button } from '@/components/ui';
import { allotRoom, fetchRoomMatches, type RequestPayload } from '@/lib/api';
import { stayDates } from '@/lib/requests';
import { cn } from '@/lib/utils';
import {
  DESIGNATION_LABELS,
  type CoStayMatch,
  type RequestTraveller,
  type TravelRequest,
} from '@/types';

/**
 * Same-gender room sharing on hotel stays, at both ends: the requester's choice
 * on the form, offered as soon as a city is picked, and the admin's allotment on
 * the approval queue. The gender policy is the server's - it filters before any
 * colleague is returned - so nothing here re-checks it.
 */

/** The requester's room on a new hotel request. '' leaves it to the admin. */
export interface RoomPick {
  choice: '' | 'SHARE_EXISTING' | 'SEPARATE_ROOM';
  withUserId: number | null;
}

export const NO_ROOM_PICK: RoomPick = { choice: '', withUserId: null };

/** Colleagues staying in the form's city. While only the dates change, the
 *  last list stays up so it does not flicker; a new city starts empty, so one
 *  city's colleagues are never shown under another's name. */
export function useRoomMatches(args: {
  enabled: boolean;
  city: string;
  checkIn: string;
  checkOut: string;
  requestId?: number;
}) {
  return useQuery({
    queryKey: ['room-matches', args.city, args.checkIn, args.checkOut, args.requestId ?? null],
    queryFn: () =>
      fetchRoomMatches({
        city: args.city,
        check_in: args.checkIn || undefined,
        check_out: args.checkOut || undefined,
        request_id: args.requestId,
      }),
    enabled: args.enabled && Boolean(args.city),
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[1] === args.city ? previous : undefined,
    staleTime: 30_000,
  });
}

/** What the form sends for a pick, against the colleagues on offer now. A share
 *  with someone no longer on offer - the dates moved - is dropped rather than
 *  sent to be refused, and with nobody on offer there is nothing to choose. */
export function roomPayload(
  pick: RoomPick,
  matches: CoStayMatch[],
): Pick<RequestPayload, 'room_sharing' | 'share_with_user_id'> {
  if (matches.length === 0) return {};
  if (pick.choice === 'SEPARATE_ROOM') return { room_sharing: 'SEPARATE_ROOM' };
  if (
    pick.choice === 'SHARE_EXISTING' &&
    matches.some((m) => m.user_id === pick.withUserId && m.overlapping_nights > 0)
  ) {
    return { room_sharing: 'SHARE_EXISTING', share_with_user_id: pick.withUserId };
  }
  return {};
}

function matchDetail(match: CoStayMatch): string {
  const parts = [
    match.designation ? DESIGNATION_LABELS[match.designation] : null,
    match.hotel_name ? `Hotel: ${match.hotel_name}` : null,
    stayDates(match),
    match.overlapping_nights > 0
      ? `${match.overlapping_nights} ${match.overlapping_nights === 1 ? 'night' : 'nights'} in common`
      : null,
  ];
  return parts.filter(Boolean).join(' · ');
}

function Choice({
  checked,
  onSelect,
  title,
  detail,
}: {
  checked: boolean;
  onSelect: () => void;
  title: string;
  detail?: string;
}) {
  return (
    <label
      className={cn(
        'flex cursor-pointer items-start gap-2.5 rounded-md border px-2.5 py-2 transition-colors',
        checked ? 'border-info bg-surface' : 'border-border/70 hover:bg-surface/60',
      )}
    >
      <input
        type="radio"
        name="room-choice"
        checked={checked}
        onChange={onSelect}
        className="mt-0.5"
      />
      <span className="min-w-0">
        <span className="block text-xs font-medium text-text">{title}</span>
        {detail && <span className="block text-2xs text-text-muted">{detail}</span>}
      </span>
    </label>
  );
}

/**
 * The form's room box: who of the requester's gender is staying in the picked
 * city, and - once there are dates and it is a new request - the choice of
 * sharing with one of them, a room of their own, or leaving it to the admin.
 */
export function RoomChoice({
  city,
  hasDates,
  editing,
  matches,
  pick,
  onPick,
}: {
  city: string;
  hasDates: boolean;
  /** A saved request's room is chosen on My requests, not in the edit form. */
  editing: boolean;
  matches: CoStayMatch[];
  pick: RoomPick;
  onPick: (pick: RoomPick) => void;
}) {
  if (!city || matches.length === 0) return null;
  const chosen = roomPayload(pick, matches);
  const choosing = hasDates && !editing;

  return (
    <div className="rounded-md border border-info/40 bg-info-soft px-3 py-2.5 sm:col-span-2">
      <p className="flex items-center gap-1.5 text-xs font-semibold text-info">
        <Users size={13} />
        {matches.length === 1
          ? `A colleague is staying in ${city}`
          : `${matches.length} colleagues are staying in ${city}`}
      </p>
      <p className="mt-0.5 text-2xs text-text-subtle">
        Colleagues of your gender only. An admin confirms any shared room before it is booked.
      </p>

      {choosing ? (
        <fieldset className="mt-2 space-y-1.5">
          <legend className="sr-only">Your room</legend>
          {matches.map((match) => (
            <div key={match.user_id} className="relative">
              <Choice
                checked={
                  chosen.room_sharing === 'SHARE_EXISTING' &&
                  chosen.share_with_user_id === match.user_id
                }
                onSelect={() => onPick({ choice: 'SHARE_EXISTING', withUserId: match.user_id })}
                title={`Share a room with ${match.full_name}`}
                detail={matchDetail(match)}
              />
              {match.same_hotel && (
                <span className="absolute top-2 right-2 inline-flex items-center px-1.5 py-0.5 rounded text-2xs font-semibold bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300">
                  Exact Same Hotel
                </span>
              )}
            </div>
          ))}
          <Choice
            checked={chosen.room_sharing === 'SEPARATE_ROOM'}
            onSelect={() => onPick({ choice: 'SEPARATE_ROOM', withUserId: null })}
            title="A room of my own"
          />
          <Choice
            checked={!chosen.room_sharing}
            onSelect={() => onPick(NO_ROOM_PICK)}
            title="Let the admin decide"
          />
        </fieldset>
      ) : (
        <>
          <ul className="mt-1.5 space-y-1">
            {matches.map((match) => (
              <li key={match.user_id} className="text-xs leading-relaxed text-text-muted">
                <span className="font-medium text-text">{match.full_name}</span> ·{' '}
                {matchDetail(match)}
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-2xs text-text-subtle">
            {editing
              ? 'To ask for a shared room, open this request on My requests.'
              : 'Add your check-in date to choose whether to share a room.'}
          </p>
        </>
      )}
    </div>
  );
}

const LIVE = new Set(['PENDING', 'APPROVED', 'BOOKED']);

/**
 * One hotel traveller's room on the approval queue: who they share with, a
 * share they asked for, or the colleagues they could be put in one room with.
 * Allotting pairs both stays and tells both people; "Own room" undoes it.
 */
export function RoomAllotment({
  request,
  traveller,
  onChanged,
}: {
  request: TravelRequest;
  traveller: RequestTraveller;
  onChanged: () => void;
}) {
  const allot = useMutation({
    mutationFn: (withUserId: number | null) => allotRoom(request.id, traveller.id, withUserId),
    onSuccess: (_, withUserId) => {
      toast.success(
        withUserId
          ? 'Room shared. Both travellers have been told.'
          : `${traveller.full_name} has a room of their own.`,
      );
      onChanged();
    },
    meta: { errorFallback: 'Could not allot the room.' },
  });

  if (
    request.request_type !== 'HOTEL' ||
    request.is_draft ||
    request.is_cancelled ||
    !LIVE.has(traveller.status)
  ) {
    return null;
  }

  const busy = (withUserId: number | null) => allot.isPending && allot.variables === withUserId;
  const partner = traveller.share_with_name ?? 'a colleague';
  const asked = traveller.room_sharing === 'SHARE_EXISTING' && traveller.share_with_user_id;

  if (asked && traveller.share_confirmed) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="inline-flex items-center gap-1.5 rounded-md bg-success-soft px-2 py-1 font-medium text-success">
          <BedDouble size={13} />
          Sharing a room with {partner}
        </span>
        <Button size="sm" variant="ghost" loading={busy(null)} onClick={() => allot.mutate(null)}>
          Separate rooms
        </Button>
      </div>
    );
  }

  if (asked) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="inline-flex items-center gap-1.5 rounded-md bg-info-soft px-2 py-1 font-medium text-info">
          <BedDouble size={13} />
          Asked to share a room with {partner}
        </span>
        <Button
          size="sm"
          variant="secondary"
          loading={busy(traveller.share_with_user_id)}
          onClick={() => allot.mutate(traveller.share_with_user_id)}
        >
          Allot same room
        </Button>
        <Button size="sm" variant="ghost" loading={busy(null)} onClick={() => allot.mutate(null)}>
          Own room
        </Button>
      </div>
    );
  }

  const matches = traveller.room_matches ?? [];
  if (matches.length === 0) return null;
  return (
    <div className="rounded-md border border-info/40 bg-info-soft px-2.5 py-2">
      <p className="flex items-center gap-1.5 text-xs font-semibold text-info">
        <Users size={13} />
        Can share a room - same gender, staying in {request.hotel_city} on these nights
        {traveller.room_sharing === 'SEPARATE_ROOM' && (
          <span className="font-normal text-text-subtle">(asked for a room of their own)</span>
        )}
      </p>
      <ul className="mt-1.5 space-y-1.5">
        {matches.map((match) => (
          <li key={match.user_id} className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 text-xs text-text-muted">
              <span className="font-medium text-text">{match.full_name}</span> ·{' '}
              {matchDetail(match)}
              {match.same_hotel && (
                <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded text-2xs font-semibold bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300">
                  Same Hotel
                </span>
              )}
            </span>
            <Button
              size="sm"
              variant="secondary"
              className="ml-auto"
              loading={busy(match.user_id)}
              onClick={() => allot.mutate(match.user_id)}
            >
              Allot same room
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
