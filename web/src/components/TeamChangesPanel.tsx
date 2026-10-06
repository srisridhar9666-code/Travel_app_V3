import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, UserMinus, UserPen, UserPlus, X } from 'lucide-react';
import { useState } from 'react';
import toast from 'react-hot-toast';

import { EmailLinkChoice } from '@/components/EmailLinkChoice';
import { Modal } from '@/components/Modal';
import { Badge, Button, Card, CardHeader, Field, ItemCard, ItemList, ItemNumber } from '@/components/ui';
import { approveTeamChange, errorMessage, fetchTeamChanges, rejectTeamChange } from '@/lib/api';
import { formatInstantDate } from '@/lib/time';
import {
  DESIGNATION_LABELS,
  GENDER_LABELS,
  type Designation,
  type Gender,
  type InviteLink,
  type TeamChange,
  type TeamChangeKind,
} from '@/types';

export const CHANGE_KIND: Record<
  TeamChangeKind,
  { label: string; verb: string; icon: typeof UserPlus; tone: 'success' | 'info' | 'warning' }
> = {
  ADD: { label: 'Add', verb: 'add', icon: UserPlus, tone: 'success' },
  EDIT: { label: 'Edit', verb: 'change the details of', icon: UserPen, tone: 'info' },
  REMOVE: { label: 'Remove', verb: 'remove', icon: UserMinus, tone: 'warning' },
};

const FIELD_LABELS: Record<string, string> = {
  full_name: 'Name',
  email: 'Email',
  gender: 'Gender',
  designation: 'Designation',
  phone: 'Phone',
  employee_code: 'Employee code',
  base_state: 'State',
  base_location: 'Base',
};

function show(field: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (field === 'designation') return DESIGNATION_LABELS[value as Designation] ?? String(value);
  if (field === 'gender') return GENDER_LABELS[value as Gender] ?? String(value);
  return String(value);
}

/** Exactly what the manager asked for, in words. */
export function ChangeDetails({ change }: { change: TeamChange }) {
  const p = change.payload;
  if (change.kind === 'EDIT') {
    return (
      <ul className="space-y-0.5 text-xs text-text-muted">
        {Object.entries(p as Record<string, { from: unknown; to: unknown }>).map(([field, v]) => (
          <li key={field}>
            <span className="font-medium text-text">{FIELD_LABELS[field] ?? field}:</span>{' '}
            <span className="line-through">{show(field, v.from)}</span> → {show(field, v.to)}
          </li>
        ))}
      </ul>
    );
  }
  if (change.kind === 'REMOVE') {
    return (
      <p className="text-xs text-text-muted">
        Mark as <span className="font-medium text-text">{p.status === 'LEFT' ? 'left' : 'deactivated'}</span>
        {p.exited_on ? ` from ${formatInstantDate(String(p.exited_on))}` : ''} - {String(p.reason ?? '')}
      </p>
    );
  }
  const shown = ['email', 'gender', 'designation', 'phone', 'employee_code', 'base_location', 'base_state'];
  return (
    <p className="text-xs text-text-muted">
      {shown
        .filter((field) => p[field])
        .map((field) => `${FIELD_LABELS[field]}: ${show(field, p[field])}`)
        .join(' · ')}
    </p>
  );
}

/**
 * Managers' asks to add, edit or remove the people who report to them, waiting
 * on an admin. Approving makes the change exactly as asked; rejecting needs a
 * reason. The manager is told either way, with the comment.
 */
export function TeamChangesPanel({
  onInvite,
}: {
  /** An approved addition was invited: show its link to copy. */
  onInvite: (name: string, result: InviteLink, byHand: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const pending = useQuery({
    queryKey: ['team-changes', 'PENDING'],
    queryFn: () => fetchTeamChanges('PENDING'),
  });
  const [deciding, setDeciding] = useState<{ change: TeamChange; approve: boolean } | null>(null);
  const [comment, setComment] = useState('');
  const [byEmail, setByEmail] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const decide = useMutation({
    mutationFn: () =>
      deciding!.approve
        ? approveTeamChange(deciding!.change.id, comment, byEmail)
        : rejectTeamChange(deciding!.change.id, comment.trim()),
    onSuccess: (result) => {
      const { change } = result;
      toast.success(
        change.status === 'APPROVED'
          ? `Approved - ${change.requested_by_name ?? 'the manager'} has been told`
          : `Rejected - ${change.requested_by_name ?? 'the manager'} has been told why`,
      );
      if (result.invite) onInvite(change.target_name ?? 'them', result.invite, !byEmail);
      setDeciding(null);
      queryClient.invalidateQueries({ queryKey: ['team-changes'] });
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (err) => setError(errorMessage(err, 'Could not record that decision.')),
  });

  const items = pending.data?.items ?? [];
  if (items.length === 0) return null;

  const open = (change: TeamChange, approve: boolean) => {
    setDeciding({ change, approve });
    setComment('');
    setByEmail(true);
    setError(null);
  };

  const rejecting = deciding && !deciding.approve;
  const tooShort = rejecting && comment.trim().length < 3;

  return (
    <Card>
      <CardHeader
        title={`Manager requests · ${items.length} waiting`}
        description="Managers ask to add, edit or remove the people who report to them. Nothing changes until you approve."
      />
      <ItemList className="rounded-b-xl">
        {items.map((change) => {
          const kind = CHANGE_KIND[change.kind];
          const Icon = kind.icon;
          // Every ask here is waiting on an admin: the same reading as the
          // manager's own "Waiting for an admin" badge.
          return (
            <ItemCard key={change.id} accent="warning" className="flex flex-wrap items-start gap-3">
              <div className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-surface-sunken text-text-muted">
                <Icon size={15} />
              </div>
              {/* At least 10rem: on a phone the buttons drop below instead of
                  squeezing the ask into a sliver beside them. */}
              <div className="min-w-0 flex-1 basis-40 space-y-1">
                <p className="text-sm">
                  <ItemNumber value={change.id} className="mr-1.5 align-middle" />
                  <Badge tone={kind.tone} className="mr-1.5 align-middle">
                    {kind.label}
                  </Badge>
                  <span className="font-medium">{change.requested_by_name}</span> asks to {kind.verb}{' '}
                  <span className="font-medium">{change.target_name}</span>
                </p>
                <ChangeDetails change={change} />
                {change.note && <p className="text-xs italic text-text-muted">“{change.note}”</p>}
                <p className="text-2xs text-text-subtle">Asked {formatInstantDate(change.created_at)}</p>
              </div>
              <div className="flex gap-1.5">
                <Button size="sm" onClick={() => open(change, true)}>
                  <Check size={13} />
                  Approve
                </Button>
                <Button size="sm" variant="secondary" onClick={() => open(change, false)}>
                  <X size={13} />
                  Reject
                </Button>
              </div>
            </ItemCard>
          );
        })}
      </ItemList>

      <Modal
        open={deciding !== null}
        onClose={() => setDeciding(null)}
        title={deciding?.approve ? 'Approve this change?' : 'Reject this change?'}
        description={
          deciding
            ? `${deciding.change.requested_by_name} asked to ${CHANGE_KIND[deciding.change.kind].verb} ${deciding.change.target_name}.`
            : undefined
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeciding(null)}>
              Cancel
            </Button>
            <Button
              variant={deciding?.approve ? 'primary' : 'danger'}
              loading={decide.isPending}
              disabled={Boolean(tooShort)}
              onClick={() => decide.mutate()}
            >
              {deciding?.approve ? 'Approve' : 'Reject'}
            </Button>
          </>
        }
      >
        {deciding && (
          <div className="space-y-4">
            <div className="rounded-md bg-surface-sunken px-3 py-2.5">
              <ChangeDetails change={deciding.change} />
            </div>
            <Field
              label={deciding.approve ? 'Comment for the manager (optional)' : 'Why not? The manager reads this.'}
              htmlFor="team_change_comment"
              required={!deciding.approve}
            >
              <textarea
                id="team_change_comment"
                rows={2}
                maxLength={500}
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder={deciding.approve ? 'Welcome aboard' : 'Hiring is on hold this quarter'}
                className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text transition-colors placeholder:text-text-subtle hover:border-border-strong"
              />
            </Field>
            {deciding.approve && deciding.change.kind === 'ADD' && (
              <>
                <p className="text-xs text-text-muted">
                  They join as ground staff in {deciding.change.requested_by_name}'s department,
                  reporting to them.
                </p>
                <EmailLinkChoice
                  name={deciding.change.target_name ?? ''}
                  checked={byEmail}
                  onChange={setByEmail}
                />
              </>
            )}
            {error && <p className="text-xs text-danger">{error}</p>}
          </div>
        )}
      </Modal>
    </Card>
  );
}
