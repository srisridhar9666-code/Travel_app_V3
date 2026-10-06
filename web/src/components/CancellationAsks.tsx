import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ban } from 'lucide-react';
import { useState } from 'react';
import toast from 'react-hot-toast';

import { Modal } from '@/components/Modal';
import { Button, Card, CardHeader, Field, ItemCard, ItemList, ItemNumber } from '@/components/ui';
import { decideCancellation, errorMessage, fetchRequests } from '@/lib/api';
import { dayTime, itinerary } from '@/lib/requests';
import type { TravelRequest } from '@/types';

const TEXTAREA =
  'w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text transition-colors placeholder:text-text-subtle hover:border-border-strong';

/**
 * Trips already approved or booked whose requester asked to cancel. An admin
 * sees every one; a manager sees their team's. Approving cancels the trip;
 * rejecting keeps it, with the comment the requester reads.
 */
export function CancellationAsks({ scope }: { scope: 'admin' | 'manager' }) {
  const queryClient = useQueryClient();
  const asks = useQuery({
    queryKey: ['queue', 'cancellations'],
    queryFn: () => fetchRequests({ mine: false, cancellation: 'pending', page_size: 100 }),
  });
  const [deciding, setDeciding] = useState<{ request: TravelRequest; approve: boolean } | null>(null);
  const [comment, setComment] = useState('');
  const [error, setError] = useState<string | null>(null);

  const decide = useMutation({
    mutationFn: () =>
      decideCancellation(deciding!.request.id, {
        approve: deciding!.approve,
        comment: comment.trim() || null,
      }),
    onSuccess: (_, __) => {
      toast.success(
        deciding?.approve
          ? 'Cancelled - the requester is told'
          : 'The trip stands - the requester is told why',
      );
      setDeciding(null);
      for (const key of ['queue', 'queue-counts', 'requests', 'request']) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
    onError: (err) => setError(errorMessage(err, 'Could not record that decision.')),
  });

  const items = (asks.data?.items ?? []).filter((r) => r.can_decide_cancellation);
  if (items.length === 0) return null;

  const tooShort = deciding !== null && !deciding.approve && comment.trim().length < 3;

  return (
    <Card className="border-danger/40">
      <CardHeader
        title={`${items.length} ${items.length === 1 ? 'trip' : 'trips'} asked to be cancelled`}
        description={
          scope === 'admin'
            ? 'These are already approved or booked. Approving cancels the trip - call off any booking with the vendor.'
            : 'Your team asked to cancel these approved or booked trips. An admin may answer too; the first answer counts.'
        }
      />
      <ItemList className="rounded-b-xl">
        {items.map((request) => (
          <ItemCard key={request.id} accent="danger" className="flex flex-wrap items-start gap-3">
            <div className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-md bg-danger-soft text-danger">
              <Ban size={15} />
            </div>
            {/* At least 10rem: on a phone the buttons drop below instead of
                squeezing the trip into a sliver beside them. */}
            <div className="min-w-0 flex-1 basis-40">
              <p className="text-sm font-medium">
                <ItemNumber value={request.id} className="mr-1.5 align-middle" />
                {itinerary(request)}
              </p>
              <p className="mt-0.5 text-xs text-text-muted">
                <span className="font-medium text-text">
                  {request.cancellation_requested_by_name ?? request.requester_name}:
                </span>{' '}
                {request.cancellation_reason}
              </p>
              {request.cancellation_requested_at && (
                <p className="mt-0.5 text-2xs text-text-subtle">
                  Asked {dayTime(request.cancellation_requested_at)}
                </p>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5">
              <Button
                size="sm"
                variant="danger"
                onClick={() => {
                  setDeciding({ request, approve: true });
                  setComment('');
                  setError(null);
                }}
              >
                Approve cancel
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  setDeciding({ request, approve: false });
                  setComment('');
                  setError(null);
                }}
              >
                Keep the trip
              </Button>
            </div>
          </ItemCard>
        ))}
      </ItemList>

      <Modal
        open={deciding !== null}
        onClose={() => setDeciding(null)}
        title={deciding?.approve ? 'Cancel this trip?' : 'Keep this trip?'}
        description={deciding ? itinerary(deciding.request) : undefined}
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeciding(null)}>
              Back
            </Button>
            <Button
              variant={deciding?.approve ? 'danger' : 'primary'}
              loading={decide.isPending}
              disabled={tooShort}
              onClick={() => decide.mutate()}
            >
              {deciding?.approve ? 'Approve cancel' : 'Keep the trip'}
            </Button>
          </>
        }
      >
        {deciding && (
          <div className="space-y-3">
            <p className="rounded-md bg-surface-sunken px-3 py-2.5 text-xs text-text-muted">
              <span className="font-medium text-text">Their reason:</span>{' '}
              {deciding.request.cancellation_reason}
            </p>
            <Field
              label={deciding.approve ? 'Comment (optional)' : 'Why it goes ahead - the requester reads this'}
              htmlFor="cancellation_comment"
              required={!deciding.approve}
            >
              <textarea
                id="cancellation_comment"
                rows={2}
                maxLength={500}
                className={TEXTAREA}
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder={deciding.approve ? 'Cancelled with the airline' : 'Tickets are non-refundable'}
              />
            </Field>
            {error && <p className="text-xs text-danger">{error}</p>}
          </div>
        )}
      </Modal>
    </Card>
  );
}

/** Where an ask to cancel stands, for the requester's own list. */
export function CancellationNote({ request }: { request: TravelRequest }) {
  if (request.is_cancelled || !request.cancellation_status) {
    return null;
  }
  if (request.cancellation_status === 'PENDING') {
    return (
      <p className="rounded-md bg-warning-soft px-3 py-2 text-xs text-warning">
        You asked to cancel this trip ({request.cancellation_reason}). It stands until an admin
        or your manager approves.
      </p>
    );
  }
  if (request.cancellation_status === 'REJECTED') {
    return (
      <p className="rounded-md bg-surface-sunken px-3 py-2 text-xs text-text-muted">
        Cancelling was not approved by {request.cancellation_decided_by_name ?? 'an admin'}
        {request.cancellation_comment ? ` - ${request.cancellation_comment}` : ''}. The trip goes ahead.
      </p>
    );
  }
  return null;
}
