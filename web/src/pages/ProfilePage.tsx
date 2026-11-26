import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, KeyRound, Mail, UserRound } from 'lucide-react';
import { useMemo, useState, type FormEvent, type ReactNode } from 'react';
import toast from 'react-hot-toast';

import { DepartmentPicker } from '@/components/DepartmentPicker';
import { PasswordChecklist } from '@/components/PasswordChecklist';
import { PlacePicker } from '@/components/PlacePicker';
import { Button, Card, CardHeader, Field, Input, PageHeader, Select } from '@/components/ui';
import {
  changeMyEmail,
  changePassword,
  errorMessage,
  fetchMe,
  updateMyProfile,
  updateUser,
  type UserUpdatePayload,
} from '@/lib/api';
import { passwordIssues } from '@/lib/password';
import { formatInstant } from '@/lib/time';
import { MOBILE_HINT, mobileDigits } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import {
  DESIGNATION_LABELS,
  GENDER_LABELS,
  ROLE_LABELS,
  SELECTABLE_GENDERS,
  type Designation,
  type UserProfile,
  isAdminRole,
} from '@/types';

function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-md bg-danger-soft px-3 py-2.5 text-xs text-danger"
    >
      <AlertCircle size={14} className="mt-px shrink-0" />
      <span>{message}</span>
    </div>
  );
}

function ReadOnly({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-2xs font-semibold uppercase tracking-wider text-text-subtle">{label}</dt>
      <dd className="mt-0.5 text-sm">{children || '—'}</dd>
    </div>
  );
}

interface Details {
  full_name: string;
  phone: string;
  designation: string;
  department_id: number | null;
  /** Blank when the stored value predates Male/Female being required. */
  gender: string;
  base_state: string;
  base_location: string;
  employee_code: string;
  hotel_occupancy_entitlement: 'SINGLE' | 'DOUBLE';
  upi_id: string;
  bank_name: string;
  bank_account_number: string;
  account_holder_name: string;
  ifsc_code: string;
}

function detailsOf(user: UserProfile): Details {
  return {
    full_name: user.full_name,
    phone: user.phone ?? '',
    designation: user.designation ?? '',
    department_id: user.department_id ?? null,
    gender: (SELECTABLE_GENDERS as readonly string[]).includes(user.gender) ? user.gender : '',
    base_state: user.base_state ?? '',
    base_location: user.base_location ?? '',
    employee_code: user.employee_code ?? '',
    hotel_occupancy_entitlement:
      (user.hotel_occupancy_entitlement as 'SINGLE' | 'DOUBLE') || 'DOUBLE',
    upi_id: user.upi_id ?? '',
    bank_name: user.bank_name ?? '',
    bank_account_number: user.bank_account_number ?? '',
    account_holder_name: user.account_holder_name ?? '',
    ifsc_code: user.ifsc_code ?? '',
  };
}

/**
 * Everyone's own page: name and phone, the email they sign in with, and their
 * password. For ground staff the rest of their record - department, gender,
 * base - is set by an admin and shown here read-only. Admins are the people
 * who set those, so they set their own here too, through the same endpoint the
 * Team page uses. App access stays read-only for everyone: nobody changes
 * their own role.
 */
export default function ProfilePage() {
  const user = useAuth((s) => s.user);
  if (!user) return null;
  return <Profile user={user} />;
}

function Profile({ user }: { user: UserProfile }) {
  const queryClient = useQueryClient();
  const isAdmin = isAdminRole(user.role);

  /** Keep the shell, and the cached copy the shell refreshes from, in step. */
  const adopt = (updated: UserProfile) => {
    useAuth.getState().setUser(updated);
    queryClient.setQueryData(['me'], updated);
  };

  // --- personal details ----------------------------------------------------
  // Null until they type, so an admin's edit that arrives on the next refresh
  // shows up instead of being hidden behind a stale copy.
  const [draft, setDraft] = useState<Details | null>(null);
  const details = draft ?? detailsOf(user);
  const edit = (patch: Partial<Details>) => setDraft({ ...details, ...patch });
  const [detailsError, setDetailsError] = useState<string | null>(null);

  const changes = useMemo(() => {
    const out: UserUpdatePayload = {};
    const name = details.full_name.trim();
    if (name !== user.full_name) out.full_name = name;
    const phone = details.phone.trim();
    if (phone !== (user.phone ?? '')) out.phone = phone || null;

    if (details.upi_id !== (user.upi_id ?? '')) out.upi_id = details.upi_id.trim() || null;
    if (details.bank_name !== (user.bank_name ?? '')) out.bank_name = details.bank_name.trim() || null;
    if (details.bank_account_number !== (user.bank_account_number ?? ''))
      out.bank_account_number = details.bank_account_number.trim() || null;
    if (details.account_holder_name !== (user.account_holder_name ?? ''))
      out.account_holder_name = details.account_holder_name.trim() || null;
    if (details.ifsc_code !== (user.ifsc_code ?? ''))
      out.ifsc_code = details.ifsc_code.trim().toUpperCase() || null;

    if (!isAdmin) return out;

    if (details.hotel_occupancy_entitlement !== (user.hotel_occupancy_entitlement || 'DOUBLE')) {
      out.hotel_occupancy_entitlement = details.hotel_occupancy_entitlement;
    }
    if (details.designation !== (user.designation ?? '')) out.designation = details.designation || null;
    if (details.department_id !== (user.department_id ?? null)) out.department_id = details.department_id;
    if (details.gender && details.gender !== user.gender) out.gender = details.gender;
    // As a pair: the server checks a city against its state.
    if (
      details.base_state !== (user.base_state ?? '') ||
      details.base_location !== (user.base_location ?? '')
    ) {
      out.base_state = details.base_state || null;
      out.base_location = details.base_location || null;
    }
    const code = details.employee_code.trim();
    if (code !== (user.employee_code ?? '')) out.employee_code = code || null;
    return out;
  }, [details, user, isAdmin]);
  const dirty = Object.keys(changes).length > 0;

  const saveDetails = useMutation({
    mutationFn: async () => {
      if (!isAdmin) {
        return updateMyProfile({
          full_name: changes.full_name,
          phone: changes.phone,
          upi_id: changes.upi_id,
          bank_name: changes.bank_name,
          bank_account_number: changes.bank_account_number,
          account_holder_name: changes.account_holder_name,
          ifsc_code: changes.ifsc_code,
        });
      }
      await updateUser(user.id, changes);
      return fetchMe();
    },
    meta: { errorFallback: 'Could not save your details.' },
    onSuccess: (updated) => {
      adopt(updated);
      setDraft(null);
      setDetailsError(null);
      if (isAdmin) {
        // Their row on Team, and the department member counts, just moved.
        queryClient.invalidateQueries({ queryKey: ['users'] });
        queryClient.invalidateQueries({ queryKey: ['departments'] });
      }
      toast.success('Profile saved');
    },
    onError: (err) => setDetailsError(errorMessage(err, 'Could not save your details.')),
  });

  // --- sign-in email ---------------------------------------------------------
  const [newEmail, setNewEmail] = useState('');
  const [emailPassword, setEmailPassword] = useState('');
  const [emailError, setEmailError] = useState<string | null>(null);

  const saveEmail = useMutation({
    mutationFn: () => changeMyEmail(newEmail.trim(), emailPassword),
    meta: { errorFallback: 'Could not change your email.' },
    onSuccess: (updated) => {
      adopt(updated);
      setNewEmail('');
      setEmailPassword('');
      setEmailError(null);
      toast.success(`You now sign in as ${updated.email}`);
    },
    onError: (err) => setEmailError(errorMessage(err, 'Could not change your email.')),
  });

  // --- password --------------------------------------------------------------
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);

  const issues = useMemo(
    () => passwordIssues(next, user.email, user.full_name),
    [next, user.email, user.full_name],
  );
  const mismatch = confirm.length > 0 && next !== confirm;
  const passwordReady =
    current.length > 0 && next.length > 0 && issues.length === 0 && confirm.length > 0 && !mismatch;

  const savePassword = useMutation({
    mutationFn: () => changePassword(current, next),
    meta: { errorFallback: 'Could not change your password.' },
    onSuccess: (result) => {
      // Every token issued before now has just stopped working, this one
      // included; carry on with the fresh one.
      useAuth.getState().replaceToken(result.access_token, result.expires_at);
      queryClient.invalidateQueries({ queryKey: ['me'] });
      setCurrent('');
      setNext('');
      setConfirm('');
      setPasswordError(null);
      toast.success('Password changed. Your other devices are signed out.');
    },
    onError: (err) => setPasswordError(errorMessage(err, 'Could not change your password.')),
  });

  const submit = (run: () => void, clear: () => void) => (event: FormEvent) => {
    event.preventDefault();
    clear();
    run();
  };

  const base = [user.base_location, user.base_state].filter(Boolean).join(', ');

  return (
    <div className="space-y-6">
      <PageHeader
        title="My profile"
        description={
          isAdmin
            ? 'Your details and sign-in. Every change is recorded in the activity log.'
            : 'Your name, phone and sign-in details. Every change is recorded in the activity log.'
        }
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Personal details" />
          <form
            onSubmit={submit(() => saveDetails.mutate(), () => setDetailsError(null))}
            className="space-y-4 p-4 sm:p-5"
            noValidate
          >
            <Field label="Full name" htmlFor="profile_name" required>
              <Input
                id="profile_name"
                autoComplete="name"
                required
                value={details.full_name}
                onChange={(e) => edit({ full_name: e.target.value })}
              />
            </Field>
            <Field label="Phone" htmlFor="profile_phone" hint={MOBILE_HINT}>
              <Input
                id="profile_phone"
                type="tel"
                autoComplete="tel"
                value={details.phone}
                onChange={(e) => edit({ phone: mobileDigits(e.target.value) })}
                placeholder="9876543210"
                inputMode="numeric"
              />
            </Field>

            {isAdmin ? (
              <>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Designation" htmlFor="profile_designation">
                    <Select
                      id="profile_designation"
                      value={details.designation}
                      onChange={(e) => edit({ designation: e.target.value })}
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
                    label="Gender"
                    htmlFor="profile_gender"
                    hint="Decides who may share a room."
                    error={
                      details.gender === '' && user.gender
                        ? `Recorded as "${GENDER_LABELS[user.gender]}". Choose Male or Female.`
                        : null
                    }
                  >
                    <Select
                      id="profile_gender"
                      value={details.gender}
                      onChange={(e) => edit({ gender: e.target.value })}
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
                  <Field
                    label="Department"
                    htmlFor="profile_department"
                    hint="Type a new one to add it."
                    className="sm:col-span-2"
                  >
                    <DepartmentPicker
                      id="profile_department"
                      value={details.department_id}
                      onChange={(department_id) => edit({ department_id })}
                    />
                  </Field>
                  <PlacePicker
                    label="Base"
                    id="profile_base"
                    className="sm:col-span-2"
                    state={details.base_state}
                    city={details.base_location}
                    onChange={({ state, city }) => edit({ base_state: state, base_location: city })}
                  />
                  <Field label="Employee code" htmlFor="profile_code">
                    <Input
                      id="profile_code"
                      value={details.employee_code}
                      onChange={(e) => edit({ employee_code: e.target.value })}
                    />
                  </Field>
                  <Field
                    label="Hotel room occupancy entitlement"
                    htmlFor="profile_occupancy"
                    hint="Controls whether employee gets a single room or double sharing."
                  >
                    <Select
                      id="profile_occupancy"
                      value={details.hotel_occupancy_entitlement}
                      onChange={(e) =>
                        edit({
                          hotel_occupancy_entitlement: e.target.value as 'SINGLE' | 'DOUBLE',
                        })
                      }
                    >
                      <option value="DOUBLE">Double Sharing Room (Default)</option>
                      <option value="SINGLE">Single Occupancy Room</option>
                    </Select>
                  </Field>
                </div>
                <div className="rounded-md bg-surface-sunken px-3 py-3">
                  <ReadOnly label="App access">{ROLE_LABELS[user.role]}</ReadOnly>
                  <p className="mt-2 text-xs text-text-subtle">
                    Nobody changes their own app access. Someone at your level or above can, from Team.
                  </p>
                </div>
              </>
            ) : (
              <div className="rounded-md bg-surface-sunken px-3 py-3">
                <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
                  <ReadOnly label="App access">{ROLE_LABELS[user.role]}</ReadOnly>
                  <ReadOnly label="Designation">
                    {user.designation ? DESIGNATION_LABELS[user.designation] : null}
                  </ReadOnly>
                  <ReadOnly label="Employee code">{user.employee_code}</ReadOnly>
                  <ReadOnly label="Gender">{GENDER_LABELS[user.gender]}</ReadOnly>
                  <ReadOnly label="Department">{user.department_name}</ReadOnly>
                  <ReadOnly label="Hotel occupancy">
                    {user.hotel_occupancy_entitlement === 'SINGLE' ? 'Single Room' : 'Double Sharing'}
                  </ReadOnly>
                  <ReadOnly label="Base">{base}</ReadOnly>
                  {user.manager_name && (
                    <ReadOnly label="Reports to">{user.manager_name}</ReadOnly>
                  )}
                </dl>
                <p className="mt-3 text-xs text-text-subtle">Managed by your administrator.</p>
              </div>
            )}

            {/* Payout & Bank Details */}
            <div className="pt-2 border-t border-border/60">
              <h4 className="text-xs font-semibold text-text uppercase tracking-wider mb-2">
                Reimbursement Payout Details (Receive Money)
              </h4>
              <p className="text-2xs text-text-muted mb-3">
                Your reimbursements will be credited directly to this UPI ID or Bank Account.
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="UPI ID" htmlFor="profile_upi" className="sm:col-span-2">
                  <Input
                    id="profile_upi"
                    value={details.upi_id}
                    onChange={(e) => edit({ upi_id: e.target.value })}
                    placeholder="e.g. name@okhdfcbank"
                  />
                </Field>
                <Field label="Bank Name" htmlFor="profile_bank_name">
                  <Input
                    id="profile_bank_name"
                    value={details.bank_name}
                    onChange={(e) => edit({ bank_name: e.target.value })}
                    placeholder="e.g. HDFC Bank"
                  />
                </Field>
                <Field label="Bank A/c Number" htmlFor="profile_bank_ac">
                  <Input
                    id="profile_bank_ac"
                    value={details.bank_account_number}
                    onChange={(e) => edit({ bank_account_number: e.target.value })}
                    placeholder="e.g. 50100234567890"
                  />
                </Field>
                <Field label="A/c Holder Name" htmlFor="profile_holder">
                  <Input
                    id="profile_holder"
                    value={details.account_holder_name}
                    onChange={(e) => edit({ account_holder_name: e.target.value })}
                    placeholder="e.g. Priya Sharma"
                  />
                </Field>
                <Field label="IFSC Code" htmlFor="profile_ifsc">
                  <Input
                    id="profile_ifsc"
                    value={details.ifsc_code}
                    onChange={(e) => edit({ ifsc_code: e.target.value.toUpperCase() })}
                    placeholder="e.g. HDFC0001234"
                  />
                </Field>
              </div>
            </div>

            <ErrorBox message={detailsError} />

            <div className="flex justify-end gap-2">
              {dirty && (
                <Button type="button" variant="secondary" onClick={() => setDraft(null)}>
                  Undo
                </Button>
              )}
              <Button type="submit" disabled={!dirty} loading={saveDetails.isPending}>
                <UserRound size={15} />
                Save changes
              </Button>
            </div>
          </form>
        </Card>

        <Card>
          <CardHeader
            title="Sign-in email"
            description="You sign in with this address and password-reset links go to it."
          />
          <form
            onSubmit={submit(() => saveEmail.mutate(), () => setEmailError(null))}
            className="space-y-4 p-4 sm:p-5"
            noValidate
          >
            <ReadOnly label="Current email">{user.email}</ReadOnly>
            <Field label="New email" htmlFor="profile_email" required>
              <Input
                id="profile_email"
                type="email"
                autoComplete="email"
                required
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                placeholder="you@designboxed.com"
              />
            </Field>
            <Field
              label="Current password"
              htmlFor="profile_email_password"
              required
              hint="Needed so nobody else can move your account to their address."
            >
              <Input
                id="profile_email_password"
                type="password"
                autoComplete="current-password"
                required
                value={emailPassword}
                onChange={(e) => setEmailPassword(e.target.value)}
              />
            </Field>

            <ErrorBox message={emailError} />

            <div className="flex justify-end">
              <Button
                type="submit"
                disabled={!newEmail.trim() || !emailPassword}
                loading={saveEmail.isPending}
              >
                <Mail size={15} />
                Change email
              </Button>
            </div>
          </form>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader
            title="Password"
            description={
              user.password_changed_at
                ? `Last changed ${formatInstant(user.password_changed_at)}. Changing it signs you out on your other devices.`
                : 'Changing it signs you out on your other devices.'
            }
          />
          <form
            onSubmit={submit(() => savePassword.mutate(), () => setPasswordError(null))}
            className="grid gap-4 p-4 sm:grid-cols-3 sm:p-5"
            noValidate
          >
            <Field label="Current password" htmlFor="profile_current" required>
              <Input
                id="profile_current"
                type="password"
                autoComplete="current-password"
                required
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
              />
            </Field>
            <Field label="New password" htmlFor="profile_new" required>
              <Input
                id="profile_new"
                type="password"
                autoComplete="new-password"
                required
                value={next}
                onChange={(e) => setNext(e.target.value)}
              />
            </Field>
            <Field
              label="Confirm new password"
              htmlFor="profile_confirm"
              required
              error={mismatch ? 'Passwords do not match.' : null}
            >
              <Input
                id="profile_confirm"
                type="password"
                autoComplete="new-password"
                required
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                aria-invalid={mismatch}
              />
            </Field>

            <div className="space-y-4 sm:col-span-3">
              <PasswordChecklist password={next} issues={issues} />
              <ErrorBox message={passwordError} />
              <div className="flex justify-end">
                <Button type="submit" disabled={!passwordReady} loading={savePassword.isPending}>
                  <KeyRound size={15} />
                  Change password
                </Button>
              </div>
            </div>
          </form>
        </Card>
      </div>
    </div>
  );
}
