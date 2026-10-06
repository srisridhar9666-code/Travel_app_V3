import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Bell,
  CheckCheck,
  Clock,
  Mail,
  Play,
  RefreshCw,
  Send,
} from 'lucide-react';
import { useState } from 'react';
import toast from 'react-hot-toast';
import { useSearchParams } from 'react-router-dom';

import EmailDeliveryCard from '@/components/EmailDeliveryCard';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Input,
  ItemCard,
  ItemList,
  Select,
  Skeleton,
  ZEBRA_ROWS,
} from '@/components/ui';
import {
  fetchLedger,
  fetchMyNotices,
  fetchPreferences,
  fetchSchedulerStatus,
  markAllRead,
  retryFailedEmail,
  runReminderJobs,
  setPreference,
} from '@/lib/api';
import { formatInstant } from '@/lib/time';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import {
  CATEGORY_HINTS,
  CATEGORY_LABELS,
  JOB_LABELS,
  NOTIFICATION_STATUS_LABELS,
  type NotificationCategory,
  type NotificationStatus,
  isAdminRole,
} from '@/types';

const STATUS_TONE: Record<NotificationStatus, 'neutral' | 'success' | 'danger' | 'warning'> = {
  QUEUED: 'warning',
  SENT: 'success',
  FAILED: 'danger',
  READ: 'neutral',
  SUPPRESSED: 'neutral',
};

const when = (iso: string) =>
  formatInstant(iso, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });

/** Everyone's own inbox. */
function Inbox() {
  const queryClient = useQueryClient();
  const notices = useQuery({ queryKey: ['my-notices'], queryFn: fetchMyNotices });

  const readAll = useMutation({
    mutationFn: markAllRead,
    meta: { errorFallback: 'Could not mark the notifications read.' },
    onSuccess: () => {
      toast.success('All notifications marked read');
      queryClient.invalidateQueries({ queryKey: ['my-notices'] });
      queryClient.invalidateQueries({ queryKey: ['unread-count'] });
    },
  });

  const rows = notices.data ?? [];
  const unread = rows.filter((n) => n.read_at === null).length;

  return (
    <Card>
      <CardHeader
        title={`${rows.length} ${rows.length === 1 ? 'notice' : 'notices'}`}
        description="Everything the system has told you."
        action={
          unread > 0 ? (
            <Button
              variant="secondary"
              size="sm"
              loading={readAll.isPending}
              onClick={() => readAll.mutate()}
            >
              <CheckCheck size={13} />
              Mark all read
            </Button>
          ) : undefined
        }
      />
      {notices.isPending ? (
        <div className="space-y-2 p-5">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<Bell size={28} />}
          title="Nothing yet"
          description="Decisions on your requests, booking confirmations and reminders will appear here."
        />
      ) : (
        // One card per notice; an unread one carries the brand edge beside its dot.
        <ItemList className="rounded-b-xl">
          {rows.map((notice) => (
            <ItemCard key={notice.id} accent={notice.read_at === null ? 'brand' : 'neutral'}>
              <div className="flex flex-wrap items-baseline gap-x-2">
                {notice.read_at === null && (
                  <span className="h-1.5 w-1.5 rounded-full bg-brand" aria-label="Unread" />
                )}
                <span className="text-sm font-medium">{notice.title}</span>
                <Badge tone="neutral">
                  {CATEGORY_LABELS[notice.category as NotificationCategory] ?? notice.category}
                </Badge>
                <span className="text-2xs text-text-subtle">{when(notice.created_at)}</span>
              </div>
              <p className="mt-1 text-xs leading-relaxed text-text-muted">{notice.body}</p>
            </ItemCard>
          ))}
        </ItemList>
      )}
    </Card>
  );
}

/** Which categories of email this person still wants. */
function Preferences() {
  const queryClient = useQueryClient();
  const prefs = useQuery({ queryKey: ['preferences'], queryFn: fetchPreferences });

  const save = useMutation({
    mutationFn: (vars: { category: string; enabled: boolean }) =>
      setPreference(vars.category, vars.enabled),
    onSuccess: (data, vars) => {
      queryClient.setQueryData(['preferences'], data);
      toast.success(
        vars.enabled
          ? `${CATEGORY_LABELS[vars.category as NotificationCategory]} email on`
          : `${CATEGORY_LABELS[vars.category as NotificationCategory]} email off`,
      );
    },
  });

  const entries = Object.entries(prefs.data?.email ?? {});

  return (
    <Card>
      <CardHeader
        title="Email preferences"
        description="Everything still appears in your notifications here — this only controls email."
      />
      <div className="divide-y divide-border">
        {/* Decisions are listed but not switchable, because a toggle that does
            nothing is worse than an honest explanation of why there isn't one. */}
        <div className="flex items-start justify-between gap-4 px-5 py-3 opacity-70">
          <div className="min-w-0">
            <p className="text-sm font-medium">{CATEGORY_LABELS.DECISIONS}</p>
            <p className="mt-0.5 text-xs text-text-muted">{CATEGORY_HINTS.DECISIONS}</p>
          </div>
          <Badge tone="neutral">Always on</Badge>
        </div>

        {prefs.isPending
          ? Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="px-5 py-3">
                <Skeleton className="h-8 w-full" />
              </div>
            ))
          : entries.map(([category, enabled]) => (
              <label
                key={category}
                className="flex cursor-pointer items-start justify-between gap-4 px-5 py-3 hover:bg-surface-sunken/60"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium">
                    {CATEGORY_LABELS[category as NotificationCategory] ?? category}
                  </p>
                  <p className="mt-0.5 text-xs text-text-muted">
                    {CATEGORY_HINTS[category as NotificationCategory]}
                  </p>
                </div>
                <input
                  type="checkbox"
                  checked={enabled}
                  disabled={save.isPending}
                  onChange={(e) => save.mutate({ category, enabled: e.target.checked })}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-[rgb(var(--primary))]"
                  aria-label={`Email me about ${CATEGORY_LABELS[category as NotificationCategory]}`}
                />
              </label>
            ))}
      </div>
    </Card>
  );
}

/** The admin view: did it arrive, and can we make it arrive. */
function Ledger() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState('');
  const [channelFilter, setChannelFilter] = useState('');
  const [search, setSearch] = useState('');

  const ledger = useQuery({
    queryKey: ['ledger', statusFilter, channelFilter, search],
    queryFn: () =>
      fetchLedger({
        status: statusFilter || undefined,
        channel: channelFilter || undefined,
        search: search.trim() || undefined,
        page_size: 100,
      }),
  });
  const scheduler = useQuery({ queryKey: ['scheduler'], queryFn: fetchSchedulerStatus });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['ledger'] });
    queryClient.invalidateQueries({ queryKey: ['scheduler'] });
    queryClient.invalidateQueries({ queryKey: ['unread-count'] });
    queryClient.invalidateQueries({ queryKey: ['my-notices'] });
  };

  const retry = useMutation({
    mutationFn: retryFailedEmail,
    onSuccess: (r) => {
      toast.success(
        r.attempted === 0 ? 'Nothing was waiting to retry' : `${r.sent} of ${r.attempted} sent`,
      );
      refresh();
    },
  });

  const runJobs = useMutation({
    mutationFn: runReminderJobs,
    onSuccess: (results) => {
      const total = results.reduce((sum, r) => sum + (r.notified ?? 0) + (r.sent ?? 0), 0);
      toast.success(total === 0 ? 'Nothing needed sending' : `${total} notice(s) sent`);
      refresh();
    },
  });

  const rows = ledger.data?.items ?? [];
  const summary = ledger.data?.summary;
  const status = scheduler.data;

  return (
    <div className="space-y-4">
      <EmailDeliveryCard />

      <Card>
        <CardHeader
          title="Scheduled reminders"
          description="Travel nudges, stale-request chases and email retries. Every notice is deduplicated by event, so running these twice sends nothing twice."
        />
        <div className="flex flex-wrap items-center gap-4 px-5 py-4">
          {scheduler.isPending ? (
            <Skeleton className="h-8 w-64" />
          ) : (
            <>
              <div className="flex items-center gap-2 text-xs">
                <Clock size={14} className="text-text-subtle" />
                {status?.enabled ? (
                  <span className="text-text-muted">
                    Running every{' '}
                    <span className="font-medium text-text">{status.interval_minutes} min</span>
                  </span>
                ) : (
                  <span className="text-text-muted">
                    Background loop is <span className="font-medium text-text">off</span>
                  </span>
                )}
              </div>
              <span className="text-2xs text-text-subtle">
                Travellers reminded {status?.travel_reminder_days}d ahead · requests chased after{' '}
                {status?.stale_after_days}d
              </span>
              {(status?.failed_email ?? 0) > 0 && (
                <span className="inline-flex items-center gap-1 text-2xs text-danger">
                  <AlertTriangle size={11} />
                  {status?.failed_email} email awaiting retry
                </span>
              )}
              <div className="ml-auto flex gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  loading={runJobs.isPending}
                  onClick={() => runJobs.mutate()}
                >
                  <Play size={13} />
                  Run now
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  loading={retry.isPending}
                  onClick={() => retry.mutate()}
                >
                  <RefreshCw size={13} />
                  Retry failed email
                </Button>
              </div>
            </>
          )}
        </div>
        {runJobs.data && (
          <div className="border-t border-border px-5 py-3">
            <ul className="space-y-1">
              {runJobs.data.map((result) => (
                <li key={result.job} className="text-2xs text-text-muted">
                  <span className="font-medium text-text">
                    {JOB_LABELS[result.job] ?? result.job}
                  </span>
                  {result.error
                    ? ` — failed: ${result.error}`
                    : ` — ${result.notified ?? result.sent ?? 0} sent`}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title={`${ledger.data?.total ?? 0} in the delivery ledger`}
          description="Who was told what, on which channel, and whether it arrived. “Not sent” means email was off or held back by EMAIL_ALLOWLIST when it was due; those are never retried."
        />

        {summary && (
          <div className="flex flex-wrap gap-4 border-b border-border px-5 py-3 text-2xs">
            <span className="text-text-muted">
              <span className="font-semibold text-text">{summary.emails}</span> email
            </span>
            {Object.entries(summary.by_status)
              .filter(([, n]) => n > 0)
              .map(([key, n]) => (
                <span key={key} className="text-text-muted">
                  <span className="font-semibold text-text">{n}</span>{' '}
                  {NOTIFICATION_STATUS_LABELS[key as NotificationStatus]?.toLowerCase() ?? key}
                </span>
              ))}
          </div>
        )}

        <div className="flex flex-wrap gap-2 border-b border-border px-5 py-3">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search address or subject"
            className="min-w-40 flex-1 sm:max-w-64"
            aria-label="Search the ledger"
          />
          <Select
            value={channelFilter}
            onChange={(e) => setChannelFilter(e.target.value)}
            aria-label="Filter by channel"
            className="w-[calc(50%-0.25rem)] sm:w-36"
          >
            <option value="">All channels</option>
            <option value="EMAIL">Email</option>
            <option value="IN_APP">In app</option>
          </Select>
          <Select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Filter by status"
            className="w-[calc(50%-0.25rem)] sm:w-36"
          >
            <option value="">All statuses</option>
            {(Object.keys(NOTIFICATION_STATUS_LABELS) as NotificationStatus[]).map((s) => (
              <option key={s} value={s}>
                {NOTIFICATION_STATUS_LABELS[s]}
              </option>
            ))}
          </Select>
        </div>

        {ledger.isPending ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState icon={<Mail size={28} />} title="Nothing matches that" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-2xs uppercase tracking-widest text-text-subtle">
                  <th className="px-5 py-2.5 font-semibold">Who</th>
                  <th className="px-5 py-2.5 font-semibold">What</th>
                  <th className="hidden px-5 py-2.5 font-semibold md:table-cell">Channel</th>
                  <th className="px-5 py-2.5 font-semibold">Delivery</th>
                  <th className="hidden px-5 py-2.5 font-semibold lg:table-cell">When</th>
                </tr>
              </thead>
              <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                {rows.map((row) => (
                  // Important, or the zebra band would swallow the hover on even rows.
                  <tr key={row.id} className="transition-colors hover:!bg-surface-sunken">
                    <td className="px-5 py-2.5">
                      <div className="font-medium">{row.user_name ?? `User ${row.user_id}`}</div>
                      {row.to_address && (
                        <div className="text-2xs text-text-subtle">{row.to_address}</div>
                      )}
                      {row.cc_addresses && (
                        <div className="text-2xs text-text-subtle">Cc {row.cc_addresses}</div>
                      )}
                    </td>
                    <td className="px-5 py-2.5">
                      <div className="text-xs">{row.subject ?? row.title}</div>
                      <div className="text-2xs text-text-subtle">
                        {CATEGORY_LABELS[row.category as NotificationCategory] ?? row.category}
                      </div>
                    </td>
                    <td className="hidden px-5 py-2.5 text-xs text-text-muted md:table-cell">
                      {row.channel === 'EMAIL' ? 'Email' : 'In app'}
                    </td>
                    <td className="px-5 py-2.5">
                      <Badge tone={STATUS_TONE[row.status]}>
                        {NOTIFICATION_STATUS_LABELS[row.status]}
                      </Badge>
                      {row.last_error && (
                        <div className="mt-1 max-w-80 whitespace-pre-wrap break-words text-2xs text-text-subtle">
                          {row.last_error}
                        </div>
                      )}
                      {row.attempts > 1 && (
                        <div className="text-2xs text-text-subtle">{row.attempts} attempts</div>
                      )}
                    </td>
                    <td className="hidden px-5 py-2.5 text-2xs text-text-muted lg:table-cell">
                      {when(row.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

export default function NotificationsPage() {
  const user = useAuth((s) => s.user);
  const isAdmin = isAdminRole(user?.role);
  // The tab is in the address, so "Notifications > Delivery ledger" can be
  // linked to directly.
  const [params, setParams] = useSearchParams();
  type Tab = 'inbox' | 'settings' | 'ledger';
  const asked = params.get('tab') as Tab | null;
  const tab: Tab =
    asked === 'settings' || (asked === 'ledger' && isAdmin) ? asked : 'inbox';
  const setTab = (next: Tab) =>
    setParams(next === 'inbox' ? {} : { tab: next }, { replace: true });

  const tabs: { key: Tab; label: string; icon: typeof Bell }[] = [
    { key: 'inbox', label: 'Inbox', icon: Bell },
    { key: 'settings', label: 'Email settings', icon: Mail },
    ...(isAdmin ? [{ key: 'ledger' as const, label: 'Delivery ledger', icon: Send }] : []),
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Notifications</h1>
        <p className="mt-1.5 max-w-2xl text-sm text-text-muted">
          Everything the system has told you, and which of it also reaches your inbox.
          {isAdmin && ' Admins can see whether it actually arrived.'}
        </p>
      </div>

      <div className="flex flex-wrap gap-1 border-b border-border">
        {tabs.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            aria-pressed={tab === key}
            className={cn(
              '-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs transition-colors',
              tab === key
                ? 'border-primary font-medium text-text'
                : 'border-transparent text-text-muted hover:text-text',
            )}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}
      </div>

      {tab === 'inbox' && <Inbox />}
      {tab === 'settings' && <Preferences />}
      {tab === 'ledger' && isAdmin && <Ledger />}
    </div>
  );
}
