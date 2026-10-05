import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BedDouble, CalendarPlus, Car, ClipboardCheck, Plane, ThumbsDown, ThumbsUp } from 'lucide-react';
import { useState } from 'react';
import toast from 'react-hot-toast';

import { CancellationAsks } from '@/components/CancellationAsks';
import { ManagerReview } from '@/components/ManagerReview';
import { Modal } from '@/components/Modal';
import { PriorityBadge } from '@/components/PriorityBadge';
import { ConflictList } from '@/components/RequestForm';
import { Badge, Button, Card, EmptyState, Field, PageHeader, Skeleton } from '@/components/ui';
import { errorMessage, fetchTeamReviews, recommendRequest } from '@/lib/api';
import { cabAsked, campaignLabel, itinerary } from '@/lib/requests';
import { formatInstant } from '@/lib/time';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import {
  RECOMMENDATION_LABELS,
  REQUEST_STATUS_LABELS,
  TRAVELLER_STATUS_LABELS,
  TRAVEL_MODE_LABELS,
  type ManagerRecommendation,
  type RequestTraveller,
  type ReviewFilter,
  type TravelRequest,
  type TravellerStatus,
} from '@/types';

const TYPE_ICON = { LONG_DISTANCE: Plane, LOCAL_CAB: Car, HOTEL: BedDouble } as const;

const TRAVELLER_TONE: Record<TravellerStatus, 'neutral' | 'success' | 'danger' | 'info'> = {
  PENDING: 'neutral',
  APPROVED: 'success',
  BOOKED: 'info',
  REJECTED: 'danger',
  CANCELLED: 'neutral',
};

const TABS: { key: ReviewFilter; label: string }[] = [
  { key: 'waiting', label: 'Waiting for you' },
  { key: 'reviewed', label: 'Reviewed' },
];

const TEXTAREA =
  'w-full rounded-md border border-border bg-surface px-3 py-2 text-base text-text shadow-sm transition-colors placeholder:text-text-subtle hover:border-border-strong sm:text-sm';

/** What the manager is about to say, held until the comment is typed. */
interface Draft {
  request: TravelRequest;
  verdict: ManagerRecommendation;
  /** Their own team members on it who are still pending - the ones it can cover. */
  team: RequestTraveller[];
}

/**
 * Team approvals: the first of the two levels.
 *
 * A manager recommends their team's requests, or does not, always with a
 * comment. An admin then makes the final decision and sees what they said; the
 * manager is copied on the decision email. Nothing here shows a cost.
 */
export default function TeamApprovalsPage() {
  const queryClient = useQueryClient();
  const me = useAuth((s) => s.user);
  const [tab, setTab] = useState<ReviewFilter>('waiting');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [comment, setComment] = useState('');
  const [covered, setCovered] = useState<number[]>([]);

  // Both lists load up front, so each tab's count is true before it is opened.
  // The waiting one shares its key with the sidebar badge.
  const waiting = useQuery({
    queryKey: ['team-reviews', 'waiting'],
    queryFn: () => fetchTeamReviews('waiting'),
  });
  const reviewed = useQuery({
    queryKey: ['team-reviews', 'reviewed'],
    queryFn: () => fetchTeamReviews('reviewed'),
  });
  const current = tab === 'waiting' ? waiting : reviewed;

  /** The travellers this manager answers for: the ones who report to them. */
  const mine = (request: TravelRequest) =>
    request.travellers.filter((t) => me && t.manager_id === me.id);

  const save = useMutation({
    mutationFn: (vars: Draft & { comment: string; travellerIds?: number[] }) =>
      recommendRequest(vars.request.id, {
        recommendation: vars.verdict,
        comment: vars.comment,
        traveller_ids: vars.travellerIds,
      }),
    meta: { errorFallback: 'Could not save your recommendation.' },
    onSuccess: (_, vars) => {
      toast.success(
        vars.verdict === 'RECOMMENDED'
          ? 'Recommended. An admin will make the final decision.'
          : 'Not recommended. An admin will make the final decision.',
      );
      close();
      queryClient.invalidateQueries({ queryKey: ['team-reviews'] });
      queryClient.invalidateQueries({ queryKey: ['requests'] });
    },
  });

  function close() {
    setDraft(null);
    setComment('');
    setCovered([]);
  }

  const start = (request: TravelRequest, verdict: ManagerRecommendation) => {
    const team = mine(request).filter((t) => t.status === 'PENDING');
    // Changing an earlier answer starts from what was said, so a small
    // correction does not mean retyping it.
    const earlier = team.find((t) => t.manager_comment)?.manager_comment ?? '';
    setComment(earlier);
    setCovered(team.map((t) => t.id));
    setDraft({ request, verdict, team });
  };

  const confirm = () => {
    if (!draft) return;
    save.mutate({
      ...draft,
      comment: comment.trim(),
      // Only named when they chose a subset; otherwise the server covers every
      // one of their people still pending, which is the same set.
      travellerIds: covered.length === draft.team.length ? undefined : covered,
    });
  };

  const rows = current.data?.items ?? [];
  const recommending = draft?.verdict === 'RECOMMENDED';
  const subject = draft
    ? draft.team.length === 1
      ? draft.team[0].full_name
      : `${draft.team.length} team members`
    : '';

  return (
    <div className="space-y-6">
      <PageHeader
        title="Team approvals"
        description="Your team's requests wait here for your recommendation. An admin makes the final decision and sees your comment, and you are copied on the decision email."
      />

      <CancellationAsks scope="manager" />

      <Card>
        <div role="group" aria-label="Which requests" className="flex gap-1 border-b border-border px-3 py-2">
          {TABS.map((item) => {
            const count = (item.key === 'waiting' ? waiting : reviewed).data?.total;
            return (
              <button
                key={item.key}
                type="button"
                aria-pressed={tab === item.key}
                onClick={() => setTab(item.key)}
                className={cn(
                  'rounded-md px-3 py-1.5 text-sm transition-colors',
                  tab === item.key
                    ? 'bg-surface-sunken font-medium text-text'
                    : 'text-text-muted hover:bg-surface-sunken hover:text-text',
                )}
              >
                {item.label}
                {count !== undefined && <span className="ml-1.5 text-text-subtle">{count}</span>}
              </button>
            );
          })}
        </div>

        {current.isPending ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-28 w-full" />
            ))}
          </div>
        ) : current.isError ? (
          <EmptyState
            icon={<ClipboardCheck size={28} />}
            title="Could not load your team's requests"
            description={errorMessage(current.error)}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<ClipboardCheck size={28} />}
            title={tab === 'waiting' ? 'Nothing waiting for you' : 'Nothing reviewed yet'}
            description={
              tab === 'waiting'
                ? 'When someone on your team raises a request, it appears here for your recommendation.'
                : 'Requests you have recommended, or not, appear here with the admin’s final decision.'
            }
          />
        ) : (
          <ul className="divide-y divide-border">
            {rows.map((request) => {
              const Icon = TYPE_ICON[request.request_type];
              const team = mine(request);
              const others = request.travellers.filter((t) => !team.includes(t));
              const pending = team.filter((t) => t.status === 'PENDING');
              const answered = pending.some((t) => t.manager_recommendation);
              return (
                <li key={request.id} className="px-4 py-4 sm:px-5">
                  <div className="flex flex-wrap items-start gap-3">
                    <div className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-md bg-surface-sunken text-text-muted">
                      <Icon size={15} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium">{itinerary(request)}</span>
                        <Badge tone={request.status === 'SUBMITTED' ? 'info' : 'neutral'}>
                          {REQUEST_STATUS_LABELS[request.status]}
                        </Badge>
                        <PriorityBadge priority={request.priority} />
                        {request.extends_request_id != null && (
                          <Badge tone="brand">
                            <CalendarPlus size={11} />
                            Extends request {request.extends_request_id}
                          </Badge>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-text-muted">
                        {campaignLabel(request)} · raised by {request.requester_name}
                        {/* A cab's size and distance, which a manager weighs too. */}
                        {cabAsked(request)
                          ? ` · ${cabAsked(request)}`
                          : request.mode && ` · ${TRAVEL_MODE_LABELS[request.mode]}`}
                        {request.submitted_at && (
                          <span className="hidden sm:inline"> · {formatInstant(request.submitted_at)}</span>
                        )}
                      </p>
                      {request.travel_reason && (
                        <p className="mt-1.5 text-sm">
                          <span className="text-text-muted">Reason: </span>
                          {request.travel_reason}
                        </p>
                      )}
                      {request.notes && (
                        <p className="mt-0.5 text-xs text-text-muted">Notes: {request.notes}</p>
                      )}
                      {request.extends_request_id != null && request.previous_booking && (
                        <p className="mt-0.5 text-xs text-text-muted">
                          Booked before as {request.previous_booking}
                        </p>
                      )}
                    </div>
                  </div>

                  {request.conflicts.length > 0 && (
                    <div className="mt-3">
                      <ConflictList
                        conflicts={request.conflicts}
                        footnote="The admin sees these clashes too, and has to give a reason to approve over one."
                      />
                    </div>
                  )}

                  <div className="mt-3 space-y-1.5">
                    {team.map((traveller) => (
                      <div
                        key={traveller.id}
                        className="flex flex-col gap-1.5 rounded-md border border-border px-3 py-2"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium">{traveller.full_name}</span>
                          {traveller.status !== 'PENDING' ? (
                            <Badge tone={TRAVELLER_TONE[traveller.status]}>
                              {TRAVELLER_STATUS_LABELS[traveller.status]}
                            </Badge>
                          ) : traveller.manager_recommendation ? (
                            <Badge tone="info">Waiting for an admin</Badge>
                          ) : (
                            <Badge tone="warning">Waiting for you</Badge>
                          )}
                        </div>
                        {traveller.manager_recommendation && (
                          <ManagerReview traveller={traveller} className="self-start" />
                        )}
                        {/* The final word, once there is one. */}
                        {traveller.decided_by_name && traveller.status !== 'PENDING' && (
                          <p className="text-xs text-text-muted">
                            <span className="font-medium text-text">
                              {TRAVELLER_STATUS_LABELS[traveller.status]} by {traveller.decided_by_name}
                            </span>
                            {traveller.decision_reason && ` - ${traveller.decision_reason}`}
                          </p>
                        )}
                      </div>
                    ))}
                    {others.length > 0 && (
                      <p className="text-xs text-text-subtle">
                        Also travelling: {others.map((t) => t.full_name).join(', ')}
                      </p>
                    )}
                  </div>

                  {pending.length > 0 && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <Button size="sm" onClick={() => start(request, 'RECOMMENDED')}>
                        <ThumbsUp size={14} />
                        Recommend
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => start(request, 'NOT_RECOMMENDED')}>
                        <ThumbsDown size={14} />
                        Do not recommend
                      </Button>
                      {answered && (
                        <span className="text-2xs text-text-subtle">
                          You can change your answer until an admin decides.
                        </span>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Modal
        open={draft !== null}
        onClose={close}
        title={draft ? `${recommending ? 'Recommend' : 'Do not recommend'} ${subject}` : ''}
        description="An admin makes the final decision and reads your comment. It is also quoted in the decision email, which you are copied on."
        footer={
          <>
            <Button variant="secondary" onClick={close}>
              Cancel
            </Button>
            <Button
              variant={recommending ? 'primary' : 'danger'}
              loading={save.isPending}
              disabled={comment.trim().length < 3 || covered.length === 0}
              onClick={confirm}
            >
              {draft ? RECOMMENDATION_LABELS[draft.verdict] : ''}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {draft && (
            <p className="rounded-md bg-surface-sunken px-3 py-2 text-xs text-text-muted">
              <span className="font-medium text-text">{itinerary(draft.request)}</span>
              {draft.request.travel_reason && ` - ${draft.request.travel_reason}`}
            </p>
          )}

          {draft && draft.team.length > 1 && (
            <fieldset className="space-y-1.5">
              <legend className="mb-1.5 text-xs font-medium">Who this covers</legend>
              {draft.team.map((traveller) => (
                <label key={traveller.id} className="flex cursor-pointer items-center gap-2.5 text-sm">
                  <input
                    type="checkbox"
                    checked={covered.includes(traveller.id)}
                    onChange={(e) =>
                      setCovered((now) =>
                        e.target.checked ? [...now, traveller.id] : now.filter((id) => id !== traveller.id),
                      )
                    }
                    className="h-4 w-4 accent-[rgb(var(--primary))]"
                  />
                  {traveller.full_name}
                </label>
              ))}
            </fieldset>
          )}

          <Field
            label="Comment for the admin"
            htmlFor="recommendation-comment"
            required
            hint="A sentence is enough: why this trip should, or should not, go ahead."
          >
            <textarea
              id="recommendation-comment"
              rows={3}
              maxLength={500}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={
                recommending
                  ? 'Needed on site for the store audit'
                  : 'Sana is already covering Pune that week'
              }
              className={TEXTAREA}
            />
          </Field>
        </div>
      </Modal>
    </div>
  );
}
