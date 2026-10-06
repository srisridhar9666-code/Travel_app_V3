import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CalendarRange,
  Copy,
  FileText,
  KeyRound,
  LockOpen,
  MailPlus,
  Pencil,
  Search,
  ShieldAlert,
  Upload,
  UserCog,
  UserPlus,
  Users as UsersIcon,
} from 'lucide-react';
import { useState, type FormEvent } from 'react';
import toast from 'react-hot-toast';

import { BulkImportModal } from '@/components/BulkImportModal';
import { ChangeStatusModal } from '@/components/ChangeStatusModal';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { DepartmentPicker } from '@/components/DepartmentPicker';
import { EmailLinkChoice } from '@/components/EmailLinkChoice';
import { IdProofsPanel } from '@/components/IdProofsPanel';
import { PlacePicker } from '@/components/PlacePicker';
import { TravelHistoryPanel } from '@/components/TravelHistoryPanel';
import { Modal } from '@/components/Modal';
import { TeamChangesPanel } from '@/components/TeamChangesPanel';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
  ZEBRA_ROWS,
} from '@/components/ui';
import {
  createUser,
  errorMessage,
  fetchDepartments,
  fetchRetentionStatus,
  fetchUsers,
  reinviteUser,
  runRetentionPurge,
  unlockUser,
  updateUser,
  type UserPayload,
  type UserUpdatePayload,
} from '@/lib/api';
import { formatInstantDate } from '@/lib/time';
import { cn, MOBILE_HINT, mobileDigits } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import {
  ACCOUNT_ROLES,
  DESIGNATION_LABELS,
  GENDER_LABELS,
  ROLE_DESCRIPTIONS,
  ROLE_LABELS,
  ROLE_RANK,
  SELECTABLE_GENDERS,
  USER_STATUS_LABELS,
  type Designation,
  type InviteLink,
  type Role,
  type UserRow,
  type UserStatus,
} from '@/types';

const BLANK: UserPayload = {
  email: '',
  full_name: '',
  role: 'GROUND_STAFF',
  designation: 'EXECUTIVE',
  // Chosen on purpose, every time: it decides who may share a room.
  gender: '',
  phone: '',
  employee_code: '',
  base_state: '',
  base_location: '',
  department_id: null,
  manager_id: null,
  send_email: true,
};

interface EditForm {
  email: string;
  full_name: string;
  role: string;
  designation: string;
  gender: string;
  phone: string;
  employee_code: string;
  base_state: string;
  base_location: string;
  department_id: number | null;
  manager_id: number | null;
}

const GENDER_REQUIRED = 'Choose Male or Female.';
/** Lowest first, the order the role pickers list them in. */
const ROLES_BY_RANK = (Object.keys(ROLE_RANK) as Role[])
  // System admin is no longer offered: it was the same job as Admin.
  .filter((role) => role !== 'SYSTEM_ADMIN')
  .sort((a, b) => ROLE_RANK[a] - ROLE_RANK[b]);
const MANAGER_HINT = 'Their manager sees their trips (never costs) and can ask for changes to their details.';

/** Designation "Manager" is a job title only; the list of managers is everyone
 *  whose App access is Manager. Saying so, with names, when the two differ. */
function reportsToHint(people: UserRow[]): string {
  const titledOnly = people.filter(
    (p) => p.designation === 'MANAGER' && p.role !== 'MANAGER' && p.status === 'ACTIVE',
  );
  if (titledOnly.length === 0) return MANAGER_HINT;
  const names = titledOnly.slice(0, 3).map((p) => p.full_name).join(', ');
  return `Only people whose App access is Manager are listed. ${names}${
    titledOnly.length > 3 ? ' and others' : ''
  } ${titledOnly.length === 1 ? 'has' : 'have'} the designation Manager but not Manager access - edit them and set App access to Manager.`;
}
const DEPARTMENT_HINT = 'e.g. Field Operations, Data, Finance. Type a new one to add it.';

function isSelectableGender(gender: string): boolean {
  return (SELECTABLE_GENDERS as readonly string[]).includes(gender);
}

function StatusBadge({ user }: { user: UserRow }) {
  switch (user.status) {
    case 'DELETED':
      return <Badge tone="danger">Deleted</Badge>;
    case 'LEFT':
      return (
        <span title={user.exited_on ? `Left on ${formatInstantDate(user.exited_on)}` : undefined}>
          <Badge tone="neutral">Left</Badge>
        </span>
      );
    case 'DEACTIVATED':
      return <Badge tone="warning">Deactivated</Badge>;
    default:
      if (user.is_locked) return <Badge tone="danger">Locked</Badge>;
      if (!user.has_password) return <Badge tone="warning">Invited</Badge>;
      return <Badge tone="success">Active</Badge>;
  }
}

/** Clipboard is unavailable on insecure origins, so always offer the raw link. */
async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success('Link copied');
  } catch {
    toast.error('Could not copy - select the link and copy it manually');
  }
}

interface IssuedLink {
  name: string;
  url: string;
  emailed: boolean;
  /** The admin chose to share it themselves, so not emailing is no failure. */
  byHand: boolean;
  detail: string | null;
  kind: 'invite' | 'reset';
}

function issued(
  name: string,
  result: InviteLink,
  hadPassword: boolean,
  byHand = false,
): IssuedLink | null {
  if (!result.invite_url) return null;
  // The server says which kind it issued; older answers did not, and then
  // having a password already means it was a reset.
  const reset = result.purpose ? result.purpose === 'PASSWORD_RESET' : hadPassword;
  const kind: IssuedLink['kind'] = reset ? 'reset' : 'invite';
  return {
    name,
    url: result.invite_url,
    emailed: Boolean(result.email_sent),
    byHand,
    detail: result.email_detail ?? null,
    kind,
  };
}

/** "Reports to": an active manager, or nobody. */
function ManagerSelect({
  id,
  value,
  managers,
  current,
  onChange,
}: {
  id: string;
  value: number | null;
  managers: UserRow[];
  /** Their manager now, kept in the list even if no longer active. */
  current?: { id: number; name: string | null } | null;
  onChange: (managerId: number | null) => void;
}) {
  const stale = current && !managers.some((m) => m.id === current.id);
  return (
    <Select
      id={id}
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
    >
      <option value="">{managers.length ? 'Nobody - not in a team' : 'No managers yet'}</option>
      {stale && (
        <option value={current.id} disabled>
          {current.name ?? 'Their manager'} (no longer a manager)
        </option>
      )}
      {managers.map((m) => (
        <option key={m.id} value={m.id}>
          {m.full_name}
          {m.department_name ? ` · ${m.department_name}` : ''}
        </option>
      ))}
    </Select>
  );
}

export default function TeamPage() {
  const queryClient = useQueryClient();
  const me = useAuth((s) => s.user);
  const isSystemAdmin = !!me && ACCOUNT_ROLES.includes(me.role);
  const myRank = me ? ROLE_RANK[me.role] : 0;
  /** The roles this admin may hand out: their own and those below it. */
  const grantable = ROLES_BY_RANK.filter((role) => ROLE_RANK[role] <= myRank);

  /** Nobody changes the account of someone above them; the server refuses it
   *  too, this just keeps the buttons from offering it. */
  const canManage = (user: UserRow) => ROLE_RANK[user.role] <= myRank || user.id === me?.id;

  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<UserStatus | ''>('');
  const [departmentFilter, setDepartmentFilter] = useState('');
  const [managerFilter, setManagerFilter] = useState('');

  const [inviteOpen, setInviteOpen] = useState(false);
  const [form, setForm] = useState<UserPayload>(BLANK);
  const [formError, setFormError] = useState<string | null>(null);
  const [issuedLink, setIssuedLink] = useState<IssuedLink | null>(null);

  const [importOpen, setImportOpen] = useState(false);
  const [docsUser, setDocsUser] = useState<UserRow | null>(null);
  const [historyUser, setHistoryUser] = useState<UserRow | null>(null);
  const [statusUser, setStatusUser] = useState<UserRow | null>(null);
  const [resetUser, setResetUser] = useState<UserRow | null>(null);
  const [linkByEmail, setLinkByEmail] = useState(true);
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [editUser, setEditUser] = useState<UserRow | null>(null);
  const [editForm, setEditForm] = useState<EditForm | null>(null);
  const [editError, setEditError] = useState<string | null>(null);

  const users = useQuery({
    queryKey: ['users', search, roleFilter, statusFilter, departmentFilter, managerFilter],
    queryFn: () =>
      fetchUsers({
        search: search.trim() || undefined,
        role: roleFilter || undefined,
        status: statusFilter || undefined,
        department_id: departmentFilter ? Number(departmentFilter) : undefined,
        manager_id: managerFilter ? Number(managerFilter) : undefined,
        page_size: 100,
      }),
  });
  // Who ground staff can report to, for the pickers and the filter.
  const managers = useQuery({
    queryKey: ['users', 'managers'],
    queryFn: () => fetchUsers({ role: 'MANAGER', status: 'ACTIVE', page_size: 200 }),
  });
  const managerOptions = managers.data?.items ?? [];

  const departments = useQuery({ queryKey: ['departments'], queryFn: fetchDepartments });
  const retention = useQuery({ queryKey: ['retention'], queryFn: fetchRetentionStatus });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['users'] });
    queryClient.invalidateQueries({ queryKey: ['retention'] });
    queryClient.invalidateQueries({ queryKey: ['departments'] });
    // The report filters list people by name.
    queryClient.invalidateQueries({ queryKey: ['filter-options'] });
  };

  const invite = useMutation({
    mutationFn: () =>
      createUser({
        ...form,
        email: form.email.trim().toLowerCase(),
        phone: form.phone?.trim() || null,
        employee_code: form.employee_code?.trim() || null,
        base_state: form.base_state || null,
        base_location: form.base_location || null,
        manager_id: form.role === 'GROUND_STAFF' ? (form.manager_id ?? null) : null,
      }),
    meta: { errorFallback: 'Could not create this account.' },
    onSuccess: (result) => {
      const name = form.full_name.trim();
      toast.success(
        result.email_sent ? `Invitation emailed to ${name}` : `${name} added - share the invitation link`,
      );
      setInviteOpen(false);
      setForm(BLANK);
      setFormError(null);
      refresh();
      setIssuedLink(issued(name, result, false, form.send_email === false));
    },
    onError: (err) => setFormError(errorMessage(err, 'Could not create this account.')),
  });

  const saveEdit = useMutation({
    mutationFn: () => {
      const payload: UserUpdatePayload = {
        full_name: editForm!.full_name,
        role: editForm!.role,
        designation: editForm!.designation || null,
        gender: editForm!.gender,
        phone: editForm!.phone.trim() || null,
        employee_code: editForm!.employee_code.trim() || null,
        base_state: editForm!.base_state || null,
        base_location: editForm!.base_location || null,
        department_id: editForm!.department_id,
        manager_id: editForm!.role === 'GROUND_STAFF' ? editForm!.manager_id : null,
      };
      // Sent only when it changed: the server tells the old address, and
      // refuses a change to your own (that needs your password, on My profile).
      const email = editForm!.email.trim().toLowerCase();
      if (email !== editUser!.email) payload.email = email;
      return updateUser(editUser!.id, payload);
    },
    meta: { errorFallback: 'Could not save these changes.' },
    onSuccess: (updated) => {
      toast.success(`${updated.full_name} updated`);
      setEditUser(null);
      setEditForm(null);
      setEditError(null);
      refresh();
      // Their own row: the shell and My profile read the signed-in profile.
      if (updated.id === me?.id) queryClient.invalidateQueries({ queryKey: ['me'] });
    },
    onError: (err) => setEditError(errorMessage(err, 'Could not save these changes.')),
  });

  const reinvite = useMutation({
    mutationFn: (vars: { user: UserRow; sendEmail: boolean }) =>
      reinviteUser(vars.user.id, vars.sendEmail),
    onSuccess: (result, { user, sendEmail }) => {
      const link = issued(user.full_name, result, user.has_password, !sendEmail);
      const what = link?.kind === 'reset' ? 'Password reset link' : 'New invitation';
      toast.success(
        result.email_sent
          ? `${what} emailed to ${user.full_name}`
          : `${what} ready for ${user.full_name} - share the link`,
      );
      setResetUser(null);
      refresh();
      setIssuedLink(link);
    },
  });

  const unlock = useMutation({
    mutationFn: (user: UserRow) => unlockUser(user.id),
    onSuccess: (_r, user) => {
      toast.success(`${user.full_name} unlocked`);
      refresh();
    },
  });

  const purge = useMutation({
    mutationFn: runRetentionPurge,
    onSuccess: (result) => {
      toast.success(`${result.purged} document(s) purged`);
      setPurgeOpen(false);
      refresh();
    },
  });

  /** An invitation just goes again; a reset link asks first, since it is a
   *  way into someone's account. */
  /** Both kinds of link ask first, so the admin can choose how it goes out. */
  const sendLink = (user: UserRow) => {
    setLinkByEmail(true);
    setResetUser(user);
  };

  const openEdit = (user: UserRow) => {
    setEditUser(user);
    setEditForm({
      email: user.email,
      full_name: user.full_name,
      role: user.role,
      designation: user.designation ?? '',
      // A value saved before only Male and Female could be chosen starts
      // blank, so the required field makes the admin pick one.
      gender: isSelectableGender(user.gender) ? user.gender : '',
      phone: user.phone ?? '',
      employee_code: user.employee_code ?? '',
      base_state: user.base_state ?? '',
      base_location: user.base_location ?? '',
      department_id: user.department_id,
      manager_id: user.manager_id,
    });
    setEditError(null);
  };

  const submitInvite = (event: FormEvent) => {
    event.preventDefault();
    if (!isSelectableGender(form.gender)) {
      setFormError(GENDER_REQUIRED);
      return;
    }
    setFormError(null);
    invite.mutate();
  };

  const submitEdit = (event: FormEvent) => {
    event.preventDefault();
    if (!editForm || !isSelectableGender(editForm.gender)) {
      setEditError(GENDER_REQUIRED);
      return;
    }
    setEditError(null);
    saveEdit.mutate();
  };

  const rows = users.data?.items ?? [];
  const dueNow = retention.data?.due_now ?? 0;
  const editingSelf = editUser?.id === me?.id;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Team</h1>
          <p className="mt-1.5 max-w-2xl text-sm text-text-muted">
            Accounts are created here and activated by the person through an invitation link.
            Only people marked Active can sign in.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => setImportOpen(true)}>
            <Upload size={15} />
            Import CSV
          </Button>
          <Button onClick={() => setInviteOpen(true)}>
            <UserPlus size={15} />
            Invite someone
          </Button>
        </div>
      </div>

      {/* Retention is surfaced before it deletes, not only in the ledger after. */}
      {dueNow > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-warning-soft px-4 py-3 text-sm text-warning">
          <ShieldAlert size={16} className="shrink-0" />
          <span className="flex-1">
            {dueNow} identity document{dueNow === 1 ? '' : 's'} belong to people who left more
            than {retention.data?.retention_days} days ago and are due for deletion.
          </span>
          {isSystemAdmin && (
            <Button size="sm" variant="secondary" onClick={() => setPurgeOpen(true)}>
              Purge now
            </Button>
          )}
        </div>
      )}

      <TeamChangesPanel
        onInvite={(name, result, byHand) => setIssuedLink(issued(name, result, false, byHand))}
      />

      <Card>
        <CardHeader
          title={`${users.data?.total ?? 0} ${users.data?.total === 1 ? 'person' : 'people'}`}
          action={
            <div className="flex flex-wrap gap-2">
              <div className="relative">
                <Search
                  size={14}
                  className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-subtle"
                />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search name, email, code or place"
                  className="w-64 pl-8"
                  aria-label="Search team"
                />
              </div>
              <Select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as UserStatus | '')}
                aria-label="Filter by status"
                className="w-44"
              >
                <option value="">Everyone but deleted</option>
                {(Object.keys(USER_STATUS_LABELS) as UserStatus[]).map((status) => (
                  <option key={status} value={status}>
                    {USER_STATUS_LABELS[status]}
                  </option>
                ))}
              </Select>
              <Select
                value={departmentFilter}
                onChange={(e) => setDepartmentFilter(e.target.value)}
                aria-label="Filter by department"
                className="w-44"
              >
                <option value="">All departments</option>
                {(departments.data ?? []).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </Select>
              <Select
                value={roleFilter}
                onChange={(e) => setRoleFilter(e.target.value)}
                aria-label="Filter by app access"
                className="w-36"
              >
                <option value="">Any access</option>
                {ROLES_BY_RANK.map((role) => (
                  <option key={role} value={role}>
                    {ROLE_LABELS[role]}
                  </option>
                ))}
              </Select>
              {managerOptions.length > 0 && (
                <Select
                  value={managerFilter}
                  onChange={(e) => setManagerFilter(e.target.value)}
                  aria-label="Filter by manager"
                  className="w-44"
                >
                  <option value="">Any manager</option>
                  {managerOptions.map((m) => (
                    <option key={m.id} value={m.id}>
                      Reports to {m.full_name}
                    </option>
                  ))}
                </Select>
              )}
            </div>
          }
        />

        {users.isPending ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : users.isError ? (
          <EmptyState
            icon={<UsersIcon size={28} />}
            title="Could not load the team"
            description={errorMessage(users.error)}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<UsersIcon size={28} />}
            title="Nobody matches that"
            description="Try a different search or filter, or invite someone new."
          />
        ) : (
          // Rounded at the foot, so the banded rows keep the card's corners.
          <div className="overflow-x-auto rounded-b-xl">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-2xs uppercase tracking-widest text-text-subtle">
                  <th className="px-5 py-2.5 font-semibold">Name</th>
                  <th className="px-5 py-2.5 font-semibold">App access</th>
                  <th className="hidden px-5 py-2.5 font-semibold md:table-cell">Department</th>
                  <th className="hidden px-5 py-2.5 font-semibold xl:table-cell">Designation</th>
                  <th className="hidden px-5 py-2.5 font-semibold lg:table-cell">Base</th>
                  <th className="px-5 py-2.5 font-semibold">Status</th>
                  <th className="px-5 py-2.5 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              {/* Banded: each person is two lines (name and email, access and
                  manager), and the band keeps them together. */}
              <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                {rows.map((user) => {
                  const manageable = canManage(user);
                  return (
                    <tr key={user.id} className="transition-colors hover:bg-surface-sunken/60">
                      <td className="px-5 py-3">
                        <div className="font-medium">
                          {user.full_name}
                          {user.id === me?.id && (
                            <span className="ml-1.5 text-2xs text-text-subtle">(you)</span>
                          )}
                          {!isSelectableGender(user.gender) && (
                            <Badge tone="warning" className="ml-1.5 align-middle">
                              Gender not set
                            </Badge>
                          )}
                        </div>
                        <div className="text-xs text-text-muted">{user.email}</div>
                      </td>
                      <td className="px-5 py-3 text-text-muted">
                        {ROLE_LABELS[user.role]}
                        {user.manager_name && (
                          <div className="text-xs text-text-subtle">Reports to {user.manager_name}</div>
                        )}
                      </td>
                      <td className="hidden px-5 py-3 text-text-muted md:table-cell">
                        {user.department_name || '—'}
                      </td>
                      <td className="hidden px-5 py-3 text-text-muted xl:table-cell">
                        {user.designation ? DESIGNATION_LABELS[user.designation] : '—'}
                      </td>
                      <td className="hidden px-5 py-3 text-text-muted lg:table-cell">
                        {[user.base_location, user.base_state].filter(Boolean).join(', ') || '—'}
                      </td>
                      <td className="px-5 py-3">
                        <StatusBadge user={user} />
                      </td>
                      <td className="px-5 py-3">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            title="Travel history"
                            onClick={() => setHistoryUser(user)}
                          >
                            <CalendarRange size={14} />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            title="Identity documents"
                            onClick={() => setDocsUser(user)}
                          >
                            <FileText size={14} />
                          </Button>
                          {manageable && (
                            <Button
                              variant="ghost"
                              size="sm"
                              title="Edit profile"
                              onClick={() => openEdit(user)}
                            >
                              <Pencil size={14} />
                            </Button>
                          )}
                          {manageable && user.is_locked && (
                            <Button
                              variant="ghost"
                              size="sm"
                              title="Unlock"
                              loading={unlock.isPending && unlock.variables?.id === user.id}
                              onClick={() => unlock.mutate(user)}
                            >
                              <LockOpen size={14} />
                            </Button>
                          )}
                          {manageable && user.status === 'ACTIVE' && (
                            <Button
                              variant="ghost"
                              size="sm"
                              title={user.has_password ? 'Reset password' : 'Resend invitation'}
                              loading={
                                reinvite.isPending &&
                                !resetUser &&
                                reinvite.variables?.user.id === user.id
                              }
                              onClick={() => sendLink(user)}
                            >
                              <MailPlus size={14} />
                            </Button>
                          )}
                          {manageable && user.id !== me?.id && (
                            <Button
                              variant="ghost"
                              size="sm"
                              title="Change status"
                              onClick={() => setStatusUser(user)}
                            >
                              <UserCog size={14} />
                              <span className="hidden sm:inline">Status</span>
                            </Button>
                          )}
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

      {/* Travel history - SOW section 5 */}
      <Modal
        open={Boolean(historyUser)}
        onClose={() => setHistoryUser(null)}
        title={historyUser ? `${historyUser.full_name}: travel history` : ''}
        description="Every movement, including trips they were tagged onto by a colleague."
        className="sm:max-w-2xl"
        footer={
          <Button variant="secondary" onClick={() => setHistoryUser(null)}>
            Close
          </Button>
        }
      >
        {historyUser && <TravelHistoryPanel userId={historyUser.id} />}
      </Modal>

      {/* Identity documents */}
      <Modal
        open={Boolean(docsUser)}
        onClose={() => setDocsUser(null)}
        title={docsUser ? `${docsUser.full_name}: identity documents` : ''}
        description={docsUser?.email}
        className="sm:max-w-2xl"
        footer={
          <Button variant="secondary" onClick={() => setDocsUser(null)}>
            Close
          </Button>
        }
      >
        {docsUser && <IdProofsPanel user={docsUser} />}
      </Modal>

      <ChangeStatusModal
        key={statusUser?.id ?? 'none'}
        user={statusUser}
        onClose={() => setStatusUser(null)}
        onChanged={refresh}
      />

      {/* Edit profile */}
      <Modal
        open={Boolean(editUser)}
        onClose={() => {
          setEditUser(null);
          setEditError(null);
        }}
        title={editUser ? `Edit ${editUser.full_name}` : ''}
        description="Every change is recorded in the activity log."
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditUser(null)}>
              Cancel
            </Button>
            <Button form="edit-form" type="submit" loading={saveEdit.isPending}>
              Save changes
            </Button>
          </>
        }
      >
        {editForm && editUser && (
          <form id="edit-form" onSubmit={submitEdit} className="space-y-4" noValidate>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Full name" htmlFor="edit_name" required className="sm:col-span-2">
                <Input
                  id="edit_name"
                  required
                  value={editForm.full_name}
                  onChange={(e) => setEditForm({ ...editForm, full_name: e.target.value })}
                />
              </Field>

              <Field
                label="Work email"
                htmlFor="edit_email"
                required
                className="sm:col-span-2"
                hint={
                  editingSelf
                    ? 'Change your own email from My profile.'
                    : !editUser.has_password
                      ? 'They have not accepted their invitation yet. Send a new one after changing this.'
                      : 'They will sign in with the new address. We will let the old address know.'
                }
              >
                <Input
                  id="edit_email"
                  type="email"
                  required
                  disabled={editingSelf}
                  value={editForm.email}
                  onChange={(e) => setEditForm({ ...editForm, email: e.target.value })}
                />
              </Field>

              <Field
                label="App access"
                htmlFor="edit_role"
                hint={ROLE_DESCRIPTIONS[editForm.role as Role]}
              >
                <Select
                  id="edit_role"
                  value={editForm.role}
                  disabled={editingSelf}
                  onChange={(e) =>
                    setEditForm({
                      ...editForm,
                      role: e.target.value,
                      // A manager's job title follows their access, unless set.
                      designation:
                        e.target.value === 'MANAGER' && !editForm.designation ? 'MANAGER' : editForm.designation,
                    })
                  }
                >
                  {ROLES_BY_RANK.filter(
                    (role) => grantable.includes(role) || role === editForm.role,
                  ).map((role) => (
                    <option key={role} value={role}>
                      {ROLE_LABELS[role]}
                    </option>
                  ))}
                </Select>
              </Field>

              {editForm.role === 'GROUND_STAFF' && (
                <Field
                  label="Reports to"
                  htmlFor="edit_manager"
                  hint={reportsToHint(rows)}
                  className="sm:col-span-2"
                >
                  <ManagerSelect
                    id="edit_manager"
                    value={editForm.manager_id}
                    managers={managerOptions}
                    current={editUser.manager_id ? { id: editUser.manager_id, name: editUser.manager_name } : null}
                    onChange={(manager_id) => setEditForm({ ...editForm, manager_id })}
                  />
                </Field>
              )}

              <Field label="Designation" htmlFor="edit_designation">
                <Select
                  id="edit_designation"
                  value={editForm.designation}
                  onChange={(e) => setEditForm({ ...editForm, designation: e.target.value })}
                >
                  <option value="">Not set</option>
                  {(Object.keys(DESIGNATION_LABELS) as Designation[]).map((d) => (
                    <option key={d} value={d}>
                      {DESIGNATION_LABELS[d]}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field
                label="Department"
                htmlFor="edit_department"
                hint={DEPARTMENT_HINT}
                className="sm:col-span-2"
              >
                <DepartmentPicker
                  id="edit_department"
                  value={editForm.department_id}
                  onChange={(department_id) => setEditForm({ ...editForm, department_id })}
                />
              </Field>

              <Field
                label="Gender"
                htmlFor="edit_gender"
                required
                hint="Decides who may share a room."
                error={
                  editForm.gender === '' && !isSelectableGender(editUser.gender)
                    ? `Recorded as "${GENDER_LABELS[editUser.gender]}". Choose Male or Female.`
                    : null
                }
              >
                <Select
                  id="edit_gender"
                  required
                  value={editForm.gender}
                  onChange={(e) => setEditForm({ ...editForm, gender: e.target.value })}
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

              <Field label="Phone" htmlFor="edit_phone" hint={MOBILE_HINT}>
                <Input
                  id="edit_phone"
                  type="tel"
                  value={editForm.phone}
                  onChange={(e) => setEditForm({ ...editForm, phone: mobileDigits(e.target.value) })}
                  placeholder="9876543210"
                  inputMode="numeric"
                />
              </Field>

              <PlacePicker
                label="Base"
                id="edit_base"
                className="sm:col-span-2"
                state={editForm.base_state}
                city={editForm.base_location}
                onChange={({ state, city }) =>
                  setEditForm({ ...editForm, base_state: state, base_location: city })
                }
              />

              <Field label="Employee code" htmlFor="edit_code">
                <Input
                  id="edit_code"
                  value={editForm.employee_code}
                  onChange={(e) => setEditForm({ ...editForm, employee_code: e.target.value })}
                />
              </Field>
            </div>

            {/* An admin never sees or sets anyone's password: they hand over a
                one-time link, emailed and shown here to share on WhatsApp. */}
            {!editingSelf && editUser.status === 'ACTIVE' && (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-surface-sunken px-3 py-3">
                <p className="max-w-sm text-xs text-text-muted">
                  {editUser.has_password
                    ? 'You never see their password. A reset link (valid 72 hours) is emailed to them and shown to you, so you can share it if email is off.'
                    : 'They have not set a password yet. Send a fresh invitation link (valid 72 hours).'}
                </p>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    // One dialog at a time: the confirmation and the link
                    // replace this form rather than stacking on top of it.
                    const target = editUser;
                    setEditUser(null);
                    setEditForm(null);
                    sendLink(target);
                  }}
                >
                  {editUser.has_password ? <KeyRound size={14} /> : <MailPlus size={14} />}
                  {editUser.has_password ? 'Reset password' : 'Resend invitation'}
                </Button>
              </div>
            )}

            {editError && (
              <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
                {editError}
              </p>
            )}
          </form>
        )}
      </Modal>

      {/* Invite */}
      <Modal
        open={inviteOpen}
        onClose={() => {
          setInviteOpen(false);
          setFormError(null);
        }}
        title="Invite someone"
        description="They set their own password from the link. You never see it."
        footer={
          <>
            <Button variant="secondary" onClick={() => setInviteOpen(false)}>
              Cancel
            </Button>
            <Button form="invite-form" type="submit" loading={invite.isPending}>
              Send invitation
            </Button>
          </>
        }
      >
        <form id="invite-form" onSubmit={submitInvite} className="space-y-4" noValidate>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Full name" htmlFor="full_name" required className="sm:col-span-2">
              <Input
                id="full_name"
                required
                value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                placeholder="Ravi Kumar"
              />
            </Field>

            <Field label="Work email" htmlFor="new_email" required className="sm:col-span-2">
              <Input
                id="new_email"
                type="email"
                required
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                placeholder="ravi@designboxed.com"
              />
            </Field>

            <Field
              label="App access"
              htmlFor="role"
              required
              hint={ROLE_DESCRIPTIONS[form.role as Role]}
            >
              <Select
                id="role"
                value={form.role}
                onChange={(e) =>
                  setForm({
                    ...form,
                    role: e.target.value,
                    designation:
                      e.target.value === 'MANAGER' && (!form.designation || form.designation === 'EXECUTIVE')
                        ? 'MANAGER'
                        : form.designation,
                  })
                }
              >
                {grantable.map((role) => (
                  <option key={role} value={role}>
                    {ROLE_LABELS[role]}
                  </option>
                ))}
              </Select>
            </Field>

            {form.role === 'GROUND_STAFF' && (
              <Field label="Reports to" htmlFor="manager" hint={reportsToHint(rows)} className="sm:col-span-2">
                <ManagerSelect
                  id="manager"
                  value={form.manager_id ?? null}
                  managers={managerOptions}
                  onChange={(manager_id) => setForm({ ...form, manager_id })}
                />
              </Field>
            )}

            <Field
              label="Designation"
              htmlFor="designation"
              hint="Hierarchy only. It does not affect approvals."
            >
              <Select
                id="designation"
                value={form.designation ?? ''}
                onChange={(e) => setForm({ ...form, designation: e.target.value || null })}
              >
                <option value="">Not set</option>
                {(Object.keys(DESIGNATION_LABELS) as Designation[]).map((d) => (
                  <option key={d} value={d}>
                    {DESIGNATION_LABELS[d]}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              label="Department"
              htmlFor="department"
              hint={DEPARTMENT_HINT}
              className="sm:col-span-2"
            >
              <DepartmentPicker
                id="department"
                value={form.department_id ?? null}
                onChange={(department_id) => setForm({ ...form, department_id })}
              />
            </Field>

            <Field label="Gender" htmlFor="gender" required hint="Decides who may share a room.">
              <Select
                id="gender"
                required
                value={form.gender}
                onChange={(e) => setForm({ ...form, gender: e.target.value })}
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

            <Field label="Phone" htmlFor="phone" hint={MOBILE_HINT}>
              <Input
                id="phone"
                type="tel"
                value={form.phone ?? ''}
                onChange={(e) => setForm({ ...form, phone: mobileDigits(e.target.value) })}
                placeholder="9876543210"
                inputMode="numeric"
              />
            </Field>

            <PlacePicker
              label="Base"
              id="base"
              className="sm:col-span-2"
              state={form.base_state ?? ''}
              city={form.base_location ?? ''}
              onChange={({ state, city }) =>
                setForm({ ...form, base_state: state, base_location: city })
              }
            />

            <Field label="Employee code" htmlFor="employee_code">
              <Input
                id="employee_code"
                value={form.employee_code ?? ''}
                onChange={(e) => setForm({ ...form, employee_code: e.target.value })}
                placeholder="DB-1042"
              />
            </Field>
          </div>

          <EmailLinkChoice
            name={form.full_name.trim().split(/\s+/)[0] ?? ''}
            checked={form.send_email !== false}
            onChange={(send_email) => setForm({ ...form, send_email })}
          />

          {formError && (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
              {formError}
            </p>
          )}
        </form>
      </Modal>

      <ConfirmDialog
        open={Boolean(resetUser)}
        title={
          !resetUser
            ? ''
            : resetUser.has_password
              ? `Reset ${resetUser.full_name}'s password?`
              : `Send ${resetUser.full_name} a new invitation?`
        }
        confirmLabel={resetUser?.has_password ? 'Create reset link' : 'Create invitation link'}
        tone="primary"
        loading={reinvite.isPending}
        onConfirm={() => resetUser && reinvite.mutate({ user: resetUser, sendEmail: linkByEmail })}
        onClose={() => setResetUser(null)}
      >
        <p>
          {resetUser?.has_password
            ? 'They get a one-time link to choose a new password, valid for 72 hours. Their current password keeps working until they use it.'
            : 'A fresh one-time link to set their password, valid for 72 hours. Any earlier link stops working.'}
        </p>
        <div className="mt-3">
          <EmailLinkChoice
            name={resetUser?.full_name ?? ''}
            checked={linkByEmail}
            onChange={setLinkByEmail}
          />
        </div>
      </ConfirmDialog>

      <ConfirmDialog
        open={purgeOpen}
        title={`Purge ${dueNow} identity document${dueNow === 1 ? '' : 's'}?`}
        confirmLabel="Purge permanently"
        loading={purge.isPending}
        onConfirm={() => purge.mutate()}
        onClose={() => setPurgeOpen(false)}
      >
        <p>
          The numbers and scans are deleted for good. The activity log keeps a record that they
          existed and were purged.
        </p>
      </ConfirmDialog>

      {/* The link, shown once. It is emailed too; this is the fallback. */}
      <Modal
        open={Boolean(issuedLink)}
        onClose={() => setIssuedLink(null)}
        title={
          issuedLink?.kind === 'reset'
            ? issuedLink.emailed
              ? 'Password reset link emailed'
              : 'Password reset link'
            : issuedLink?.emailed
              ? 'Invitation emailed'
              : 'Invitation link'
        }
        description={
          issuedLink?.emailed
            ? 'They will get an email with this link. You can also send it yourself.'
            : issuedLink?.byHand
              ? 'Copy this link and send it to them yourself - on WhatsApp, SMS or email.'
              : 'The email did not go out, so send this link to them yourself.'
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setIssuedLink(null)}>
              Done
            </Button>
            <Button onClick={() => issuedLink && copy(issuedLink.url)}>
              <Copy size={14} />
              Copy link
            </Button>
          </>
        }
      >
        {issuedLink && !issuedLink.emailed && !issuedLink.byHand && issuedLink.detail && (
          <p className="mb-3 rounded-md bg-warning-soft px-3 py-2 text-xs text-warning">
            Email not sent: {issuedLink.detail}
          </p>
        )}
        <p className="text-sm text-text-muted">
          {issuedLink?.emailed ? 'Sent to' : 'Send this to'}{' '}
          <span className="font-medium text-text">{issuedLink?.name}</span>.{' '}
          {issuedLink?.kind === 'reset'
            ? 'They choose a new password from it. It can be used once and expires in 72 hours.'
            : 'It can be used once and expires in 72 hours.'}
        </p>
        <code className="mt-3 block max-h-32 overflow-auto break-all rounded-md bg-surface-sunken px-3 py-2.5 font-mono text-xs text-text-muted">
          {issuedLink?.url}
        </code>
      </Modal>

      <BulkImportModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={refresh}
      />
    </div>
  );
}
