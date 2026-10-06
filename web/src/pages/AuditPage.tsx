import { useQuery } from '@tanstack/react-query';
import {
  ChevronDown,
  Download,
  Lock,
  ScrollText,
  ShieldAlert,
  ShieldCheck,
  Unlock,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import {
  type Accent,
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ItemCard,
  ItemList,
  ItemNumber,
  Select,
  Skeleton,
} from '@/components/ui';
import {
  errorMessage,
  exportAudit,
  fetchAudit,
  fetchAuditSummary,
  fetchHealth,
  fetchLedgerGrants,
  verifyAuditChain,
} from '@/lib/api';
import { formatInstant, formatInstantDate, todayInIndia } from '@/lib/time';
import { cn } from '@/lib/utils';
import type { AuditRow } from '@/types';

// Sign-ins, sign-outs and failed sign-ins are recorded but not listed here.
const ACTIONS = [
  'CREATE', 'UPDATE', 'DELETE',
  'SUBMIT', 'APPROVE', 'REJECT', 'CANCEL', 'BOOK', 'UPLOAD',
  'EXTRACT', 'NOTIFY', 'OVERRIDE_CONFLICT', 'RECOMMEND', 'VIEW_SENSITIVE', 'EXPORT',
];

function actionTone(action: string): Accent {
  if (action === 'REJECT' || action === 'DELETE') return 'danger';
  if (action === 'APPROVE' || action === 'BOOK' || action === 'CREATE') return 'success';
  if (action === 'OVERRIDE_CONFLICT' || action === 'VIEW_SENSITIVE' || action === 'EXPORT') return 'warning';
  return 'neutral';
}

function timestamp(iso: string) {
  // With seconds, which an audit trail needs; 12-hour like every other time.
  return formatInstant(iso, {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function Row({ entry }: { entry: AuditRow }) {
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(entry.changes || entry.reason || entry.ip_address);

  return (
    <ItemCard accent={actionTone(entry.action)}>
      <div className="flex items-start gap-3">
        {/* On a phone the time and the action get a line of their own above the
            summary; from sm up they are its two leading columns. */}
        <div className="flex min-w-0 flex-1 flex-col gap-1.5 sm:flex-row sm:items-start sm:gap-3">
          <div className="flex flex-wrap items-center gap-2 sm:contents">
            <span className="shrink-0 font-mono text-2xs text-text-subtle sm:w-32 sm:pt-0.5">
              {timestamp(entry.created_at)}
            </span>

            <Badge tone={actionTone(entry.action) as never} className="mt-px shrink-0">
              {entry.action.replace(/_/g, ' ').toLowerCase()}
            </Badge>
          </div>

          <div className="min-w-0 flex-1">
            {/* The number the integrity banner names if the chain ever breaks. */}
            <p className="text-sm leading-snug">
              <ItemNumber value={entry.id} className="mr-1.5" />
              {entry.summary}
            </p>
            <p className="mt-0.5 text-2xs text-text-subtle">
              {entry.actor_name ?? 'System'}
              {entry.actor_email ? ` · ${entry.actor_email}` : ''}
              {` · ${entry.entity_type.replace(/_/g, ' ')}${entry.entity_id ? ` ${entry.entity_id}` : ''}`}
            </p>

            {open && hasDetail && (
              <div className="mt-2.5 space-y-2 rounded-md bg-surface-sunken p-3">
                {entry.changes && (
                  <dl className="space-y-1">
                    {Object.entries(entry.changes).map(([field, change]) => (
                      <div key={field} className="flex flex-wrap items-baseline gap-2 text-xs">
                        <dt className="font-medium">{field}</dt>
                        <dd className="font-mono text-text-muted">
                          <span className="text-danger">{String(change.from ?? '—')}</span>
                          {' → '}
                          <span className="text-success">{String(change.to ?? '—')}</span>
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
                {entry.reason && (
                  <p className="text-xs">
                    <span className="font-medium">Reason: </span>
                    <span className="text-text-muted">{entry.reason}</span>
                  </p>
                )}
                {entry.ip_address && (
                  <p className="font-mono text-2xs text-text-subtle">from {entry.ip_address}</p>
                )}
              </div>
            )}
          </div>
        </div>

        {hasDetail && (
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            aria-label={open ? 'Hide detail' : 'Show detail'}
            className="shrink-0 rounded p-1 text-text-subtle hover:bg-surface-sunken hover:text-text"
          >
            <ChevronDown size={14} className={cn('transition-transform', open && 'rotate-180')} />
          </button>
        )}
      </div>
    </ItemCard>
  );
}

export default function AuditPage() {
  const [action, setAction] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 50;

  const audit = useQuery({
    queryKey: ['audit', action, page],
    queryFn: () => fetchAudit({ action: action || undefined, page, page_size: pageSize }),
  });

  const chain = useQuery({ queryKey: ['audit', 'verify'], queryFn: verifyAuditChain });

  // The other half of section 7's "immutable": the chain makes tampering
  // detectable, the database grant makes it impossible. Both are shown, because
  // either alone leaves a gap.
  const grants = useQuery({ queryKey: ['audit', 'grants'], queryFn: fetchLedgerGrants });
  // The same probe the shell runs; only its environment is read here.
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth, staleTime: 5 * 60 * 1000 });
  const production = health.data?.environment === 'production';
  const summary = useQuery({ queryKey: ['audit', 'summary'], queryFn: fetchAuditSummary });

  const download = useMutation({
    mutationFn: () => exportAudit({ action: action || undefined, limit: 5000 }),
    onSuccess: (blob) => {
      // Through a blob rather than a link: the endpoint needs the bearer token,
      // and the export writes its own VIEW_SENSITIVE row.
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `activity-log-${todayInIndia()}.csv`;
      anchor.click();
      URL.revokeObjectURL(url);
      toast.success('Activity log exported — the download is recorded in the log');
      chain.refetch();
    },
    meta: { errorFallback: 'Could not export the activity log.' },
  });

  const total = audit.data?.total ?? 0;
  const lastPage = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Activity log</h1>
        <p className="mt-1.5 text-sm text-text-muted">
          Every action taken in the system, append-only and hash-chained. Nothing here can be
          edited or removed without the chain reporting it.
        </p>
      </div>

      {/* Integrity banner. This is what makes the SOW's "immutable" a claim you
          can check rather than one you have to trust. */}
      {chain.data && (
        <div
          className={cn(
            'flex items-start gap-2.5 rounded-lg border px-4 py-3 text-sm',
            chain.data.ok
              ? 'border-border bg-success-soft/50 text-success'
              : 'border-danger bg-danger-soft text-danger',
          )}
        >
          {chain.data.ok ? (
            <ShieldCheck size={16} className="mt-px shrink-0" />
          ) : (
            <ShieldAlert size={16} className="mt-px shrink-0" />
          )}
          <span>
            {chain.data.ok
              ? `Chain intact across ${chain.data.checked} entries.`
              : `Tampering detected at entry ${chain.data.broken_at_id}: ${chain.data.detail}`}
          </span>
        </div>
      )}

      {grants.data && (
        <div
          className={cn(
            'flex items-start gap-2.5 rounded-lg border px-4 py-3',
            grants.data.append_only
              ? 'border-success/40 bg-success-soft'
              : 'border-warning/40 bg-warning-soft',
          )}
        >
          {grants.data.append_only ? (
            <Lock size={15} className="mt-0.5 shrink-0 text-success" />
          ) : (
            <Unlock size={15} className="mt-0.5 shrink-0 text-warning" />
          )}
          <div className="min-w-0">
            <p
              className={cn(
                'text-xs font-semibold',
                grants.data.append_only ? 'text-success' : 'text-warning',
              )}
            >
              {grants.data.append_only
                ? 'The database refuses to modify or remove ledger rows'
                : 'This database user can still rewrite the ledger'}
            </p>
            <p className="mt-0.5 text-xs text-text-muted">{grants.data.detail}</p>
            {!grants.data.append_only && (
              <>
                <p className="mt-1.5 text-xs text-text-muted">
                  {production
                    ? 'Before relying on this log, run scripts/grant_append_only.py and point DATABASE_URL at the restricted database user it creates.'
                    : 'Expected on a development database: the API signs in to MySQL as root, which can change any table. Nothing is wrong and nothing has been changed. Before going live, run scripts/grant_append_only.py and point DATABASE_URL at the restricted user it creates (README, "Before deploying").'}
                </p>
                <p className="mt-1 text-2xs text-text-subtle">
                  The hash chain still makes tampering <em>detectable</em>. The grant makes it
                  impossible — see addendum B9.
                </p>
              </>
            )}
          </div>
        </div>
      )}

      <Card>
        <CardHeader
          title={`${total} ${total === 1 ? 'entry' : 'entries'}`}
          description={
            summary.data?.oldest
              ? `Covering ${formatInstantDate(summary.data.oldest)} to today. Sign-ins are not listed. Times are India time (IST).`
              : 'Sign-ins are not listed. Times are India time (IST).'
          }
          action={
            <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              loading={download.isPending}
              onClick={() => download.mutate()}
              title="Download the filtered log as CSV"
            >
              <Download size={13} />
              Export
            </Button>
            <Select
              value={action}
              onChange={(e) => {
                setAction(e.target.value);
                setPage(1);
              }}
              aria-label="Filter by action"
              className="w-48"
            >
              <option value="">All actions</option>
              {ACTIONS.map((a) => (
                <option key={a} value={a}>
                  {a.replace(/_/g, ' ').toLowerCase()}
                  {summary.data?.by_action[a] ? ` (${summary.data.by_action[a]})` : ''}
                </option>
              ))}
            </Select>
            </div>
          }
        />

        {audit.isPending ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : audit.isError ? (
          <EmptyState
            icon={<ScrollText size={28} />}
            title="Could not load the log"
            description={errorMessage(audit.error)}
          />
        ) : total === 0 ? (
          <EmptyState
            icon={<ScrollText size={28} />}
            title="Nothing recorded yet"
            description="Actions appear here as soon as anyone uses the system."
          />
        ) : (
          <>
            <ItemList className={lastPage > 1 ? undefined : 'rounded-b-xl'}>
              {audit.data.items.map((entry) => (
                <Row key={entry.id} entry={entry} />
              ))}
            </ItemList>

            {lastPage > 1 && (
              <div className="flex items-center justify-between border-t border-border px-5 py-3">
                <span className="text-xs text-text-muted">
                  Page {page} of {lastPage}
                </span>
                <div className="flex gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={page === 1}
                    onClick={() => setPage(page - 1)}
                  >
                    Previous
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={page >= lastPage}
                    onClick={() => setPage(page + 1)}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  );
}
