import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarRange, Pencil, Plane, UserMinus, UserPlus, Users as UsersIcon } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import toast from 'react-hot-toast';

import { Modal } from '@/components/Modal';
import { PlacePicker } from '@/components/PlacePicker';
import { CHANGE_KIND, ChangeDetails } from '@/components/TeamChangesPanel';
import { TravelHistoryPanel } from '@/components/TravelHistoryPanel';
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
  PageHeader,
  Select,
  Skeleton,
  ZEBRA_ROWS,
} from '@/components/ui';
import {
  askToAddMember,
  askToEditMember,
  askToRemoveMember,
  errorMessage,
  fetchMyTeam,
  fetchRequests,
  fetchTeamChanges,
  withdrawTeamChange,
  type TeamAddPayload,
} from '@/lib/api';
import { routeLabel } from '@/lib/places';
import { formatInstantDate, todayInIndia } from '@/lib/time';
import { cn, MOBILE_HINT, mobileDigits } from '@/lib/utils';
import {
  DESIGNATION_LABELS,
  GENDER_LABELS,
  REQUEST_STATUS_LABELS,
  REQUEST_TYPE_LABELS,
  SELECTABLE_GENDERS,
  USER_STATUS_LABELS,
  type Designation,
  type TeamChange,
  type TeamChangeStatus,
  type TravelRequest,
  type UserRow,
} from '@/types';

const TEXTAREA =
  'w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text transition-colors placeholder:text-text-subtle hover:border-border-strong';

const BLANK_ADD: TeamAddPayload = {
  email: '',
  full_name: '',
  gender: '',
  designation: 'EXECUTIVE',
  phone: '',
  employee_code: '',
  base_state: '',
  base_location: '',
  note: '',
};

interface EditDraft {
  full_name: string;
  designation: string;
  phone: string;
  employee_code: string;
  base_state: string;
  base_location: string;
  note: string;
}

const STATUS_TONE: Record<TeamChangeStatus, 'warning' | 'success' | 'danger' | 'neutral'> = {
  PENDING: 'warning',
  APPROVED: 'success',
  REJECTED: 'danger',
  CANCELLED: 'neutral',
};

const STATUS_LABEL: Record<TeamChangeStatus, string> = {
  PENDING: 'Waiting for an admin',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
  CANCELLED: 'Withdrawn',
};

function where(trip: TravelRequest): string {
  return trip.request_type === 'HOTEL' ? (trip.hotel_city ?? '') : routeLabel(trip);
}

function when(trip: TravelRequest): string {
  return trip.request_type === 'HOTEL'
    ? formatInstantDate(trip.check_in)
    : formatInstantDate(trip.start_at);
}

/**
 * A manager's view of the people who report to them: their details, their
 * trips and travel history (never what anything cost), and the changes the
 * manager has asked an admin to make. Nothing here changes an account
 * directly - every add, edit and removal waits for an admin's approval.
 */
export default function MyTeamPage() {
  const queryClient = useQueryClient();
  const members = useQuery({ queryKey: ['my-team'], queryFn: fetchMyTeam });
  const changes = useQuery({ queryKey: ['team-changes', 'mine'], queryFn: () => fetchTeamChanges() });
  const trips = useQuery({
    queryKey: ['requests', 'team'],
    queryFn: () => fetchRequests({ mine: false, page_size: 100 }),
  });

  const [adding, setAdding] = useState(false);
  const [addForm, setAddForm] = useState<TeamAddPayload>(BLANK_ADD);
  const [editing, setEditing] = useState<UserRow | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft | null>(null);
  const [removing, setRemoving] = useState<UserRow | null>(null);
  const [removeStatus, setRemoveStatus] = useState<'LEFT' | 'DEACTIVATED'>('LEFT');
  const [removeDate, setRemoveDate] = useState('');
  const [removeReason, setRemoveReason] = useState('');
  const [historyFor, setHistoryFor] = useState<UserRow | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['team-changes'] });
    queryClient.invalidateQueries({ queryKey: ['my-team'] });
  };
  const asked = () => {
    toast.success('Sent to an admin for approval');
    refresh();
  };

  const add = useMutation({
    mutationFn: () =>
      askToAddMember({
        ...addForm,
        email: addForm.email.trim().toLowerCase(),
        phone: addForm.phone?.trim() || null,
        employee_code: addForm.employee_code?.trim() || null,
        base_state: addForm.base_state || null,
        base_location: addForm.base_location || null,
        note: addForm.note?.trim() || null,
      }),
    onSuccess: () => {
      setAdding(false);
      setAddForm(BLANK_ADD);
      asked();
    },
    onError: (err) => setFormError(errorMessage(err, 'Could not send that request.')),
  });

  const edit = useMutation({
    mutationFn: () => {
      const d = editDraft!;
      return askToEditMember(editing!.id, {
        full_name: d.full_name.trim(),
        designation: d.designation || null,
        phone: d.phone.trim() || null,
        employee_code: d.employee_code.trim() || null,
        base_state: d.base_state || null,
        base_location: d.base_location || null,
        note: d.note.trim() || null,
      });
    },
    onSuccess: () => {
      setEditing(null);
      asked();
    },
    onError: (err) => setFormError(errorMessage(err, 'Could not send that request.')),
  });

  const remove = useMutation({
    mutationFn: () =>
      askToRemoveMember(removing!.id, {
        status: removeStatus,
        exited_on: removeStatus === 'LEFT' && removeDate ? removeDate : null,
        reason: removeReason.trim(),
      }),
    onSuccess: () => {
      setRemoving(null);
      asked();
    },
    onError: (err) => setFormError(errorMessage(err, 'Could not send that request.')),
  });

  const withdraw = useMutation({
    mutationFn: (change: TeamChange) => withdrawTeamChange(change.id),
    onSuccess: () => {
      toast.success('Request withdrawn');
      refresh();
    },
  });

  const openEdit = (member: UserRow) => {
    setEditing(member);
    setEditDraft({
      full_name: member.full_name,
      designation: member.designation ?? '',
      phone: member.phone ?? '',
      employee_code: member.employee_code ?? '',
      base_state: member.base_state ?? '',
      base_location: member.base_location ?? '',
      note: '',
    });
    setFormError(null);
  };

  const openRemove = (member: UserRow) => {
    setRemoving(member);
    setRemoveStatus('LEFT');
    setRemoveDate('');
    setRemoveReason('');
    setFormError(null);
  };

  const rows = members.data ?? [];
  const asks = changes.data?.items ?? [];
  // Who already has an ask waiting: one at a time per person.
  const waitingOn = new Set(
    asks.filter((c) => c.status === 'PENDING' && c.target_user_id).map((c) => c.target_user_id),
  );
  const teamTrips = (trips.data?.items ?? []).filter((t) => !t.is_draft);

  const submitAdd = (event: FormEvent) => {
    event.preventDefault();
    if (!(SELECTABLE_GENDERS as readonly string[]).includes(addForm.gender)) {
      setFormError('Choose Male or Female.');
      return;
    }
    setFormError(null);
    add.mutate();
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="My team"
        description="The people who report to you. Ask to add, edit or remove someone and an admin approves it - you are told the decision either way. Costs are never shown here."
        actions={
          <Button
            onClick={() => {
              setAdding(true);
              setFormError(null);
            }}
          >
            <UserPlus size={15} />
            Ask to add someone
          </Button>
        }
      />

      <Card>
        <CardHeader title={`${rows.length} ${rows.length === 1 ? 'person' : 'people'}`} />
        {members.isPending ? (
          <div className="space-y-2 p-5">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : members.isError ? (
          <EmptyState
            icon={<UsersIcon size={28} />}
            title="Could not load your team"
            description={errorMessage(members.error)}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<UsersIcon size={28} />}
            title="Nobody reports to you yet"
            description="Ask to add someone, or ask an admin to set you as their manager on Team."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-2xs uppercase tracking-widest text-text-subtle">
                  <th className="px-5 py-2.5 font-semibold">Name</th>
                  <th className="hidden px-5 py-2.5 font-semibold md:table-cell">Designation</th>
                  <th className="hidden px-5 py-2.5 font-semibold lg:table-cell">Base</th>
                  <th className="hidden px-5 py-2.5 font-semibold sm:table-cell">Phone</th>
                  <th className="px-5 py-2.5 font-semibold">Status</th>
                  <th className="px-5 py-2.5 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                {rows.map((member) => {
                  const waiting = waitingOn.has(member.id);
                  return (
                    <tr key={member.id} className="transition-colors hover:bg-surface-sunken/60">
                      <td className="px-5 py-3">
                        <div className="font-medium">{member.full_name}</div>
                        <div className="text-xs text-text-muted">{member.email}</div>
                      </td>
                      <td className="hidden px-5 py-3 text-text-muted md:table-cell">
                        {member.designation ? DESIGNATION_LABELS[member.designation] : '—'}
                      </td>
                      <td className="hidden px-5 py-3 text-text-muted lg:table-cell">
                        {[member.base_location, member.base_state].filter(Boolean).join(', ') || '—'}
                      </td>
                      <td className="hidden px-5 py-3 text-text-muted sm:table-cell">
                        {member.phone || '—'}
                      </td>
                      <td className="px-5 py-3">
                        <Badge tone={member.status === 'ACTIVE' ? 'success' : 'neutral'}>
                          {member.status === 'ACTIVE' && !member.has_password
                            ? 'Invited'
                            : USER_STATUS_LABELS[member.status]}
                        </Badge>
                        {waiting && <div className="mt-1 text-2xs text-warning">Change waiting</div>}
                      </td>
                      <td className="px-5 py-3">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            title="Travel history"
                            onClick={() => setHistoryFor(member)}
                          >
                            <CalendarRange size={14} />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            title={waiting ? 'A change for them is already waiting' : 'Ask to edit details'}
                            disabled={waiting}
                            onClick={() => openEdit(member)}
                          >
                            <Pencil size={14} />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            title={waiting ? 'A change for them is already waiting' : 'Ask to remove'}
                            disabled={waiting || member.status !== 'ACTIVE'}
                            onClick={() => openRemove(member)}
                          >
                            <UserMinus size={14} />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Your requests to admins"
          description="Withdraw one any time before an admin decides it."
        />
        {asks.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-text-muted">Nothing asked yet.</p>
        ) : (
          <ItemList className="rounded-b-xl">
            {asks.map((change) => (
              <ItemCard
                key={change.id}
                accent={STATUS_TONE[change.status]}
                className="flex flex-wrap items-start gap-3"
              >
                {/* At least 10rem: on a phone the status and Withdraw drop
                    below instead of squeezing the ask beside them. */}
                <div className="min-w-0 flex-1 basis-40 space-y-1">
                  <p className="text-sm">
                    <ItemNumber value={change.id} className="mr-1.5 align-middle" />
                    <Badge tone={CHANGE_KIND[change.kind].tone} className="mr-1.5 align-middle">
                      {CHANGE_KIND[change.kind].label}
                    </Badge>
                    <span className="font-medium">{change.target_name}</span>
                    <span className="ml-2 text-2xs text-text-subtle">
                      {formatInstantDate(change.created_at)}
                    </span>
                  </p>
                  <ChangeDetails change={change} />
                  {change.decision_comment && (
                    <p className="text-xs">
                      <span className="font-medium">{change.decided_by_name}:</span>{' '}
                      <span className="text-text-muted">{change.decision_comment}</span>
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone={STATUS_TONE[change.status]}>{STATUS_LABEL[change.status]}</Badge>
                  {change.status === 'PENDING' && (
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={withdraw.isPending && withdraw.variables?.id === change.id}
                      onClick={() => withdraw.mutate(change)}
                    >
                      Withdraw
                    </Button>
                  )}
                </div>
              </ItemCard>
            ))}
          </ItemList>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Team trips"
          description="Requests your team raised or travel on, newest first."
        />
        {trips.isPending ? (
          <div className="p-5">
            <Skeleton className="h-10 w-full" />
          </div>
        ) : teamTrips.length === 0 ? (
          <EmptyState icon={<Plane size={28} />} title="No trips yet" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-2xs uppercase tracking-widest text-text-subtle">
                  <th className="px-5 py-2.5 font-semibold">Request</th>
                  <th className="px-5 py-2.5 font-semibold">Where</th>
                  <th className="hidden px-5 py-2.5 font-semibold md:table-cell">When</th>
                  <th className="hidden px-5 py-2.5 font-semibold lg:table-cell">Who</th>
                  <th className="px-5 py-2.5 font-semibold">Status</th>
                </tr>
              </thead>
              <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                {teamTrips.map((trip) => (
                  <tr key={trip.id}>
                    <td className="px-5 py-3">
                      <div className="flex flex-wrap items-center gap-1.5 font-medium">
                        <ItemNumber value={trip.id} />
                        {REQUEST_TYPE_LABELS[trip.request_type]}
                      </div>
                      <div className="text-xs text-text-muted">{trip.project_name}</div>
                    </td>
                    <td className="px-5 py-3 text-text-muted">{where(trip)}</td>
                    <td className="hidden px-5 py-3 text-text-muted md:table-cell">{when(trip)}</td>
                    <td className="hidden px-5 py-3 text-text-muted lg:table-cell">
                      {trip.travellers.map((t) => t.full_name).join(', ')}
                    </td>
                    <td className="px-5 py-3">
                      <Badge>{REQUEST_STATUS_LABELS[trip.status]}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Ask to add */}
      <Modal
        open={adding}
        onClose={() => setAdding(false)}
        title="Ask to add someone"
        description="Once an admin approves, they are invited as ground staff in your department, reporting to you."
        footer={
          <>
            <Button variant="secondary" onClick={() => setAdding(false)}>
              Cancel
            </Button>
            <Button type="submit" form="team-add-form" loading={add.isPending}>
              Send for approval
            </Button>
          </>
        }
      >
        <form id="team-add-form" onSubmit={submitAdd} className="grid gap-4 sm:grid-cols-2" noValidate>
          <Field label="Full name" htmlFor="add_name" required className="sm:col-span-2">
            <Input
              id="add_name"
              value={addForm.full_name}
              onChange={(e) => setAddForm({ ...addForm, full_name: e.target.value })}
            />
          </Field>
          <Field label="Work email" htmlFor="add_email" required className="sm:col-span-2">
            <Input
              id="add_email"
              type="email"
              value={addForm.email}
              onChange={(e) => setAddForm({ ...addForm, email: e.target.value })}
            />
          </Field>
          <Field label="Gender" htmlFor="add_gender" required hint="Decides who may share a room.">
            <Select
              id="add_gender"
              value={addForm.gender}
              onChange={(e) => setAddForm({ ...addForm, gender: e.target.value })}
            >
              <option value="" disabled>
                Choose…
              </option>
              {SELECTABLE_GENDERS.map((g) => (
                <option key={g} value={g}>
                  {GENDER_LABELS[g]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Designation" htmlFor="add_designation">
            <Select
              id="add_designation"
              value={addForm.designation ?? ''}
              onChange={(e) => setAddForm({ ...addForm, designation: e.target.value || null })}
            >
              <option value="">Not set</option>
              {(Object.keys(DESIGNATION_LABELS) as Designation[]).map((d) => (
                <option key={d} value={d}>
                  {DESIGNATION_LABELS[d]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Phone" htmlFor="add_phone" hint={MOBILE_HINT}>
            <Input
              id="add_phone"
              value={addForm.phone ?? ''}
              onChange={(e) => setAddForm({ ...addForm, phone: mobileDigits(e.target.value) })}
              placeholder="9876543210"
              inputMode="numeric"
            />
          </Field>
          <Field label="Employee code" htmlFor="add_code">
            <Input
              id="add_code"
              value={addForm.employee_code ?? ''}
              onChange={(e) => setAddForm({ ...addForm, employee_code: e.target.value })}
            />
          </Field>
          <PlacePicker
            label="Base"
            id="add_base"
            className="sm:col-span-2"
            state={addForm.base_state ?? ''}
            city={addForm.base_location ?? ''}
            onChange={({ state, city }) => setAddForm({ ...addForm, base_state: state, base_location: city })}
          />
          <Field label="Note for the admin" htmlFor="add_note" className="sm:col-span-2">
            <textarea
              id="add_note"
              rows={2}
              maxLength={500}
              className={TEXTAREA}
              value={addForm.note ?? ''}
              onChange={(e) => setAddForm({ ...addForm, note: e.target.value })}
              placeholder="Joining the Pune survey from Monday"
            />
          </Field>
          {formError && <p className="text-xs text-danger sm:col-span-2">{formError}</p>}
        </form>
      </Modal>

      {/* Ask to edit */}
      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing ? `Ask to update ${editing.full_name}` : ''}
        description="Only what you change is sent. An admin approves it before it is saved."
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button loading={edit.isPending} onClick={() => edit.mutate()}>
              Send for approval
            </Button>
          </>
        }
      >
        {editDraft && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Full name" htmlFor="edit_member_name" className="sm:col-span-2">
              <Input
                id="edit_member_name"
                value={editDraft.full_name}
                onChange={(e) => setEditDraft({ ...editDraft, full_name: e.target.value })}
              />
            </Field>
            <Field label="Designation" htmlFor="edit_member_designation">
              <Select
                id="edit_member_designation"
                value={editDraft.designation}
                onChange={(e) => setEditDraft({ ...editDraft, designation: e.target.value })}
              >
                <option value="">Not set</option>
                {(Object.keys(DESIGNATION_LABELS) as Designation[]).map((d) => (
                  <option key={d} value={d}>
                    {DESIGNATION_LABELS[d]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Phone" htmlFor="edit_member_phone" hint={MOBILE_HINT}>
              <Input
                id="edit_member_phone"
                value={editDraft.phone}
                onChange={(e) => setEditDraft({ ...editDraft, phone: mobileDigits(e.target.value) })}
              />
            </Field>
            <Field label="Employee code" htmlFor="edit_member_code">
              <Input
                id="edit_member_code"
                value={editDraft.employee_code}
                onChange={(e) => setEditDraft({ ...editDraft, employee_code: e.target.value })}
              />
            </Field>
            <PlacePicker
              label="Base"
              id="edit_member_base"
              className="sm:col-span-2"
              state={editDraft.base_state}
              city={editDraft.base_location}
              onChange={({ state, city }) =>
                setEditDraft({ ...editDraft, base_state: state, base_location: city })
              }
            />
            <Field label="Note for the admin" htmlFor="edit_member_note" className="sm:col-span-2">
              <textarea
                id="edit_member_note"
                rows={2}
                maxLength={500}
                className={TEXTAREA}
                value={editDraft.note}
                onChange={(e) => setEditDraft({ ...editDraft, note: e.target.value })}
              />
            </Field>
            {formError && <p className="text-xs text-danger sm:col-span-2">{formError}</p>}
          </div>
        )}
      </Modal>

      {/* Ask to remove */}
      <Modal
        open={removing !== null}
        onClose={() => setRemoving(null)}
        title={removing ? `Ask to remove ${removing.full_name}` : ''}
        description="An admin approves it. Their past trips stay on record."
        footer={
          <>
            <Button variant="secondary" onClick={() => setRemoving(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={remove.isPending}
              disabled={removeReason.trim().length < 3}
              onClick={() => remove.mutate()}
            >
              Send for approval
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="What happened" htmlFor="remove_status">
            <Select
              id="remove_status"
              value={removeStatus}
              onChange={(e) => setRemoveStatus(e.target.value as 'LEFT' | 'DEACTIVATED')}
            >
              <option value="LEFT">They have left the organisation</option>
              <option value="DEACTIVATED">Switch their account off for now</option>
            </Select>
          </Field>
          {removeStatus === 'LEFT' && (
            <Field label="Last day (optional)" htmlFor="remove_date" hint="Defaults to today.">
              <Input
                id="remove_date"
                type="date"
                max={todayInIndia()}
                value={removeDate}
                onChange={(e) => setRemoveDate(e.target.value)}
              />
            </Field>
          )}
          <Field label="Reason" htmlFor="remove_reason" required>
            <textarea
              id="remove_reason"
              rows={2}
              maxLength={500}
              className={TEXTAREA}
              value={removeReason}
              onChange={(e) => setRemoveReason(e.target.value)}
              placeholder="Resigned, last day 30 Sept"
            />
          </Field>
          {formError && <p className="text-xs text-danger">{formError}</p>}
        </div>
      </Modal>

      <Modal
        open={historyFor !== null}
        onClose={() => setHistoryFor(null)}
        title={historyFor ? `${historyFor.full_name}: travel history` : ''}
        description="Every movement, including trips they were tagged onto by a colleague."
        className="sm:max-w-2xl"
      >
        {historyFor && <TravelHistoryPanel userId={historyFor.id} />}
      </Modal>
    </div>
  );
}
