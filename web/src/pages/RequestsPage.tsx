import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BedDouble,
  Ban,
  CalendarPlus,
  Car,
  CheckCircle2,
  ChevronDown,
  Download,
  FileText,
  History,
  Pencil,
  Plane,
  Plus,
  Send,
  Users,
} from 'lucide-react';
import { useState } from 'react';
import toast from 'react-hot-toast';

import { CabExtensionNote, CabSent } from '@/components/CabDetails';
import { BookingSummary } from '@/components/BookingDetails';
import { CancellationNote } from '@/components/CancellationAsks';
import { ExtendTripModal } from '@/components/ExtendTripModal';
import { Modal } from '@/components/Modal';
import { PriorityBadge } from '@/components/PriorityBadge';
import RequestForm, { ConflictList } from '@/components/RequestForm';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Field,
  Input,
  ItemCard,
  ItemList,
  ItemNumber,
  Select,
  Skeleton,
} from '@/components/ui';
import {
  cancelRequest,
  errorMessage,
  fetchMyTicketFile,
  fetchMyTicketsZip,
  fetchNotifications,
  fetchRequest,
  fetchRequests,
  fetchRevisions,
  setRoomSharing,
  submitRequest,
} from '@/lib/api';
import { openFileTab, showFile } from '@/lib/files';
import { routeLabel } from '@/lib/places';
import {
  REQUEST_ACCENT,
  cabAsked,
  dayLabel,
  campaignLabel,
  revisionField,
  revisionValue,
} from '@/lib/requests';
import { formatInstant, todayInIndia } from '@/lib/time';
import { useAuth } from '@/store/auth';
import {
  REQUEST_STATUS_LABELS,
  REQUEST_TYPE_LABELS,
  ROOM_SHARING_LABELS,
  TRAVELLER_STATUS_LABELS,
  TRAVEL_MODE_LABELS,
  type RequestStatus,
  type RequestTraveller,
  type RequestType,
  type TravelRequest,
} from '@/types';

const TYPE_ICON = { LONG_DISTANCE: Plane, LOCAL_CAB: Car, HOTEL: BedDouble } as const;

const STATUS_TONE: Record<RequestStatus, 'neutral' | 'info' | 'success' | 'warning' | 'danger'> = {
  DRAFT: 'neutral',
  SUBMITTED: 'info',
  PARTIALLY_APPROVED: 'warning',
  APPROVED: 'success',
  BOOKED: 'success',
  REJECTED: 'danger',
  CANCELLED: 'neutral',
  EXPIRED: 'neutral',
};

const dayMonth = (iso: string) =>
  new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso).toLocaleDateString(undefined, {
    day: '2-digit',
    month: 'short',
  });

const dayTime = (iso: string) =>
  new Date(iso).toLocaleString('en-IN', {
    hour12: true,
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });

/** One line describing where and when, whatever the request type. */
function itinerary(request: TravelRequest): string {
  if (request.request_type === 'HOTEL') {
    const nights = request.check_out
      ? `${dayMonth(request.check_in!)} – ${dayMonth(request.check_out)}`
      : dayMonth(request.check_in!);
    return `${request.hotel_city} · ${nights}`;
  }
  return `${routeLabel(request)} · ${request.start_at ? dayTime(request.start_at) : ''}`;
}

/** Co-stay offers are not in the list response - they are only actionable on one
 *  request at a time - so an expanded row fetches the detail for them. */
function useExpandedDetail(requestId: number | null) {
  return useQuery({
    queryKey: ['request', requestId],
    queryFn: () => fetchRequest(requestId!),
    enabled: requestId !== null,
  });
}

/** The revision trail, fetched only when someone asks for it. This is the
 *  "edited N times, expand for the diff" view from addendum A1. */
function RevisionHistory({ requestId }: { requestId: number }) {
  const revisions = useQuery({
    queryKey: ['revisions', requestId],
    queryFn: () => fetchRevisions(requestId),
  });

  if (revisions.isPending) return <Skeleton className="h-16 w-full" />;
  const rows = revisions.data ?? [];
  if (rows.length === 0) return <p className="text-xs text-text-subtle">No history yet.</p>;

  return (
    <ol className="space-y-3">
      {rows.map((revision) => (
        <li key={revision.revision_number} className="border-l-2 border-border pl-3">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-xs font-medium">
              {revision.revision_number}. {revision.summary}
            </span>
            <span className="text-2xs text-text-subtle">
              {revision.editor_name} · {formatInstant(revision.created_at)}
            </span>
          </div>
          {revision.changes && (
            <dl className="mt-1.5 space-y-0.5">
              {Object.entries(revision.changes).map(([field, change]) => (
                <div key={field} className="flex flex-wrap gap-x-1.5 text-2xs">
                  <dt className="text-text-subtle">{revisionField(field)}</dt>
                  <dd className="text-text-muted">
                    <span className="line-through opacity-70">
                      {revisionValue(field, change.from)}
                    </span>
                    {' → '}
                    <span className="font-medium text-text">
                      {revisionValue(field, change.to)}
                    </span>
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </li>
      ))}
    </ol>
  );
}

export default function RequestsPage() {
  const queryClient = useQueryClient();
  const me = useAuth((s) => s.user);

  const [statusFilter, setStatusFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [search, setSearch] = useState('');
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<TravelRequest | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  // The tab opens inside the click; the file follows once it has arrived.
  const downloadTicket = useMutation({
    mutationFn: (vars: {
      requestId: number;
      travellerId: number;
      ticketId: number;
      name: string;
      tab: Window | null;
    }) =>
      showFile(
        vars.tab,
        () => fetchMyTicketFile(vars.requestId, vars.travellerId, vars.ticketId),
        vars.name,
      ),
    meta: { errorFallback: 'Could not download the ticket.' },
  });
  const [cancelling, setCancelling] = useState<TravelRequest | null>(null);
  const [cancelReason, setCancelReason] = useState('');
  const [extending, setExtending] = useState<TravelRequest | null>(null);

  // Every file at once, for a booking that has several.
  const downloadAll = useMutation({
    mutationFn: (vars: { requestId: number; travellerId: number }) =>
      showFile(
        null,
        () => fetchMyTicketsZip(vars.requestId, vars.travellerId),
        `request-${vars.requestId}-tickets.zip`,
      ),
    meta: { errorFallback: 'Could not download the tickets.' },
  });

  /** The files on someone's booking, one per line, each to open or save. */
  const fileList = (request: TravelRequest, traveller: RequestTraveller) => {
    const files = (traveller.ticket_files ?? []).filter((f) => f.confirmed);
    if (files.length === 0) return undefined;
    return (
      <ul className="mt-2 space-y-1 border-t border-border pt-2">
        {files.map((file) => (
          <li key={file.id} className="flex items-center gap-2 text-xs">
            <FileText size={13} className="shrink-0 text-text-subtle" />
            <span className="min-w-0 flex-1 truncate">{file.file_name ?? 'Ticket'}</span>
            <Button
              size="sm"
              variant="link"
              className="h-7 shrink-0 px-0"
              loading={downloadTicket.isPending && downloadTicket.variables?.ticketId === file.id}
              onClick={() =>
                downloadTicket.mutate({
                  requestId: request.id,
                  travellerId: traveller.id,
                  ticketId: file.id,
                  name: file.file_name ?? 'ticket',
                  tab: openFileTab(),
                })
              }
            >
              <Download size={13} />
              Open
            </Button>
          </li>
        ))}
      </ul>
    );
  };

  /** "Download all" beside the title, when there is more than one file. */
  const allFiles = (request: TravelRequest, traveller: RequestTraveller) => {
    const count = (traveller.ticket_files ?? []).filter((f) => f.confirmed).length;
    if (count < 2) return undefined;
    return (
      <Button
        size="sm"
        variant="link"
        className="h-7 px-0"
        loading={downloadAll.isPending && downloadAll.variables?.travellerId === traveller.id}
        onClick={() => downloadAll.mutate({ requestId: request.id, travellerId: traveller.id })}
      >
        <Download size={13} />
        Download all {count}
      </Button>
    );
  };

  const requests = useQuery({
    queryKey: ['requests', statusFilter, typeFilter, search],
    queryFn: () =>
      fetchRequests({
        mine: true,
        status: statusFilter || undefined,
        type: typeFilter || undefined,
        search: search.trim() || undefined,
        page_size: 100,
      }),
  });

  // The same query as the bell, so reading a notice there clears it here.
  const notifications = useQuery({ queryKey: ['my-notices'], queryFn: fetchNotifications });
  const detail = useExpandedDetail(expanded);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['requests'] });
    queryClient.invalidateQueries({ queryKey: ['revisions'] });
    queryClient.invalidateQueries({ queryKey: ['request'] });
    // An admin's own request sits in the queue and the reports as well.
    queryClient.invalidateQueries({ queryKey: ['queue'] });
    queryClient.invalidateQueries({ queryKey: ['queue-counts'] });
    queryClient.invalidateQueries({ queryKey: ['insights'] });
    queryClient.invalidateQueries({ queryKey: ['travel-logs'] });
  };

  const submit = useMutation({
    mutationFn: (id: number) => submitRequest(id),
    onSuccess: (saved) => {
      toast.success(`Request ${saved.id} submitted`);
      refresh();
    },
  });

  const share = useMutation({
    mutationFn: (vars: { requestId: number; travellerId: number; withUserId: number | null }) =>
      setRoomSharing(vars.requestId, {
        traveller_id: vars.travellerId,
        choice: vars.withUserId === null ? 'SEPARATE_ROOM' : 'SHARE_EXISTING',
        share_with_user_id: vars.withUserId,
      }),
    onSuccess: (updated) => {
      toast.success(
        updated.travellers.some((t) => t.room_sharing === 'SHARE_EXISTING')
          ? 'Asked to share — an admin will confirm it'
          : 'Separate room requested',
      );
      refresh();
    },
  });

  const cancel = useMutation({
    mutationFn: () => cancelRequest(cancelling!.id, cancelReason),
    onSuccess: (saved) => {
      toast.success(
        saved.cancellation_status === 'PENDING' && !saved.is_cancelled
          ? 'Sent for approval - it stands until an admin or your manager agrees'
          : `Request ${saved.id} cancelled`,
      );
      setCancelling(null);
      setCancelReason('');
      refresh();
    },
  });

  const rows = requests.data?.items ?? [];
  // Unread only: an ask that has been read - or answered - is not still waiting.
  const coStayNotices = (notifications.data ?? []).filter(
    (n) => n.kind === 'COSTAY_REQUESTED' && n.read_at === null,
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">My requests</h1>
          <p className="mt-1.5 max-w-2xl text-sm text-text-muted">
            Travel, cab and hotel requests you have raised or been tagged onto. You can edit one
            freely until an admin acts on it — every change is recorded.
          </p>
        </div>
        <Button
          onClick={() => {
            setEditing(null);
            setFormOpen(true);
          }}
        >
          <Plus size={15} />
          New request
        </Button>
      </div>

      {coStayNotices.length > 0 && (
        <Card className="border-info/40 bg-info-soft">
          <div className="flex items-start gap-2.5 px-5 py-3.5">
            <Users size={15} className="mt-0.5 shrink-0 text-info" />
            <div className="min-w-0">
              <p className="text-xs font-semibold text-info">
                {coStayNotices.length === 1
                  ? 'A colleague has asked to share your room'
                  : `${coStayNotices.length} colleagues have asked to share your room`}
              </p>
              {coStayNotices.slice(0, 3).map((notice) => (
                <p key={notice.id} className="mt-1 text-xs leading-relaxed text-text-muted">
                  {notice.body}
                </p>
              ))}
            </div>
          </div>
        </Card>
      )}

      <Card>
        <CardHeader
          title={`${requests.data?.total ?? 0} ${
            requests.data?.total === 1 ? 'request' : 'requests'
          }`}
        />

        {/* The filters sit in their own row rather than in the header's action
            slot: three controls and a heading do not fit side by side on a
            phone, and ground staff work from phones. */}
        <div className="flex flex-wrap gap-2 border-b border-border px-5 py-3">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search place or notes"
            className="min-w-40 flex-1 sm:max-w-56"
            aria-label="Search requests"
          />
          <Select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            aria-label="Filter by type"
            className="w-[calc(50%-0.25rem)] sm:w-40"
          >
            <option value="">All types</option>
            {(Object.keys(REQUEST_TYPE_LABELS) as RequestType[]).map((type) => (
              <option key={type} value={type}>
                {REQUEST_TYPE_LABELS[type]}
              </option>
            ))}
          </Select>
          <Select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Filter by status"
            className="w-[calc(50%-0.25rem)] sm:w-40"
          >
            <option value="">All statuses</option>
            {(Object.keys(REQUEST_STATUS_LABELS) as RequestStatus[]).map((status) => (
              <option key={status} value={status}>
                {REQUEST_STATUS_LABELS[status]}
              </option>
            ))}
          </Select>
        </div>

        {requests.isPending ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full" />
            ))}
          </div>
        ) : requests.isError ? (
          <EmptyState
            icon={<Plane size={28} />}
            title="Could not load your requests"
            description={errorMessage(requests.error)}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<Plane size={28} />}
            title={
              search || statusFilter || typeFilter ? 'Nothing matches that' : 'No requests yet'
            }
            description={
              search || statusFilter || typeFilter
                ? 'Try a different search or filter.'
                : 'Raise one for a flight, train, bus, local cab or hotel stay.'
            }
            action={
              !search && !statusFilter && !typeFilter ? (
                <Button
                  onClick={() => {
                    setEditing(null);
                    setFormOpen(true);
                  }}
                >
                  <Plus size={15} />
                  New request
                </Button>
              ) : undefined
            }
          />
        ) : (
          <ItemList>
            {rows.map((request) => {
              const Icon = TYPE_ICON[request.request_type];
              const isOwner = request.requester_id === me?.id;
              const open = expanded === request.id;
              const myRow = request.travellers.find((t) => t.user_id === me?.id);

              return (
                <ItemCard key={request.id} accent={REQUEST_ACCENT[request.status]}>
                  <div className="flex flex-wrap items-start gap-3">
                    <div className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-md bg-surface-sunken text-text-muted">
                      <Icon size={15} />
                    </div>

                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <ItemNumber value={request.id} />
                        <span className="text-sm font-medium">{itinerary(request)}</span>
                        <Badge tone={STATUS_TONE[request.status]}>
                          {REQUEST_STATUS_LABELS[request.status]}
                        </Badge>
                        <PriorityBadge priority={request.priority} />
                        {request.edit_count > 0 && (
                          <button
                            type="button"
                            onClick={() => setExpanded(open ? null : request.id)}
                            className="inline-flex items-center gap-1 rounded-full bg-surface-sunken px-2 py-0.5 text-2xs font-semibold text-text-muted ring-1 ring-inset ring-border hover:text-text"
                          >
                            <History size={10} />
                            edited {request.edit_count}
                            {request.edit_count === 1 ? ' time' : ' times'}
                          </button>
                        )}
                      </div>

                      <p className="mt-1 text-xs text-text-muted">
                        {campaignLabel(request)}
                        {/* A cab says what kind and how far; "Cab" alone says
                            nothing the icon has not. */}
                        {cabAsked(request)
                          ? ` · ${cabAsked(request)}`
                          : request.mode && ` · ${TRAVEL_MODE_LABELS[request.mode]}`}
                        {!isOwner && ` · raised by ${request.requester_name}`}
                        {request.notes && ` · ${request.notes}`}
                      </p>

                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {request.travellers.map((traveller) => (
                          <span
                            key={traveller.id}
                            className="inline-flex items-center gap-1 rounded-full bg-surface-sunken px-2 py-0.5 text-2xs text-text-muted ring-1 ring-inset ring-border"
                            title={`${traveller.full_name} — ${
                              TRAVELLER_STATUS_LABELS[traveller.status]
                            }`}
                          >
                            {traveller.full_name}
                            <span className="text-text-subtle">
                              {TRAVELLER_STATUS_LABELS[traveller.status]}
                            </span>
                            {traveller.room_sharing !== 'NOT_OFFERED' && (
                              <span className="text-info">
                                {ROOM_SHARING_LABELS[traveller.room_sharing]}
                                {traveller.room_sharing === 'SHARE_EXISTING' &&
                                  (traveller.share_confirmed ? ' ✓' : ' (pending)')}
                              </span>
                            )}
                          </span>
                        ))}
                      </div>

                      {/* Extensions read as one trip carried on: each links to
                          the other. */}
                      {(request.extends_request_id || request.extended_by_request_id) && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {request.extends_request_id && (
                            <Badge tone="brand">
                              <CalendarPlus size={11} />
                              Extends request {request.extends_request_id}
                            </Badge>
                          )}
                          {request.extended_by_request_id && (
                            <Badge tone="info">
                              <CalendarPlus size={11} />
                              Extended by request {request.extended_by_request_id}
                            </Badge>
                          )}
                        </div>
                      )}

                      {request.cancel_reason && (
                        <p className="mt-2 text-xs text-text-subtle">
                          Cancelled{request.cancelled_by_name ? ` by ${request.cancelled_by_name}` : ''}:{' '}
                          {request.cancel_reason}
                        </p>
                      )}
                      <div className="mt-2">
                        <CancellationNote request={request} />
                      </div>

                    </div>

                    <div className="flex shrink-0 gap-1">
                      {isOwner && request.is_draft && (
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Submit"
                          loading={submit.isPending && submit.variables === request.id}
                          onClick={() => submit.mutate(request.id)}
                        >
                          <Send size={14} />
                        </Button>
                      )}
                      {isOwner && request.is_editable && (
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Edit"
                          onClick={() => {
                            setEditing(request);
                            setFormOpen(true);
                          }}
                        >
                          <Pencil size={14} />
                        </Button>
                      )}
                      {isOwner && !request.is_cancelled && request.cancellation_status !== 'PENDING' && (
                        <Button
                          variant="ghost"
                          size="sm"
                          title={request.cancel_needs_approval ? 'Ask to cancel' : 'Cancel'}
                          onClick={() => {
                            setCancelling(request);
                            setCancelReason('');
                          }}
                        >
                          <Ban size={14} />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        title={open ? 'Hide detail' : 'Show detail'}
                        aria-expanded={open}
                        onClick={() => setExpanded(open ? null : request.id)}
                      >
                        <ChevronDown
                          size={14}
                          className={open ? 'rotate-180 transition-transform' : 'transition-transform'}
                        />
                      </Button>
                    </div>
                  </div>

                  <div className="empty:hidden">
                  {/* Full width, like the cab below: on a phone the column beside the
                      action icons is too narrow for a PNR and file names. A cab's car
                      is its own block; here its files. A
                      ticket is offered to its traveller and to whoever asked
                      for the trip - the people the server will hand it to. */}
                  {request.travellers
                    .filter(
                      (t) =>
                        t.status === 'BOOKED' &&
                        (request.request_type === 'LOCAL_CAB'
                          ? fileList(request, t)
                          : t.booking_reference || t.booking_details || fileList(request, t)),
                    )
                    .map((t) => (
                      <BookingSummary
                        key={t.id}
                        reference={request.request_type === 'LOCAL_CAB' ? null : t.booking_reference}
                        details={request.request_type === 'LOCAL_CAB' ? null : t.booking_details}
                        title={
                          request.request_type === 'LOCAL_CAB'
                            ? t.user_id === me?.id
                              ? 'Your cab booking'
                              : `${t.full_name}'s cab booking`
                            : t.user_id === me?.id
                              ? 'Your booking'
                              : `${t.full_name}'s booking`
                        }
                        action={allFiles(request, t)}
                        footer={fileList(request, t)}
                      />
                    ))}
                  </div>

                  {/* Full width, below the row like the clash warnings: on a
                      phone the column beside the action icons is too narrow
                      for a plate and a phone number. */}
                  {(request.request_type === 'LOCAL_CAB' || request.can_extend) && (
                    <div className="mt-3 space-y-2 empty:hidden">
                      {request.request_type === 'LOCAL_CAB' && (
                        <>
                          <CabSent request={request} />
                          <CabExtensionNote request={request} />
                        </>
                      )}
                      {request.can_extend && (
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                          <Button variant="secondary" size="sm" onClick={() => setExtending(request)}>
                            <CalendarPlus size={14} />
                            {request.request_type === 'LOCAL_CAB'
                              ? 'Need the cab longer?'
                              : 'Need more nights?'}
                          </Button>
                          {/* Open until midnight on the trip's last day; after
                              that, extra days are a new request. */}
                          {request.extend_until && (
                            <span
                              className={
                                request.extend_until === todayInIndia()
                                  ? 'text-2xs font-medium text-warning'
                                  : 'text-2xs text-text-subtle'
                              }
                            >
                              {request.extend_until === todayInIndia()
                                ? 'You can ask until midnight tonight'
                                : `You can ask until midnight on ${dayLabel(request.extend_until)}`}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {request.conflicts.length > 0 && (
                    <div className="mt-3">
                      <ConflictList conflicts={request.conflicts} />
                    </div>
                  )}

                  {/* Co-stay offers apply to the requester's own bed, so they are
                      shown only to the person who raised the stay, and only while
                      the request is still theirs to change. */}
                  {open &&
                    isOwner &&
                    request.is_editable &&
                    request.request_type === 'HOTEL' &&
                    myRow &&
                    myRow.room_sharing === 'NOT_OFFERED' &&
                    (detail.data?.costay_matches.length ?? 0) > 0 && (
                      <div className="mt-3 rounded-md border border-info/40 bg-info-soft px-3 py-2.5">
                        <p className="flex items-center gap-1.5 text-xs font-semibold text-info">
                          <Users size={13} />
                          Share a room?
                        </p>
                        <ul className="mt-2 space-y-1.5">
                          {(detail.data?.costay_matches ?? []).map((match) => (
                            <li
                              key={match.user_id}
                              className="flex flex-wrap items-center gap-2 text-xs text-text-muted"
                            >
                              <span className="font-medium text-text">{match.full_name}</span>
                              <span className="text-text-subtle">
                                {match.overlapping_nights}{' '}
                                {match.overlapping_nights === 1 ? 'night' : 'nights'} in common
                              </span>
                              <Button
                                size="sm"
                                variant="secondary"
                                className="ml-auto"
                                loading={share.isPending}
                                onClick={() =>
                                  share.mutate({
                                    requestId: request.id,
                                    travellerId: myRow.id,
                                    withUserId: match.user_id,
                                  })
                                }
                              >
                                Ask to share
                              </Button>
                            </li>
                          ))}
                        </ul>
                        <div className="mt-2 flex items-center justify-between gap-2">
                          <p className="text-2xs text-text-subtle">
                            An admin confirms any shared room before it is booked, and your
                            colleague is told you asked.
                          </p>
                          <Button
                            size="sm"
                            variant="ghost"
                            loading={share.isPending}
                            onClick={() =>
                              share.mutate({
                                requestId: request.id,
                                travellerId: myRow.id,
                                withUserId: null,
                              })
                            }
                          >
                            Separate room
                          </Button>
                        </div>
                      </div>
                    )}

                  {open && (
                    <div className="mt-3 rounded-md border border-border bg-surface-sunken px-3 py-3">
                      <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold">
                        <History size={12} />
                        Edit history
                      </p>
                      <RevisionHistory requestId={request.id} />
                      {!request.is_editable && !request.is_cancelled && (
                        <p className="mt-3 flex items-center gap-1.5 text-2xs text-text-subtle">
                          <CheckCircle2 size={11} />
                          An admin has acted on this, so it is locked. Cancel and raise a new one if
                          the plan has changed.
                        </p>
                      )}
                    </div>
                  )}
                </ItemCard>
              );
            })}
          </ItemList>
        )}
      </Card>

      <RequestForm
        open={formOpen}
        onClose={() => {
          setFormOpen(false);
          setEditing(null);
        }}
        editing={editing}
        onSaved={(saved) => {
          toast.success(
            saved.is_draft
              ? 'Draft saved — only you can see it'
              : editing
                ? 'Request updated'
                : 'Request submitted',
          );
          setFormOpen(false);
          setEditing(null);
          refresh();
        }}
      />

      {extending && (
        <ExtendTripModal
          key={extending.id}
          request={extending}
          me={me?.id}
          onClose={() => setExtending(null)}
          onExtended={() => {
            setExtending(null);
            refresh();
          }}
        />
      )}

      <Modal
        open={cancelling !== null}
        onClose={() => setCancelling(null)}
        title={
          cancelling?.cancel_needs_approval
            ? `Ask to cancel request ${cancelling.id}`
            : `Cancel request ${cancelling?.id ?? ''}`
        }
        description={
          cancelling?.cancel_needs_approval
            ? 'This trip is already approved or booked, so an admin or your manager has to agree. It stands until then.'
            : 'This cannot be undone. The reason is recorded in the activity log.'
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setCancelling(null)}>
              Keep it
            </Button>
            <Button
              variant="danger"
              loading={cancel.isPending}
              disabled={cancelReason.trim().length < 3}
              onClick={() => cancel.mutate()}
            >
              {cancelling?.cancel_needs_approval ? 'Send for approval' : 'Cancel request'}
            </Button>
          </>
        }
      >
        <Field label="Why?" htmlFor="cancel-reason" required>
          <Input
            id="cancel-reason"
            value={cancelReason}
            onChange={(e) => setCancelReason(e.target.value)}
            placeholder="Client moved the visit"
          />
        </Field>
      </Modal>
    </div>
  );
}
