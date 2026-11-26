import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BedDouble, CalendarPlus, Car, ClipboardCheck, Plane, ThumbsDown, ThumbsUp } from 'lucide-react';
import { useState } from 'react';
import toast from 'react-hot-toast';

import { CancellationAsks } from '@/components/CancellationAsks';
import { CabTelemetryCard } from '@/components/CabTelemetryCard';
import { HotelStayVerificationCard } from '@/components/HotelStayVerificationCard';
import { ManagerReview } from '@/components/ManagerReview';
import { Modal } from '@/components/Modal';
import { PriorityBadge } from '@/components/PriorityBadge';
import { RequestTelemetryBadge } from '@/components/RequestTelemetryBadge';
import { RequestTripHighlight } from '@/components/RequestTripHighlight';
import { ConflictList } from '@/components/RequestForm';
import {
  Badge,
  Button,
  Card,
  DateRangeFilter,
  EmptyState,
  Field,
  Input,
  ItemCard,
  ItemList,
  ItemNumber,
  PageHeader,
  Skeleton,
  tabButtonClass,
} from '@/components/ui';
import { errorMessage, fetchTeamReviews, recommendRequest } from '@/lib/api';
import { cabAsked, campaignLabel, matchesDateRange, REQUEST_ACCENT } from '@/lib/requests';
import { formatInstant } from '@/lib/time';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import {
  RECOMMENDATION_LABELS,
  REQUEST_STATUS_LABELS,
  TRAVELLER_STATUS_LABELS,
  TRAVEL_MODE_LABELS,
  CAB_TYPE_LABELS,
  type CabType,
  type HotelOccupancy,
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
  const [search, setSearch] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [comment, setComment] = useState('');
  const [covered, setCovered] = useState<number[]>([]);
  const [occupancy, setOccupancy] = useState<HotelOccupancy>('SINGLE');
  const [cabType, setCabType] = useState<CabType>('NO_PREFERENCE');

  // Both lists load up front, so each tab's count is true before it is opened.
  // The waiting one shares its key with the sidebar badge.
  const waiting = useQuery({
    queryKey: ['team-reviews', 'waiting'],
    queryFn: () => fetchTeamReviews('waiting'),
    refetchInterval: 10000,
    refetchOnWindowFocus: true,
  });
  const reviewed = useQuery({
    queryKey: ['team-reviews', 'reviewed'],
    queryFn: () => fetchTeamReviews('reviewed'),
    refetchInterval: 10000,
    refetchOnWindowFocus: true,
  });
  const current = tab === 'waiting' ? waiting : reviewed;

  /** The travellers this manager answers for: the ones who report to them. */
  const mine = (request: TravelRequest) =>
    request.travellers.filter((t) => me && t.manager_id === me.id);

  const save = useMutation({
    mutationFn: (vars: Draft & { comment: string; travellerIds?: number[]; hotel_occupancy?: HotelOccupancy; cab_type?: CabType }) =>
      recommendRequest(vars.request.id, {
        recommendation: vars.verdict,
        comment: vars.comment,
        traveller_ids: vars.travellerIds,
        hotel_occupancy: vars.hotel_occupancy,
        cab_type: vars.cab_type,
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
    setOccupancy('SINGLE');
    setCabType('NO_PREFERENCE');
  }

  const start = (request: TravelRequest, verdict: ManagerRecommendation) => {
    const team = mine(request).filter((t) => t.status === 'PENDING');
    // Changing an earlier answer starts from what was said, so a small
    // correction does not mean retyping it.
    const earlier = team.find((t) => t.manager_comment)?.manager_comment ?? '';
    setComment(earlier);
    setCovered(team.map((t) => t.id));
    setOccupancy(request.hotel_occupancy || 'SINGLE');
    setCabType(request.cab_type || 'NO_PREFERENCE');
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
      hotel_occupancy: draft.request.request_type === 'HOTEL' && draft.verdict === 'RECOMMENDED' ? occupancy : undefined,
      cab_type: draft.request.request_type === 'LOCAL_CAB' && draft.verdict === 'RECOMMENDED' ? cabType : undefined,
    });
  };

  const rawRows = current.data?.items ?? [];
  const rows = rawRows.filter((r) => {
    if (!matchesDateRange(r, dateFrom, dateTo)) return false;
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      const hitId = String(r.id).includes(q);
      const hitRequester = r.requester_name?.toLowerCase().includes(q);
      const hitTraveller = r.travellers.some((t) => t.full_name.toLowerCase().includes(q));
      const hitPlace = [r.origin, r.destination, r.pickup_city, r.drop_city, r.hotel_city, r.project_name]
        .filter(Boolean)
        .some((v) => v!.toLowerCase().includes(q));
      if (!hitId && !hitRequester && !hitTraveller && !hitPlace) return false;
    }
    return true;
  });
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
        <div role="group" aria-label="Which requests" className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5 bg-surface-sunken/30">
          <div className="flex gap-1.5 rounded-lg border border-border/70 bg-surface-raised p-0.5">
            {TABS.map((item) => {
              const count = (item.key === 'waiting' ? waiting : reviewed).data?.total;
              const active = tab === item.key;
              return (
                <button
                  key={item.key}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setTab(item.key)}
                  className={tabButtonClass(active)}
                >
                  <span>{item.label}</span>
                  {count !== undefined && (
                    <span
                      className={cn(
                        'rounded-full px-1.5 py-0.2 text-2xs tabular-nums',
                        active ? 'bg-primary-fg/15 text-primary-fg' : 'bg-surface-sunken text-text-subtle',
                      )}
                    >
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-3">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search employee, place, campaign or no."
            className="min-w-40 flex-1 sm:max-w-64"
            aria-label="Search team requests"
          />
          <DateRangeFilter
            from={dateFrom}
            to={dateTo}
            onFromChange={setDateFrom}
            onToChange={setDateTo}
            onClear={() => {
              setDateFrom('');
              setDateTo('');
            }}
          />
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
          <ItemList className="rounded-b-xl">
            {rows.map((request) => {
              const Icon = TYPE_ICON[request.request_type];
              const team = mine(request);
              const others = request.travellers.filter((t) => !team.includes(t));
              const pending = team.filter((t) => t.status === 'PENDING');
              const answered = pending.some((t) => t.manager_recommendation);
              return (
                <ItemCard key={request.id} accent={REQUEST_ACCENT[request.status]}>
                  <div className="flex flex-wrap items-start gap-3">
                    <div className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-md bg-surface-sunken text-text-muted">
                      <Icon size={15} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
                        <ItemNumber value={request.id} />
                        <RequestTripHighlight request={request} />
                        <Badge tone={request.status === 'SUBMITTED' ? 'info' : 'neutral'}>
                          {REQUEST_STATUS_LABELS[request.status]}
                        </Badge>
                        <RequestTelemetryBadge
                          requestId={request.id}
                          requestType={request.request_type}
                          mode={request.mode}
                          requestStatus={request.status}
                        />
                        <PriorityBadge priority={request.priority} />
                        {request.extends_request_id != null && (
                          <Badge tone="brand">
                            <CalendarPlus size={11} />
                            Extends request {request.extends_request_id}
                          </Badge>
                        )}
                      </div>
                      <p className="mt-2 text-xs text-text-muted leading-relaxed">
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

                  {(request.request_type === 'LOCAL_CAB' || request.mode === 'CAB') && (
                    <div className="mt-3">
                      <CabTelemetryCard
                        requestId={request.id}
                        requestStatus={request.status}
                        cabTrip={request.cab_trip}
                        readOnly={!request.travellers.some((t) => t.user_id === me?.id)}
                        onUpdated={() => queryClient.invalidateQueries({ queryKey: ['team-reviews'] })}
                      />
                    </div>
                  )}

                  {request.request_type === 'HOTEL' && (
                    <div className="mt-3">
                      <HotelStayVerificationCard
                        requestId={request.id}
                        requestStatus={request.status}
                        hotelCity={request.hotel_city}
                        readOnly={!request.travellers.some((t) => t.user_id === me?.id)}
                        onUpdated={() => queryClient.invalidateQueries({ queryKey: ['team-reviews'] })}
                      />
                    </div>
                  )}
                </ItemCard>
              );
            })}
          </ItemList>
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
            <div className="space-y-2">
              <RequestTripHighlight request={draft.request} variant="banner" />
              {draft.request.travel_reason && (
                <p className="rounded-md bg-surface-sunken px-3 py-2 text-xs text-text-muted">
                  <span className="font-medium text-text">Reason:</span> {draft.request.travel_reason}
                </p>
              )}
            </div>
          )}

          {draft && draft.request.request_type === 'HOTEL' && recommending && (
            <div className="rounded-lg border border-indigo-200 dark:border-indigo-800/60 bg-indigo-50/50 dark:bg-indigo-950/20 p-3 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-indigo-950 dark:text-indigo-200">
                  Manager Occupancy Evaluation
                </span>
                {draft.request.city_tier && (
                  <span className="rounded bg-indigo-600/15 px-2 py-0.5 text-2xs font-bold text-indigo-800 dark:text-indigo-300">
                    {draft.request.city_tier === 'CATEGORY_A' ? 'Cat A (₹1,500 Single / ₹2,000 Double)' : draft.request.city_tier === 'CATEGORY_B' ? 'Cat B (₹1,000 Single / ₹1,500 Double)' : 'Cat C (₹750 Single / ₹1,200 Double)'}
                  </span>
                )}
              </div>
              <p className="text-2xs text-text-muted">
                Evaluate whether your team member is eligible for Single or Double occupancy for this stay.
              </p>
              <div className="flex gap-4 pt-1">
                <label className="flex cursor-pointer items-center gap-2 text-xs font-medium">
                  <input
                    type="radio"
                    name="occupancy"
                    value="SINGLE"
                    checked={occupancy === 'SINGLE'}
                    onChange={() => setOccupancy('SINGLE')}
                    className="h-4 w-4 accent-indigo-600"
                  />
                  Single Occupancy
                </label>
                <label className="flex cursor-pointer items-center gap-2 text-xs font-medium">
                  <input
                    type="radio"
                    name="occupancy"
                    value="DOUBLE"
                    checked={occupancy === 'DOUBLE'}
                    onChange={() => setOccupancy('DOUBLE')}
                    className="h-4 w-4 accent-indigo-600"
                  />
                  Double Occupancy
                </label>
              </div>
            </div>
          )}

          {draft && draft.request.request_type === 'LOCAL_CAB' && recommending && (
            <div className="rounded-lg border border-amber-200 dark:border-amber-800/60 bg-amber-50/50 dark:bg-amber-950/20 p-3 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-amber-950 dark:text-amber-200">
                  Vehicle Category Evaluation
                </span>
                <span className="text-2xs text-text-muted">
                  Requested: {CAB_TYPE_LABELS[draft.request.cab_type || 'NO_PREFERENCE']}
                </span>
              </div>
              <p className="text-2xs text-text-muted">
                Evaluate which car class your team members need (Dzire 4 seats vs Ertiga 7 seats) so the vendor dispatches the correct vehicle.
              </p>
              <div className="flex flex-wrap gap-4 pt-1">
                <label className="flex cursor-pointer items-center gap-2 text-xs font-medium">
                  <input
                    type="radio"
                    name="manager_cab_type"
                    value="NO_PREFERENCE"
                    checked={cabType === 'NO_PREFERENCE'}
                    onChange={() => setCabType('NO_PREFERENCE')}
                    className="h-4 w-4 accent-amber-600"
                  />
                  No Preference
                </label>
                <label className="flex cursor-pointer items-center gap-2 text-xs font-medium">
                  <input
                    type="radio"
                    name="manager_cab_type"
                    value="SEDAN"
                    checked={cabType === 'SEDAN'}
                    onChange={() => setCabType('SEDAN')}
                    className="h-4 w-4 accent-amber-600"
                  />
                  Dzire / Sedan (4 seats)
                </label>
                <label className="flex cursor-pointer items-center gap-2 text-xs font-medium">
                  <input
                    type="radio"
                    name="manager_cab_type"
                    value="SUV"
                    checked={cabType === 'SUV'}
                    onChange={() => setCabType('SUV')}
                    className="h-4 w-4 accent-amber-600"
                  />
                  Ertiga / SUV (7 seats)
                </label>
              </div>
            </div>
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
