import { useMutation } from '@tanstack/react-query';
import { BedDouble, CalendarPlus, Car } from 'lucide-react';
import { useState } from 'react';
import toast from 'react-hot-toast';

import { Modal } from '@/components/Modal';
import { Button, Field, Input } from '@/components/ui';
import { extendTrip } from '@/lib/api';
import { addDays, datePart, dayLabel, daysBetween, itinerary, timePart } from '@/lib/requests';
import { cn } from '@/lib/utils';
import type { TravelRequest } from '@/types';

/**
 * Carry a decided cab or stay on for more days.
 *
 * The work ran over: the cab is wanted tomorrow too, or two more nights at the
 * hotel. Rather than raising the whole trip again, the traveller picks how much
 * longer and why; the extension goes to the admins as a request of its own,
 * linked to this one, and the admin can book it "the same as before".
 */

const QUICK = [1, 2, 3];

/** When an extension of this cab would start and end, n days on: the same
 *  pickup time the day after the cab is let go, until the same end time. */
function cabDefaults(request: TravelRequest, days: number) {
  const lastDay = datePart(request.end_at ?? request.start_at!);
  const pickup = `${addDays(lastDay, 1)}T${timePart(request.start_at!)}`;
  const until = request.end_at ? `${addDays(lastDay, days)}T${timePart(request.end_at)}` : '';
  return { pickup, until };
}

/** The night an extension of this stay starts: the day it checks out. */
const stayStarts = (request: TravelRequest) =>
  request.check_out ?? addDays(request.check_in!, 1);

/** Mount with `key={request.id}`, so each trip opens on its own defaults. */
export function ExtendTripModal({
  request,
  me,
  onClose,
  onExtended,
}: {
  request: TravelRequest;
  /** The signed-in person: always on the extension, since they ask for it. */
  me: number | undefined;
  onClose: () => void;
  onExtended: (extension: TravelRequest) => void;
}) {
  const cab = request.request_type === 'LOCAL_CAB';
  const riding = request.travellers.filter(
    (t) => t.status === 'APPROVED' || t.status === 'BOOKED',
  );
  const [days, setDays] = useState(1);
  const [pickup, setPickup] = useState(() => (cab ? cabDefaults(request, 1).pickup : ''));
  const [until, setUntil] = useState(() => (cab ? cabDefaults(request, 1).until : ''));
  const [checkOut, setCheckOut] = useState(() =>
    cab ? '' : addDays(stayStarts(request), 1),
  );
  const [reason, setReason] = useState('');
  const [who, setWho] = useState<number[]>(() => riding.map((t) => t.id));

  const pick = (n: number) => {
    setDays(n);
    if (cab) {
      const next = cabDefaults(request, n);
      setPickup(next.pickup);
      setUntil(next.until);
    } else {
      setCheckOut(addDays(stayStarts(request), n));
    }
  };

  const save = useMutation({
    mutationFn: () =>
      extendTrip(request.id, {
        reason: reason.trim(),
        traveller_ids: riding.length > 1 ? who : undefined,
        ...(cab
          ? { start_at: pickup ? `${pickup}:00` : null, end_at: until ? `${until}:00` : null }
          : { check_out: checkOut || null }),
      }),
    meta: { errorFallback: 'Could not ask for the extension.' },
    onSuccess: (extension) => {
      toast.success(`Extension sent as request ${extension.id} - an admin will decide`);
      onExtended(extension);
    },
  });

  const starts = cab ? null : stayStarts(request);
  const nights = starts && checkOut ? daysBetween(starts, checkOut) : 0;
  const wasUntil = request.end_at ?? request.start_at ?? '';
  const pickupTooEarly = cab && pickup !== '' && `${pickup}:00` <= wasUntil;
  const untilBeforePickup = cab && until !== '' && pickup !== '' && until < pickup;
  const valid =
    reason.trim().length >= 3 &&
    (cab ? pickup !== '' && !pickupTooEarly && !untilBeforePickup : nights >= 1);
  const manager = request.travellers.find((t) => t.user_id === me)?.manager_name;

  return (
    <Modal
      open
      onClose={onClose}
      title={cab ? 'Extend this cab' : 'Extend this stay'}
      description={itinerary(request)}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Not now
          </Button>
          <Button loading={save.isPending} disabled={!valid} onClick={() => save.mutate()}>
            <CalendarPlus size={15} />
            Ask to extend
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <p className="mb-1.5 text-xs font-medium">
            {cab ? 'How many more days?' : 'How many more nights?'}
          </p>
          <div className="grid grid-cols-3 gap-2" role="group" aria-label="How much longer">
            {QUICK.map((n) => (
              <button
                key={n}
                type="button"
                aria-pressed={days === n}
                onClick={() => pick(n)}
                className={cn(
                  'rounded-md border px-2 py-2 text-sm transition-colors',
                  days === n
                    ? 'border-primary bg-surface-sunken font-medium text-text'
                    : 'border-border text-text-muted hover:border-border-strong hover:text-text',
                )}
              >
                +{n} {cab ? (n === 1 ? 'day' : 'days') : n === 1 ? 'night' : 'nights'}
              </button>
            ))}
          </div>
        </div>

        {cab ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label="Pickup"
              htmlFor="ext-pickup"
              required
              error={pickupTooEarly ? 'After the cab booked now is let go.' : undefined}
            >
              <Input
                id="ext-pickup"
                type="datetime-local"
                value={pickup}
                onChange={(e) => {
                  setPickup(e.target.value);
                  setDays(0);
                }}
              />
            </Field>
            <Field
              label="Cab needed until"
              htmlFor="ext-until"
              hint="Optional, like any cab."
              error={untilBeforePickup ? 'Before the pickup.' : undefined}
            >
              <Input
                id="ext-until"
                type="datetime-local"
                value={until}
                min={pickup || undefined}
                onChange={(e) => {
                  setUntil(e.target.value);
                  setDays(0);
                }}
              />
            </Field>
          </div>
        ) : (
          <Field
            label="New check-out"
            htmlFor="ext-checkout"
            required
            hint={
              nights >= 1
                ? `${nights} more ${nights === 1 ? 'night' : 'nights'}: ${dayLabel(starts!)} to ${dayLabel(checkOut)}.`
                : `After ${dayLabel(starts!)}, when the stay now ends.`
            }
          >
            <Input
              id="ext-checkout"
              type="date"
              value={checkOut}
              min={starts ? addDays(starts, 1) : undefined}
              onChange={(e) => {
                setCheckOut(e.target.value);
                setDays(0);
              }}
            />
          </Field>
        )}

        {riding.length > 1 && (
          <fieldset>
            <legend className="mb-1.5 text-xs font-medium">Who needs it</legend>
            <div className="space-y-1.5">
              {riding.map((t) => {
                const mine = t.user_id === me;
                return (
                  <label
                    key={t.id}
                    className="flex cursor-pointer items-center gap-2.5 rounded-md border border-border px-3 py-2 text-sm"
                  >
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 accent-[rgb(var(--primary))]"
                      checked={mine || who.includes(t.id)}
                      disabled={mine}
                      onChange={(e) =>
                        setWho((now) =>
                          e.target.checked ? [...now, t.id] : now.filter((id) => id !== t.id),
                        )
                      }
                    />
                    {t.full_name}
                    {mine && <span className="text-2xs text-text-subtle">you</span>}
                  </label>
                );
              })}
            </div>
          </fieldset>
        )}

        <Field
          label="Why is it needed?"
          htmlFor="ext-reason"
          required
          hint="The admin reads this before deciding."
        >
          <Input
            id="ext-reason"
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
            placeholder={cab ? 'Two more stores to audit tomorrow' : 'The audit runs two more days'}
          />
        </Field>

        <div className="flex gap-2.5 rounded-md bg-surface-sunken px-3 py-2.5 text-xs text-text-muted">
          {cab ? (
            <Car size={14} className="mt-0.5 shrink-0" />
          ) : (
            <BedDouble size={14} className="mt-0.5 shrink-0" />
          )}
          <p>
            It goes to the admins as a new request linked to this one, and what is booked now stays
            as it is.{' '}
            {cab
              ? 'Usually the same cab and driver come back; if not, you are told the new ones.'
              : 'The admin books the extra nights - at the same hotel when it has room.'}
            {manager && ` ${manager} is asked to recommend it.`}
          </p>
        </div>
      </div>
    </Modal>
  );
}
