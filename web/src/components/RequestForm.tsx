import { useMutation, useQuery } from '@tanstack/react-query';
import { AlertTriangle, BedDouble, Car, Plane, Search } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';

import { Modal } from '@/components/Modal';
import { DateTimePicker } from '@/components/DateTimePicker';
import { PlacePicker } from '@/components/PlacePicker';
import {
  NO_ROOM_PICK,
  RoomChoice,
  roomPayload,
  useRoomMatches,
  type RoomPick,
} from '@/components/RoomSharing';
import { Button, Field, Input, Select } from '@/components/ui';
import {
  checkRequest,
  createRequest,
  editRequest,
  errorMessage,
  fetchColleagues,
  fetchProjects,
  type RequestPayload,
} from '@/lib/api';
import { cn } from '@/lib/utils';
import {
  CAB_TYPE_CHOICES,
  DESIGNATION_LABELS,
  LOCAL_CAB_MAX_KM,
  PRIORITY_LABELS,
  PRIORITY_ORDER,
  REQUEST_TYPE_LABELS,
  TRAVEL_MODE_LABELS,
  type CabTrip,
  type CabType,
  type Project,
  type RequestConflict,
  type RequestPriority,
  type RequestType,
  type TravelMode,
  type TravelRequest,
} from '@/types';

const TYPE_ICON = { LONG_DISTANCE: Plane, LOCAL_CAB: Car, HOTEL: BedDouble } as const;

interface FormState {
  request_type: RequestType;
  project_id: string;
  other_project_name: string;
  travel_reason: string;
  priority: RequestPriority;
  traveller_ids: number[];
  mode: TravelMode;
  origin: string;
  origin_state: string;
  destination_state: string;
  hotel_state: string;
  destination: string;
  /** A cab's city or constituency, beside the street address. */
  pickup_city: string;
  drop_city: string;
  /** When true, cab stays with employee all day and drops back at pickup location (e.g. hotel) */
  drop_same_location: boolean;
  /** Most cab rides start and end in one city, so the drop's state and city
   *  follow the pickup's unless this is turned off. */
  drop_same_city: boolean;
  /** A cab's size, and whether it leaves town; outstation needs a distance.
   *  Kept as typed (a string) so a half-typed number is not lost. */
  cab_type: CabType;
  cab_trip: CabTrip;
  cab_distance_km: string;
  start_at: string;
  end_at: string;
  hotel_city: string;
  check_in: string;
  check_out: string;
  notes: string;
}

const BLANK: FormState = {
  request_type: 'LONG_DISTANCE',
  project_id: '',
  other_project_name: '',
  travel_reason: '',
  priority: 'MEDIUM',
  traveller_ids: [],
  mode: 'FLIGHT',
  origin: '',
  origin_state: '',
  destination_state: '',
  hotel_state: '',
  destination: '',
  pickup_city: '',
  drop_city: '',
  drop_same_location: true,
  drop_same_city: true,
  cab_type: 'NO_PREFERENCE',
  cab_trip: 'LOCAL',
  cab_distance_km: '',
  start_at: '',
  end_at: '',
  hotel_city: '',
  check_in: '',
  check_out: '',
  notes: '',
};

function fromRequest(request: TravelRequest): FormState {
  const sameLoc =
    request.request_type === 'LOCAL_CAB'
      ? !request.destination ||
        (request.destination.trim().toLowerCase() === (request.origin || '').trim().toLowerCase()) ||
        request.destination.toLowerCase().includes('same as pickup')
      : false;
  return {
    request_type: request.request_type,
    project_id: String(request.project_id),
    other_project_name: request.other_project_name ?? '',
    travel_reason: request.travel_reason ?? '',
    priority: request.priority ?? 'MEDIUM',
    traveller_ids: request.travellers
      .filter((t) => !t.is_requester)
      .map((t) => t.user_id),
    mode: request.mode ?? 'FLIGHT',
    origin: request.origin ?? '',
    origin_state: request.origin_state ?? '',
    destination_state: request.destination_state ?? '',
    hotel_state: request.hotel_state ?? '',
    destination: request.destination ?? '',
    pickup_city: request.pickup_city ?? '',
    drop_city: request.drop_city ?? '',
    drop_same_location: sameLoc,
    drop_same_city:
      !request.drop_city ||
      (request.drop_city === request.pickup_city &&
        request.destination_state === request.origin_state),
    cab_type: request.cab_type ?? 'NO_PREFERENCE',
    cab_trip: request.cab_trip ?? 'LOCAL',
    cab_distance_km: request.cab_distance_km ? String(request.cab_distance_km) : '',
    // <input type="datetime-local"> wants exactly "YYYY-MM-DDTHH:mm" and
    // silently shows nothing if handed the seconds the API returns.
    start_at: request.start_at ? request.start_at.slice(0, 16) : '',
    end_at: request.end_at ? request.end_at.slice(0, 16) : '',
    hotel_city: request.hotel_city ?? '',
    check_in: request.check_in ?? '',
    check_out: request.check_out ?? '',
    notes: request.notes ?? '',
  };
}

/** The form state as the API wants it: blanks become nulls, and the fields that
 *  do not belong to this request type are dropped rather than sent empty. */
function toPayload(form: FormState, isDraft: boolean): RequestPayload {
  const base = {
    request_type: form.request_type,
    project_id: Number(form.project_id),
    other_project_name: form.other_project_name.trim() || null,
    travel_reason: form.travel_reason.trim(),
    priority: form.priority,
    traveller_ids: form.traveller_ids,
    notes: form.notes || null,
    is_draft: isDraft,
  };

  if (form.request_type === 'HOTEL') {
    return {
      ...base,
      hotel_city: form.hotel_city || null,
      hotel_state: form.hotel_state || null,
      check_in: form.check_in || null,
      check_out: form.check_out || null,
    };
  }
  if (form.request_type === 'LOCAL_CAB') {
    const isSameLoc = form.drop_same_location;
    const drop = isSameLoc
      ? { state: form.origin_state, city: form.pickup_city }
      : dropPlace(form);
    const dropAddress = isSameLoc
      ? (form.origin || 'Same as pickup location')
      : form.destination;
    return {
      ...base,
      mode: 'CAB',
      origin: form.origin || null,
      origin_state: form.origin_state || null,
      pickup_city: form.pickup_city || null,
      destination: dropAddress || null,
      destination_state: drop.state || null,
      drop_city: drop.city || null,
      start_at: form.start_at || null,
      end_at: form.end_at || null,
      cab_type: form.cab_type,
      cab_trip: form.cab_trip,
      // Only outstation asks for a distance; a local cab sends none rather
      // than one left over from before the trip was switched to local.
      cab_distance_km:
        form.cab_trip === 'OUTSTATION' && form.cab_distance_km.trim()
          ? Number(form.cab_distance_km)
          : null,
    };
  }
  return {
    ...base,
    mode: form.mode,
    origin: form.origin || null,
    origin_state: form.origin_state || null,
    destination_state: form.destination_state || null,
    destination: form.destination || null,
    pickup_city: null,
    drop_city: null,
    start_at: form.start_at || null,
    end_at: form.end_at || null,
  };
}

/** Where a cab drops: the pickup's state and city, unless told otherwise. */
function dropPlace(form: FormState): { state: string; city: string } {
  return form.drop_same_city
    ? { state: form.origin_state, city: form.pickup_city }
    : { state: form.destination_state, city: form.drop_city };
}

/** Code of the seeded fallback campaign. Requests that pick "Other" point at
 *  it, so every join and report keeps working while the typed name is carried
 *  alongside for an admin to triage. */
const OTHER_CODE = 'OTHER';

/** The server flags it; the code check only covers a server that predates the
 *  flag. */
const isFallbackCampaign = (project: { code: string; is_fallback?: boolean }) =>
  project.is_fallback ?? project.code === OTHER_CODE;

/** Enough filled in for a conflict check to mean anything. */
function worthChecking(form: FormState): boolean {
  if (!form.project_id) return false;
  if (form.request_type === 'HOTEL') return Boolean(form.hotel_city && form.check_in);
  const isSameLoc = form.request_type === 'LOCAL_CAB' && form.drop_same_location;
  const destination = isSameLoc ? form.origin : form.destination;
  const route = Boolean(form.origin && destination && form.start_at);
  if (form.request_type !== 'LOCAL_CAB') return route;
  const drop = isSameLoc
    ? { state: form.origin_state, city: form.pickup_city }
    : dropPlace(form);
  // An outstation cab without its distance would be refused by the check, and
  // a refused check would hide any clash warning while the distance is typed.
  const distance =
    form.cab_trip !== 'OUTSTATION' || Number(form.cab_distance_km) >= LOCAL_CAB_MAX_KM;
  return (
    route &&
    distance &&
    Boolean(form.origin_state && form.pickup_city && drop.state && drop.city)
  );
}

export function ConflictList({
  conflicts,
  footnote = 'You can still submit this. An admin will see the clash and decide.',
}: {
  conflicts: RequestConflict[];
  /** The line under the warnings. A requester needs telling that the warning is
   *  not a refusal; an admin needs telling what overriding it costs. Pass null
   *  for neither. */
  footnote?: string | null;
}) {
  if (conflicts.length === 0) return null;
  return (
    <div className="rounded-md border border-warning/40 bg-warning-soft px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-warning">
        <AlertTriangle size={13} />
        {conflicts.length === 1 ? 'Possible clash' : `${conflicts.length} possible clashes`}
      </div>
      <ul className="mt-1.5 space-y-1">
        {conflicts.map((conflict, index) => (
          <li key={index} className="text-xs leading-relaxed text-text-muted">
            {conflict.message}
          </li>
        ))}
      </ul>
      {/* Addendum B6: warn, never block. Saying so on the screen stops the
          requester treating the warning as a refusal. */}
      {footnote && <p className="mt-2 text-2xs text-text-subtle">{footnote}</p>}
    </div>
  );
}

interface RequestFormProps {
  open: boolean;
  onClose: () => void;
  editing: TravelRequest | null;
  onSaved: (request: TravelRequest) => void;
}

export default function RequestForm({ open, onClose, editing, onSaved }: RequestFormProps) {
  const [form, setForm] = useState<FormState>(BLANK);
  const [error, setError] = useState<string | null>(null);
  const [peopleQuery, setPeopleQuery] = useState('');
  const errorRef = useRef<HTMLParagraphElement>(null);

  // The error box sits at the bottom of a long modal, so a failed save on a
  // phone would otherwise look like nothing happened.
  useEffect(() => {
    if (error) errorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [error]);

  const projects = useQuery({
    queryKey: ['projects', 'for-requests'],
    queryFn: () => fetchProjects({ status: 'ACTIVE', page_size: 200 }),
    enabled: open,
  });
  const colleagues = useQuery({
    queryKey: ['colleagues'],
    queryFn: fetchColleagues,
    enabled: open,
  });

  useEffect(() => {
    if (!open) return;
    setError(null);
    setForm(editing ? fromRequest(editing) : BLANK);
    setRoomPick(NO_ROOM_PICK);
  }, [open, editing]);

  // Default to the only campaign if there is one, so the common case is one
  // fewer decision on a phone.
  useEffect(() => {
    const list = projects.data?.items ?? [];
    const real = list.filter((p) => !isFallbackCampaign(p));
    if (!editing && !form.project_id && real.length === 1) {
      setForm((f) => (f.project_id ? f : { ...f, project_id: String(real[0].id) }));
    }
  }, [projects.data, editing, form.project_id]);

  const otherProject = (projects.data?.items ?? []).find(isFallbackCampaign);
  // Names are not unique, so two campaigns that would read the same in the
  // picker get their code added to tell them apart.
  const campaignLabels = useMemo(() => {
    const list = (projects.data?.items ?? []).filter((project) => !isFallbackCampaign(project));
    const label = (project: Project) =>
      project.state ? `${project.name} · ${project.state}` : project.name;
    const seen = new Map<string, number>();
    for (const project of list) seen.set(label(project), (seen.get(label(project)) ?? 0) + 1);
    return new Map(
      list.map((project) => [
        project.id,
        (seen.get(label(project)) ?? 0) > 1 ? `${label(project)} (${project.code})` : label(project),
      ]),
    );
  }, [projects.data]);
  const isOther = Boolean(otherProject && form.project_id === String(otherProject.id));

  const payload = useMemo(() => toPayload(form, false), [form]);

  // Check as the form is typed, debounced, so the warning appears while there
  // is still time to act on it. Failures are swallowed: a dry run that cannot
  // reach the server must not look like a validation error.
  const [conflicts, setConflicts] = useState<RequestConflict[]>([]);

  useEffect(() => {
    if (!open || !worthChecking(form)) {
      setConflicts([]);
      return;
    }
    const handle = setTimeout(() => {
      checkRequest({ ...payload, request_id: editing?.id })
        .then((result) => setConflicts(result.conflicts))
        .catch(() => setConflicts([]));
    }, 400);
    return () => clearTimeout(handle);
  }, [open, payload, editing?.id, form]);

  // Who of the requester's gender is staying in the hotel's city - offered from
  // the moment the city is picked, before any dates.
  const [roomPick, setRoomPick] = useState<RoomPick>(NO_ROOM_PICK);
  const roomMatches = useRoomMatches({
    enabled: open && form.request_type === 'HOTEL',
    city: form.hotel_city,
    checkIn: form.check_in,
    checkOut: form.check_out,
    requestId: editing?.id,
  });
  const staying = form.request_type === 'HOTEL' && form.hotel_city ? (roomMatches.data ?? []) : [];

  const save = useMutation({
    mutationFn: (asDraft: boolean) => {
      const body = toPayload(form, asDraft);
      if (editing) return editRequest(editing.id, body);
      return createRequest(
        body.request_type === 'HOTEL' ? { ...body, ...roomPayload(roomPick, staying) } : body,
      );
    },
    onSuccess: onSaved,
    // The global toast and the inline box say the same thing.
    onError: (err) => setError(errorMessage(err, 'Could not save this request.')),
    meta: { errorFallback: 'Could not save this request.' },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    save.mutate(false);
  };

  const isHotel = form.request_type === 'HOTEL';
  const isCab = form.request_type === 'LOCAL_CAB';
  const people = colleagues.data ?? [];

  const visiblePeople = people.filter((person) => {
    // Already-selected people are never filtered out: hiding someone you just
    // ticked while you search for the next one reads as having lost them.
    if (form.traveller_ids.includes(person.id)) return true;
    const needle = peopleQuery.trim().toLowerCase();
    if (!needle) return true;
    return [person.full_name, person.designation]
      .filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(needle));
  });

  const toggleTraveller = (id: number) =>
    setForm((f) => ({
      ...f,
      traveller_ids: f.traveller_ids.includes(id)
        ? f.traveller_ids.filter((x) => x !== id)
        : [...f.traveller_ids, id],
    }));

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? `Edit request ${editing.id}` : 'New request'}
      description={
        editing
          ? 'Every change is recorded and shown to the admin before they decide.'
          : 'Tag anyone travelling with you — each person is approved separately.'
      }
      className="sm:max-w-2xl"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          {/* A draft stays private until it is submitted, so it is offered only
              while creating - un-submitting an existing request is not a flow. */}
          {!editing && (
            <Button
              variant="secondary"
              loading={save.isPending && save.variables === true}
              onClick={() => {
                setError(null);
                save.mutate(true);
              }}
            >
              Save draft
            </Button>
          )}
          <Button
            form="request-form"
            type="submit"
            loading={save.isPending && save.variables === false}
          >
            {editing ? 'Save changes' : 'Submit request'}
          </Button>
        </>
      }
    >
      <form id="request-form" onSubmit={submit} className="space-y-4" noValidate>
        <div className="grid grid-cols-3 gap-2">
          {(Object.keys(REQUEST_TYPE_LABELS) as RequestType[]).map((type) => {
            const Icon = TYPE_ICON[type];
            const active = form.request_type === type;
            return (
              <button
                key={type}
                type="button"
                onClick={() => setForm({ ...form, request_type: type })}
                aria-pressed={active}
                className={cn(
                  'flex flex-col items-center gap-1.5 rounded-md border px-2 py-3 text-xs transition-colors',
                  active
                    ? 'border-primary bg-surface-sunken font-medium text-text'
                    : 'border-border text-text-muted hover:border-border-strong hover:text-text',
                )}
              >
                <Icon size={17} />
                <span className="text-center leading-tight">{REQUEST_TYPE_LABELS[type]}</span>
              </button>
            );
          })}
        </div>

        <Field label="Campaign" htmlFor="project_id" required>
          <Select
            id="project_id"
            required
            value={form.project_id}
            onChange={(e) => setForm({ ...form, project_id: e.target.value })}
          >
            <option value="">Choose a campaign</option>
            {(projects.data?.items ?? [])
              .filter((project) => !isFallbackCampaign(project))
              .map((project) => (
                // The name leads: staff should never need to decode a code.
                <option key={project.id} value={project.id}>
                  {campaignLabels.get(project.id)}
                </option>
              ))}
            {otherProject && (
              <option value={otherProject.id}>Other — type the campaign name</option>
            )}
          </Select>
        </Field>

        {/* Shown only when "Other" is chosen. The request still points at the
            fallback campaign so reporting keeps working; this carries what they
            actually meant, for an admin to triage. */}
        {isOther && (
          <Field
            label="What is the campaign called?"
            htmlFor="other_project_name"
            required
            hint="An admin will create it properly and move your request onto it."
          >
            <Input
              id="other_project_name"
              required
              value={form.other_project_name}
              onChange={(e) => setForm({ ...form, other_project_name: e.target.value })}
              placeholder="Diwali retail audit, Pune"
            />
          </Field>
        )}

        <Field
          label="Reason for travel"
          htmlFor="travel_reason"
          required
          hint="The admin deciding on this needs to know what the trip is for."
        >
          <textarea
            id="travel_reason"
            required
            rows={2}
            maxLength={500}
            value={form.travel_reason}
            onChange={(e) => setForm({ ...form, travel_reason: e.target.value })}
            placeholder="Store audit at 12 outlets; client walkthrough on the 14th."
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text transition-colors placeholder:text-text-subtle hover:border-border-strong"
          />
        </Field>

        <Field
          label="Priority"
          required
          hint="How soon do you need a decision? Admins see high-priority requests first."
        >
          <div className="grid grid-cols-3 gap-2" role="group" aria-label="Priority">
            {PRIORITY_ORDER.map((priority) => {
              const active = form.priority === priority;
              return (
                <button
                  key={priority}
                  type="button"
                  onClick={() => setForm({ ...form, priority })}
                  aria-pressed={active}
                  className={cn(
                    'rounded-md border px-2 py-2 text-xs transition-colors',
                    active
                      ? priority === 'HIGH'
                        ? 'border-danger bg-danger-soft font-medium text-danger'
                        : 'border-primary bg-surface-sunken font-medium text-text'
                      : 'border-border text-text-muted hover:border-border-strong hover:text-text',
                  )}
                >
                  {PRIORITY_LABELS[priority]}
                </button>
              );
            })}
          </div>
        </Field>

        {isHotel ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <PlacePicker
              label="Hotel"
              id="hotel"
              required
              className="sm:col-span-2"
              state={form.hotel_state}
              city={form.hotel_city}
              hint="Colleagues of your gender staying in this city are shown below, to share a room."
              onChange={({ state, city }) =>
                setForm({ ...form, hotel_state: state, hotel_city: city })
              }
            />
            <Field label="Check in" htmlFor="check_in" required>
              <Input
                id="check_in"
                type="date"
                required
                value={form.check_in}
                onChange={(e) => setForm({ ...form, check_in: e.target.value })}
              />
            </Field>
            <Field label="Check out" htmlFor="check_out" hint="Counted by night.">
              <Input
                id="check_out"
                type="date"
                value={form.check_out}
                onChange={(e) => setForm({ ...form, check_out: e.target.value })}
              />
            </Field>
            <RoomChoice
              city={form.hotel_city}
              hasDates={Boolean(form.check_in)}
              editing={Boolean(editing)}
              matches={staying}
              pick={roomPick}
              onPick={setRoomPick}
            />
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {!isCab && (
              <Field label="Mode" htmlFor="mode" className="sm:col-span-2">
                <Select
                  id="mode"
                  value={form.mode}
                  onChange={(e) => setForm({ ...form, mode: e.target.value as TravelMode })}
                >
                  {(['FLIGHT', 'TRAIN', 'BUS'] as TravelMode[]).map((mode) => (
                    <option key={mode} value={mode}>
                      {TRAVEL_MODE_LABELS[mode]}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            {/* A cab's pickup and drop are street addresses - "Banjara Hills"
                is not something a state picker can offer - so those stay free
                text. The state and city each one is in are picked from the
                same list as a flight's, so cabs can be counted by place. */}
            {isCab ? (
              <>
                <PlacePicker
                  key="pickup"
                  label="Pickup"
                  id="pickup"
                  required
                  className="sm:col-span-2"
                  state={form.origin_state}
                  city={form.pickup_city}
                  onChange={({ state, city }) =>
                    setForm({ ...form, origin_state: state, pickup_city: city })
                  }
                />
                <Field
                  label="Pickup address"
                  htmlFor="origin"
                  required
                  className="sm:col-span-2"
                >
                  <Input
                    id="origin"
                    required
                    maxLength={160}
                    value={form.origin}
                    onChange={(e) => setForm({ ...form, origin: e.target.value })}
                    placeholder="Road No. 12, Banjara Hills"
                  />
                </Field>
                <div className="sm:col-span-2 rounded-lg border border-border bg-surface-sunken p-3">
                  <label className="flex cursor-pointer items-start gap-2.5">
                    <input
                      type="checkbox"
                      checked={form.drop_same_location}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          drop_same_location: e.target.checked,
                          destination: e.target.checked ? form.origin : form.destination,
                          destination_state: e.target.checked ? form.origin_state : form.destination_state,
                          drop_city: e.target.checked ? form.pickup_city : form.drop_city,
                        })
                      }
                      className="mt-0.5 h-4 w-4 accent-[rgb(var(--primary))]"
                    />
                    <div className="text-xs">
                      <span className="font-semibold text-text block">Drop is at the same location (returns to hotel / pickup)</span>
                      <span className="text-text-muted block mt-0.5">
                        Cab stays with the employee on-ground for the day and drops back at the pickup hotel/location.
                      </span>
                    </div>
                  </label>
                </div>

                {!form.drop_same_location && (
                  <>
                    <label className="flex cursor-pointer items-center gap-2.5 text-xs font-medium text-text sm:col-span-2">
                      <input
                        type="checkbox"
                        checked={form.drop_same_city}
                        onChange={(e) =>
                          setForm({
                            ...form,
                            drop_same_city: e.target.checked,
                            // Start the separate drop from the pickup's place
                            ...(!e.target.checked && !form.drop_city
                              ? { destination_state: form.origin_state, drop_city: '' }
                              : {}),
                          })
                        }
                        className="h-4 w-4 accent-[rgb(var(--primary))]"
                      />
                      Drop is in the same city
                    </label>

                    {!form.drop_same_city && (
                      <PlacePicker
                        key="drop"
                        label="Drop"
                        id="drop"
                        required
                        className="sm:col-span-2"
                        state={form.destination_state}
                        city={form.drop_city}
                        onChange={({ state, city }) =>
                          setForm({ ...form, destination_state: state, drop_city: city })
                        }
                      />
                    )}

                    <Field label="Different drop address" htmlFor="destination" required className="sm:col-span-2" hint="E.g. Airport, Client Office, or Railway Station">
                      <Input
                        id="destination"
                        required
                        maxLength={160}
                        value={form.destination}
                        onChange={(e) => setForm({ ...form, destination: e.target.value })}
                        placeholder="RGIA Airport, Terminal 1"
                      />
                    </Field>
                  </>
                )}

                <Field
                  label="Cab type"
                  className="sm:col-span-2"
                  hint="The admin books the closest match the vendor has."
                >
                  <div className="grid grid-cols-3 gap-2" role="group" aria-label="Cab type">
                    {CAB_TYPE_CHOICES.map((choice) => {
                      const active = form.cab_type === choice.value;
                      return (
                        <button
                          key={choice.value}
                          type="button"
                          aria-pressed={active}
                          onClick={() => setForm({ ...form, cab_type: choice.value })}
                          className={cn(
                            'flex flex-col items-center gap-0.5 rounded-md border px-2 py-2.5 text-center transition-colors',
                            active
                              ? 'border-primary bg-surface-sunken text-text'
                              : 'border-border text-text-muted hover:border-border-strong hover:text-text',
                          )}
                        >
                          <span className={cn('text-xs', active && 'font-medium')}>
                            {choice.title}
                          </span>
                          <span className="text-2xs text-text-subtle">{choice.detail}</span>
                        </button>
                      );
                    })}
                  </div>
                </Field>

                <Field label="Trip" className="sm:col-span-2">
                  <div className="grid grid-cols-2 gap-2" role="group" aria-label="Trip">
                    {(
                      [
                        ['LOCAL', 'Local', `Within ${LOCAL_CAB_MAX_KM} km`],
                        ['OUTSTATION', 'Outstation', `${LOCAL_CAB_MAX_KM} km or more`],
                      ] as const
                    ).map(([trip, title, detail]) => {
                      const active = form.cab_trip === trip;
                      return (
                        <button
                          key={trip}
                          type="button"
                          aria-pressed={active}
                          onClick={() => setForm({ ...form, cab_trip: trip })}
                          className={cn(
                            'flex flex-col items-center gap-0.5 rounded-md border px-2 py-2.5 text-center transition-colors',
                            active
                              ? 'border-primary bg-surface-sunken text-text'
                              : 'border-border text-text-muted hover:border-border-strong hover:text-text',
                          )}
                        >
                          <span className={cn('text-xs', active && 'font-medium')}>{title}</span>
                          <span className="text-2xs text-text-subtle">{detail}</span>
                        </button>
                      );
                    })}
                  </div>
                </Field>

                {form.cab_trip === 'OUTSTATION' && (
                  <Field
                    label="Approximate distance (km)"
                    htmlFor="cab_distance_km"
                    required
                    className="sm:col-span-2"
                    hint={`One way, as a map app shows it. Under ${LOCAL_CAB_MAX_KM} km is a local trip.`}
                  >
                    <Input
                      id="cab_distance_km"
                      type="number"
                      inputMode="numeric"
                      required
                      min={LOCAL_CAB_MAX_KM}
                      max={5000}
                      step={1}
                      value={form.cab_distance_km}
                      onChange={(e) => setForm({ ...form, cab_distance_km: e.target.value })}
                      placeholder="250"
                      className="sm:max-w-40"
                    />
                  </Field>
                )}
              </>
            ) : (
              <>
                <PlacePicker
                  key="from"
                  label="From"
                  id="origin"
                  required
                  className="sm:col-span-2"
                  state={form.origin_state}
                  city={form.origin}
                  onChange={({ state, city }) =>
                    setForm({ ...form, origin_state: state, origin: city })
                  }
                />
                <PlacePicker
                  key="to"
                  label="To"
                  id="destination"
                  required
                  className="sm:col-span-2"
                  state={form.destination_state}
                  city={form.destination}
                  onChange={({ state, city }) =>
                    setForm({ ...form, destination_state: state, destination: city })
                  }
                />
              </>
            )}
            <Field label={isCab ? 'Pickup date & time' : 'Departs (date & time)'} htmlFor="start_at" required>
              <DateTimePicker
                id="start_at"
                required
                value={form.start_at}
                onChange={(val) => setForm({ ...form, start_at: val })}
              />
            </Field>
            <Field
              label={isCab ? 'Cab needed until (date & time)' : 'Arrives (date & time)'}
              htmlFor="end_at"
              hint={
                isCab
                  ? 'Optional. If the work runs over after it is booked, you can ask for one more day.'
                  : 'Optional estimated arrival date & time.'
              }
            >
              <DateTimePicker
                id="end_at"
                minDate={form.start_at ? form.start_at.slice(0, 10) : undefined}
                value={form.end_at}
                onChange={(val) => setForm({ ...form, end_at: val })}
              />
            </Field>
          </div>
        )}

        <Field
          label="Travelling with"
          hint="Each person is approved separately. You are always on your own request."
        >
          {/* With a hundred ground staff, an unfiltered checkbox list is a
              scroll hunt. Anyone already ticked stays visible regardless of the
              filter, so searching for a second person cannot hide the first. */}
          <div className="relative mb-1.5">
            <Search
              size={13}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-subtle"
            />
            <Input
              value={peopleQuery}
              onChange={(e) => setPeopleQuery(e.target.value)}
              placeholder="Search by name"
              aria-label="Search colleagues"
              className="h-8 pl-7 text-xs"
            />
          </div>

          <div className="max-h-44 overflow-y-auto rounded-md border border-border">
            {visiblePeople.length === 0 ? (
              <p className="px-3 py-3 text-xs text-text-subtle">
                {peopleQuery ? `Nobody matches “${peopleQuery}”.` : 'No colleagues to tag.'}
              </p>
            ) : (
              visiblePeople.map((person) => (
                <label
                  key={person.id}
                  className="flex cursor-pointer items-center gap-2.5 border-b border-border px-3 py-2 last:border-0 hover:bg-surface-sunken"
                >
                  <input
                    type="checkbox"
                    checked={form.traveller_ids.includes(person.id)}
                    onChange={() => toggleTraveller(person.id)}
                    className="h-3.5 w-3.5 accent-[rgb(var(--primary))]"
                  />
                  <span className="text-sm">{person.full_name}</span>
                  {person.designation && (
                    <span className="ml-auto text-2xs text-text-subtle">
                      {DESIGNATION_LABELS[person.designation]}
                    </span>
                  )}
                </label>
              ))
            )}
          </div>
        </Field>

        <Field label="Notes" htmlFor="notes">
          <textarea
            id="notes"
            rows={2}
            value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
            placeholder="Anything the admin should know"
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-subtle hover:border-border-strong"
          />
        </Field>

        <ConflictList conflicts={conflicts} />

        {editing && !editing.is_draft && (
          <p className="text-2xs text-text-subtle">
            Saving writes revision {editing.edit_count + 2} to this request&rsquo;s history.
          </p>
        )}

        {error && (
          <p
            ref={errorRef}
            role="alert"
            className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger"
          >
            {error}
          </p>
        )}
      </form>
    </Modal>
  );
}
