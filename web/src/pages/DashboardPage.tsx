import { keepPreviousData, useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  BedDouble,
  Building2,
  CalendarCheck,
  Car,
  CheckCircle2,
  CheckSquare,
  ClipboardCheck,
  Clock,
  FileCheck,
  FolderKanban,
  History,
  IndianRupee,
  MapPin,
  Moon,
  Plane,
  Receipt,
  ShieldCheck,
  Store,
  Users,
  UsersRound,
  Wallet,
} from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import type { RangePreset } from '@/components/DateRangePicker';
import { EmailProblemBanner } from '@/components/EmailDeliveryCard';
import {
  ReportFilterBar,
  departmentChange,
  stateChange,
  useReportFilters,
} from '@/components/ReportFilters';
import { TravelHistoryPanel } from '@/components/TravelHistoryPanel';
import { Columns, HorizontalBars, StatTile, formatMoney } from '@/components/charts';
import { Badge, Button, Card, CardHeader, PageHeader, Skeleton, ZEBRA_ROWS } from '@/components/ui';
import {
  fetchExpenseClaims,
  fetchFilterOptions,
  fetchInsights,
  fetchInvoices,
  fetchMyTeam,
  fetchQueueCounts,
  fetchRequests,
  fetchTeamReviews,
} from '@/lib/api';
import { periodLabel } from '@/lib/time';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import {
  REQUEST_TYPE_LABELS,
  TRAVELLER_STATUS_LABELS,
  TRAVEL_MODE_LABELS,
  type Insights,
  type TravelMode,
  type TravellerStatus,
  type UserProfile,
  isAdminRole,
} from '@/types';

const STATUS_BAR: Record<TravellerStatus, string> = {
  BOOKED: 'bg-success',
  APPROVED: 'bg-info',
  PENDING: 'bg-warning',
  REJECTED: 'bg-danger',
  CANCELLED: 'bg-border-strong',
};

/** Where the money and the movements stand by status, as one stacked bar. */
function StatusStrip({ data }: { data: Insights['by_status'] }) {
  const total = data.reduce((sum, row) => sum + row.count, 0);
  if (total === 0) return <p className="py-6 text-center text-xs text-text-subtle">No requests in this period.</p>;
  return (
    <div>
      <div className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full bg-surface-sunken">
        {data
          .filter((row) => row.count > 0)
          .map((row) => (
            <div
              key={row.status}
              className={cn('h-full first:rounded-l-full last:rounded-r-full', STATUS_BAR[row.status])}
              style={{ width: `${(row.count / total) * 100}%` }}
              title={`${TRAVELLER_STATUS_LABELS[row.status]}: ${row.count}`}
            />
          ))}
      </div>
      <ul className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2.5">
        {data.map((row) => (
          <li key={row.status} className="flex items-center gap-2 text-sm">
            <span className={cn('h-2.5 w-2.5 shrink-0 rounded-full', STATUS_BAR[row.status])} />
            <span className="text-text-muted">{TRAVELLER_STATUS_LABELS[row.status]}</span>
            <span className="ml-auto font-semibold tabular-nums">{row.count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The menu here leads with the default: recent trips and the upcoming ones,
 *  which is where requests still awaiting a decision sit. */
const DASHBOARD_PRESETS: RangePreset[] = [
  'last_30_next_30',
  'this_month',
  'last_month',
  'last_7',
  'last_30',
  'last_90',
  'next_30',
  'this_year',
  'all',
  'custom',
];

/**
 * Trips, people, nights, pending decisions and booked spend for each
 * department, as it is now. A row filters the whole dashboard to that
 * department; clicking it again clears the filter.
 */
function DepartmentBreakdown({
  rows,
  selected,
  onSelect,
}: {
  rows: Insights['by_department'] | undefined;
  selected: number | undefined;
  onSelect: (id: number) => void;
}) {
  const busiest = Math.max(1, ...(rows ?? []).map((row) => row.count));
  return (
    <Card>
      <CardHeader
        title="By department"
        description="Trips, people and booked spend for each department in this window. Click one to filter the dashboard."
      />
      {rows ? (
        rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-text-muted">
                  <th className="px-4 py-2.5 font-medium sm:px-5">Department</th>
                  <th className="px-4 py-2.5 text-right font-medium">Trips</th>
                  <th className="hidden px-4 py-2.5 text-right font-medium sm:table-cell">People</th>
                  <th className="hidden px-4 py-2.5 text-right font-medium md:table-cell">Nights</th>
                  <th className="hidden px-4 py-2.5 text-right font-medium sm:table-cell">Pending</th>
                  <th className="px-4 py-2.5 text-right font-medium sm:px-5">Spend</th>
                </tr>
              </thead>
              <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                {rows.map((row) => (
                  <tr
                    key={row.department_id}
                    className={cn(
                      // Important, or the zebra band would hide the hover and the
                      // choice on even rows.
                      'cursor-pointer hover:!bg-surface-sunken',
                      selected === row.department_id && '!bg-surface-sunken',
                    )}
                    onClick={() => onSelect(row.department_id)}
                  >
                    <td className="px-4 py-3 sm:px-5">
                      <span
                        className={cn(
                          'flex items-center gap-1.5 font-medium',
                          row.department_id === 0 && 'text-text-muted',
                        )}
                      >
                        <Building2 size={13} className="shrink-0 text-text-subtle" />
                        {row.name}
                      </span>
                      <span className="mt-1.5 block h-1.5 w-full max-w-60 overflow-hidden rounded-sm bg-surface-sunken">
                        <span
                          className="block h-full rounded-r-[3px] bg-chart-1"
                          style={{ width: `${(row.count / busiest) * 100}%` }}
                        />
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">{row.count}</td>
                    <td className="hidden px-4 py-3 text-right tabular-nums sm:table-cell">{row.people}</td>
                    <td className="hidden px-4 py-3 text-right tabular-nums md:table-cell">{row.nights}</td>
                    <td
                      className={cn(
                        'hidden px-4 py-3 text-right tabular-nums sm:table-cell',
                        row.pending > 0 && 'font-medium text-warning',
                      )}
                    >
                      {row.pending}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums sm:px-5">{formatMoney(row.spent)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="px-5 py-8 text-center text-xs text-text-subtle">No travel in this period.</p>
        )
      ) : (
        <div className="p-5">
          <Skeleton className="h-32 w-full" />
        </div>
      )}
    </Card>
  );
}

function AdminDashboard() {
  const f = useReportFilters('last_30_next_30');
  const [trendMetric, setTrendMetric] = useState<'movements' | 'spent'>('movements');

  // This page has no error state of its own, so a failed load says so in a toast.
  const queue = useQuery({ queryKey: ['queue-counts'], queryFn: () => fetchQueueCounts(), meta: { errorToast: true } });
  const options = useQuery({ queryKey: ['filter-options'], queryFn: fetchFilterOptions, meta: { errorToast: true } });
  const insights = useQuery({
    queryKey: ['insights', f.apiFilters],
    queryFn: () => fetchInsights(f.apiFilters),
    placeholderData: keepPreviousData,
    meta: { errorToast: true },
  });

  const data = insights.data;
  const k = data?.kpis;
  const awaiting = data?.awaiting;
  // The tile counts this slice; the queue's own total is every campaign, person
  // and date. Say both when they differ, so "0" in a past window is not read as
  // "nothing waiting" - and only blame the dates when nothing else is filtered.
  const awaitingHint = awaiting
    ? [
        awaiting.with_conflicts > 0 && `${awaiting.with_conflicts} with a calendar clash`,
        awaiting.partly_approved > 0 && `+${awaiting.partly_approved} partly approved`,
        queue.data &&
          queue.data.awaiting !== awaiting.requests &&
          `${queue.data.awaiting} ${f.sliced ? 'in the whole queue' : 'across all dates'}`,
      ]
        .filter(Boolean)
        .join(' · ') || 'In this view'
    : undefined;
  const toggleState = (label: string) =>
    f.update(stateChange(options.data, f.city, f.state === label ? '' : label));
  const toggleDepartment = (id: number) =>
    f.update(departmentChange(options.data, f.userId, f.departmentId === id ? '' : String(id)));

  return (
    <div className="space-y-6">
      <ReportFilterBar
        filters={f}
        options={options.data}
        optionsLoading={options.isPending}
        presets={DASHBOARD_PRESETS}
        idPrefix="dash"
        fetching={insights.isFetching && !insights.isPending}
        footerAction={
          <Link to={f.link('/travel-logs')} className="inline-flex items-center gap-1 font-medium text-brand-strong hover:underline">
            Open in travel logs <ArrowRight size={14} />
          </Link>
        }
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Link
          to="/approvals"
          title="Open the whole approvals queue"
          className="rounded-xl focus-visible:outline-offset-4"
        >
          <StatTile
            label="Awaiting a decision"
            value={awaiting ? String(awaiting.requests) : '—'}
            hint={awaitingHint}
            tone={awaiting && awaiting.requests > 0 ? 'warning' : 'default'}
            icon={<CheckSquare size={15} />}
          />
        </Link>
        <StatTile
          label="Booked spend"
          value={k ? formatMoney(k.spent) : '—'}
          hint={k ? `${formatMoney(k.committed)} approved, not yet booked` : undefined}
          icon={<IndianRupee size={15} />}
        />
        <StatTile
          label="People travelling"
          value={k ? String(k.people) : '—'}
          hint={k ? `${k.movements} movements on ${k.requests} requests` : undefined}
          icon={<Users size={15} />}
        />
        <StatTile
          label="Hotel nights"
          value={k ? String(k.nights) : '—'}
          hint={k ? `Average booking ${formatMoney(k.average_per_booking)}` : undefined}
          icon={<Moon size={15} />}
        />
      </div>

      {k && k.uncosted > 0 && (
        <Link
          to={f.link('/analytics')}
          className="flex items-start gap-2.5 rounded-xl border border-warning/30 bg-warning-soft px-4 py-3 text-sm text-warning hover:underline"
        >
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          {k.uncosted} booked {k.uncosted === 1 ? 'trip has' : 'trips have'} no cost recorded, so spend reads low. Add them in cost analytics.
        </Link>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title={trendMetric === 'movements' ? 'Movements over time' : 'Booked spend over time'}
            description={data ? `By ${data.grain}, on the day each trip starts.` : undefined}
            action={
              <div className="inline-flex rounded-lg border border-border bg-surface-sunken p-0.5 text-xs">
                {(['movements', 'spent'] as const).map((metric) => (
                  <button
                    key={metric}
                    type="button"
                    onClick={() => setTrendMetric(metric)}
                    className={cn(
                      'rounded-md px-3 py-1.5 font-medium transition-colors',
                      trendMetric === metric ? 'bg-surface text-text shadow-sm' : 'text-text-muted hover:text-text',
                    )}
                  >
                    {metric === 'movements' ? 'Trips' : 'Spend'}
                  </button>
                ))}
              </div>
            }
          />
          <div className="px-4 py-5 sm:px-5">
            {data ? (
              <Columns
                data={data.trend.map((row) => ({
                  label: periodLabel(row.period, data.grain),
                  value: trendMetric === 'movements' ? row.movements : Number(row.spent),
                }))}
                format={trendMetric === 'movements' ? (v) => String(Math.round(v)) : (v) => formatMoney(v)}
                caption={trendMetric === 'movements' ? 'Movements' : 'Booked spend'}
              />
            ) : (
              <Skeleton className="h-40 w-full" />
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Where requests stand" description="Every traveller on a request in this window." />
          <div className="px-4 py-5 sm:px-5">
            {data ? <StatusStrip data={data.by_status} /> : <Skeleton className="h-24 w-full" />}
          </div>
        </Card>
      </div>

      <DepartmentBreakdown
        rows={data?.by_department}
        selected={f.departmentId}
        onSelect={toggleDepartment}
      />

      <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
        <Card>
          <CardHeader title="Top destination states" description="Click one to filter the dashboard." />
          <div className="px-4 py-5 sm:px-5">
            {data && k ? (
              <>
                <HorizontalBars
                  data={data.top_states.map((row) => ({ label: row.label, value: row.count, id: row.label }))}
                  onSelect={(row) => toggleState(row.label)}
                  selected={f.state}
                  empty={
                    k.movements > 0
                      ? 'None of these trips has a destination state recorded.'
                      : 'No travel in this period.'
                  }
                />
                {k.unstated > 0 && data.top_states.length > 0 && (
                  <p className="mt-3 px-2 text-2xs text-text-subtle">
                    {k.unstated} {k.unstated === 1 ? 'trip has' : 'trips have'} no state recorded (raised before
                    states were captured).
                  </p>
                )}
              </>
            ) : (
              <Skeleton className="h-40 w-full" />
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Top places" description="Destinations, cab drop cities and hotel cities." />
          <div className="px-4 py-5 sm:px-5">
            {data ? (
              <HorizontalBars
                data={data.top_places.map((row) => ({ label: row.label, value: row.count, id: row.label }))}
                onSelect={(row) => f.update({ city: f.city === row.label ? undefined : row.label })}
                selected={f.city}
                empty="No travel in this period."
              />
            ) : (
              <Skeleton className="h-40 w-full" />
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="How people travel" />
          <div className="space-y-5 px-4 py-5 sm:px-5">
            {data ? (
              <>
                <HorizontalBars
                  data={data.by_type.map((row) => ({
                    label: REQUEST_TYPE_LABELS[row.request_type],
                    value: row.count,
                    detail: Number(row.spent) > 0 ? `${formatMoney(row.spent)} booked` : undefined,
                  }))}
                />
                {data.by_mode.length > 0 && (
                  <div className="flex flex-wrap gap-2 border-t border-border pt-4">
                    {data.by_mode.map((row) => (
                      <span key={row.label} className="inline-flex items-center gap-1.5 rounded-full bg-surface-sunken px-3 py-1 text-xs">
                        <Plane size={12} className="text-text-subtle" />
                        {TRAVEL_MODE_LABELS[row.label as TravelMode] ?? row.label}
                        <span className="font-semibold tabular-nums">{row.count}</span>
                      </span>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <Skeleton className="h-40 w-full" />
            )}
          </div>
        </Card>

        {/* self-start: a short campaign list should not stretch to the
            height of the people list beside it. */}
        <Card className="self-start xl:col-span-2">
          <CardHeader title="Campaigns" description="Movements and booked spend in this window. Click one to filter." />
          {data ? (
            data.by_campaign.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-xs text-text-muted">
                      <th className="px-4 py-2.5 font-medium sm:px-5">Campaign</th>
                      <th className="px-4 py-2.5 text-right font-medium">Trips</th>
                      <th className="hidden px-4 py-2.5 text-right font-medium sm:table-cell">People</th>
                      <th className="px-4 py-2.5 text-right font-medium sm:px-5">Spend</th>
                    </tr>
                  </thead>
                  <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                    {data.by_campaign.map((row) => (
                      <tr
                        key={row.project_id}
                        className={cn(
                          // Important, as in the department table above.
                          'cursor-pointer hover:!bg-surface-sunken',
                          f.projectId === row.project_id && '!bg-surface-sunken',
                        )}
                        onClick={() =>
                          f.update({ campaign: f.projectId === row.project_id ? undefined : String(row.project_id) })
                        }
                      >
                        <td className="px-4 py-3 sm:px-5">
                          <span className="font-medium">{row.name}</span>
                          <span className="block font-mono text-xs text-text-subtle sm:ml-2 sm:inline">{row.code}</span>
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums">{row.count}</td>
                        <td className="hidden px-4 py-3 text-right tabular-nums sm:table-cell">{row.people}</td>
                        <td className="px-4 py-3 text-right tabular-nums sm:px-5">{formatMoney(row.spent)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="px-5 py-8 text-center text-xs text-text-subtle">No campaign travel in this period.</p>
            )
          ) : (
            <div className="p-5"><Skeleton className="h-32 w-full" /></div>
          )}
        </Card>

        <Card>
          <CardHeader title="Most travelled" description="Open a person's log for the same dates." />
          <div className="px-2 py-2">
            {data ? (
              data.top_travellers.length ? (
                <ul>
                  {data.top_travellers.map((row) => (
                    <li key={row.user_id}>
                      {/* Travelled or going only, so the log's count matches this one. */}
                      <Link
                        to={f.link('/travel-logs', { user: String(row.user_id), status: 'travelled' })}
                        className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-surface-sunken"
                      >
                        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-surface-sunken text-xs font-semibold text-text-muted">
                          {row.full_name.split(/\s+/).slice(0, 2).map((p) => p[0]).join('')}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium">{row.full_name}</span>
                          <span className="block text-xs text-text-subtle">
                            {row.count} trip{row.count === 1 ? '' : 's'}
                            {row.nights > 0 && ` · ${row.nights} night${row.nights === 1 ? '' : 's'}`}
                          </span>
                        </span>
                        <span className="text-sm tabular-nums text-text-muted">{formatMoney(row.spent)}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="px-3 py-8 text-center text-xs text-text-subtle">No travel in this period.</p>
              )
            ) : (
              <div className="p-3"><Skeleton className="h-32 w-full" /></div>
            )}
          </div>
        </Card>
      </div>

      <div className="flex flex-wrap gap-3 text-sm">
        <Link to="/approvals" className="inline-flex items-center gap-1.5 font-medium text-brand-strong hover:underline">
          <Clock size={15} /> Approvals queue
        </Link>
        <Link to={f.link('/analytics')} className="inline-flex items-center gap-1.5 font-medium text-brand-strong hover:underline">
          <IndianRupee size={15} /> Cost analytics
        </Link>
        <Link to={f.link('/travel-logs')} className="inline-flex items-center gap-1.5 font-medium text-brand-strong hover:underline">
          <MapPin size={15} /> Travel logs
        </Link>
      </div>
    </div>
  );
}

function GroundStaffDashboard({ user }: { user: UserProfile }) {
  const reqsQuery = useQuery({
    queryKey: ['requests', 'mine'],
    queryFn: () => fetchRequests({ mine: true, page_size: 50 }),
  });
  const claimsQuery = useQuery({
    queryKey: ['expenses', 'mine'],
    queryFn: () => fetchExpenseClaims({ user_id: user.id }),
  });

  const myTrips = reqsQuery.data?.items ?? [];
  const activeTrips = myTrips.filter((t) => t.status === 'APPROVED' || t.status === 'BOOKED');
  const pendingTrips = myTrips.filter((t) => t.status === 'SUBMITTED' || t.status === 'PARTIALLY_APPROVED');
  const claims = claimsQuery.data ?? [];
  const settledTotal = claims
    .filter((c) => c.status === 'SETTLED')
    .reduce((sum, c) => sum + Number(c.total_approved_amount || c.total_claimed_amount), 0);

  return (
    <div className="space-y-6">
      {/* Hero Card */}
      <div className="relative overflow-hidden rounded-2xl border border-border bg-gradient-to-r from-blue-500/10 via-purple-500/5 to-transparent p-6 shadow-sm">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xl font-bold tracking-tight text-text">Ground Operations Portal</span>
              <Badge tone="info">Field Staff</Badge>
              {user.hotel_occupancy_entitlement === 'DOUBLE' ? (
                <Badge tone="neutral" className="bg-purple-100 text-purple-700 dark:bg-purple-950/50 dark:text-purple-300">
                  Double Sharing Room
                </Badge>
              ) : (
                <Badge tone="neutral" className="bg-blue-100 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300">
                  Single Room Entitled
                </Badge>
              )}
            </div>
            <p className="text-sm text-text-muted">
              {user.department_name ? `${user.department_name} · ` : ''}
              {user.base_location ? `${user.base_location}, ` : ''}
              {user.base_state || 'India'}
              {user.manager_name ? ` · Reports to ${user.manager_name}` : ''}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link to="/requests">
              <Button variant="secondary" size="sm">
                <History size={14} /> My Bookings
              </Button>
            </Link>
            <Link to="/requests?action=new">
              <Button size="sm">
                <CalendarCheck size={14} /> Raise Request
              </Button>
            </Link>
          </div>
        </div>
      </div>

      {/* 4 Interactive Quick Action Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Link to="/requests?action=new" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400">
              <Plane size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Raise Travel Request</h4>
          <p className="mt-1 text-xs text-text-muted">Flight, Train, Bus, Cab or Hotel with automatic two-tier approval routing.</p>
        </Link>

        <Link to="/requests" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400">
              <Car size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">In-Transit Telemetry</h4>
          <p className="mt-1 text-xs text-text-muted">Record morning/evening cab odometer photos or hotel check-in/out GPS.</p>
        </Link>

        <Link to="/expenses" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
              <Wallet size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Claim Reimbursement</h4>
          <p className="mt-1 text-xs text-text-muted">Claim daily food allowances (₹400/day) & local transit conveyance bills.</p>
        </Link>

        <Link to="/profile" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
              <ShieldCheck size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Identity & Documents</h4>
          <p className="mt-1 text-xs text-text-muted">Manage AES-256 encrypted Aadhaar, PAN, Passport or Driver's License.</p>
        </Link>
      </div>

      {/* KPI Tiles */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Active field journeys"
          value={reqsQuery.isPending ? '—' : String(activeTrips.length)}
          hint={activeTrips.length > 0 ? 'Approved or confirmed' : 'No active trips'}
          icon={<Plane size={15} />}
        />
        <StatTile
          label="Awaiting decisions"
          value={reqsQuery.isPending ? '—' : String(pendingTrips.length)}
          hint={pendingTrips.length > 0 ? 'With manager or admin' : 'Queue clear'}
          tone={pendingTrips.length > 0 ? 'warning' : 'default'}
          icon={<Clock size={15} />}
        />
        <StatTile
          label="Reimbursement claims"
          value={claimsQuery.isPending ? '—' : String(claims.length)}
          hint={`${claims.filter((c) => c.status === 'SETTLED').length} settled to bank`}
          icon={<Receipt size={15} />}
        />
        <StatTile
          label="Disbursed payouts"
          value={claimsQuery.isPending ? '—' : formatMoney(settledTotal)}
          hint="Total settled to your bank account"
          icon={<IndianRupee size={15} />}
        />
      </div>

      {/* Corporate Policy Quick Guide */}
      <Card>
        <CardHeader
          title="DesignBoxed Corporate Travel Policy at a Glance"
          description="Official limits applied during manager review and central finance audit."
        />
        <div className="grid gap-3 p-5 sm:grid-cols-2 lg:grid-cols-4 text-xs">
          <div className="rounded-lg border border-border bg-surface-sunken/40 p-3">
            <span className="font-semibold text-text">Daily Food Allowance</span>
            <p className="mt-1 text-lg font-bold text-brand-strong">₹400 / day</p>
            <p className="mt-1 text-text-subtle">Flat ceiling across all cities. Itemized receipts required.</p>
          </div>
          <div className="rounded-lg border border-border bg-surface-sunken/40 p-3">
            <span className="font-semibold text-text">Local Transit Allowance</span>
            <p className="mt-1 text-lg font-bold text-brand-strong">₹150 / day</p>
            <p className="mt-1 text-text-subtle">Unbilled auto allowed up to ₹150; taxi & cab actuals require bills.</p>
          </div>
          <div className="rounded-lg border border-border bg-surface-sunken/40 p-3">
            <span className="font-semibold text-text">Cab Hire Package</span>
            <p className="mt-1 text-lg font-bold text-brand-strong">80 KM / 8 Hrs</p>
            <p className="mt-1 text-text-subtle">Odometer verified morning and evening. Excess km auto-audited.</p>
          </div>
          <div className="rounded-lg border border-border bg-surface-sunken/40 p-3">
            <span className="font-semibold text-text">Lodging City Limits</span>
            <p className="mt-1 text-lg font-bold text-brand-strong">Cat A: ₹1,500+</p>
            <p className="mt-1 text-text-subtle">Cat A: ₹1,500/₹2,000 · Cat B: ₹1,000/₹1,500 · Cat C: ₹750/₹1,200.</p>
          </div>
        </div>
      </Card>

      {/* Travel History Panel */}
      <Card>
        <CardHeader
          title="Your travel movements"
          description="Chronological record of all field journeys, cab hires, and hotel stays."
        />
        <div className="px-4 py-4 sm:px-5">
          <TravelHistoryPanel userId={user.id} compact />
        </div>
      </Card>
    </div>
  );
}

function ManagerDashboard({ user }: { user: UserProfile }) {
  const reviewsQuery = useQuery({
    queryKey: ['team-reviews', 'waiting'],
    queryFn: () => fetchTeamReviews('waiting'),
  });
  const myTeamQuery = useQuery({
    queryKey: ['my-team'],
    queryFn: fetchMyTeam,
  });
  const claimsQuery = useQuery({
    queryKey: ['expenses', 'team-pending'],
    queryFn: () => fetchExpenseClaims({ status: 'SUBMITTED' }),
  });

  const waitingReviews = reviewsQuery.data?.items ?? [];
  const teamMembers = myTeamQuery.data ?? [];
  const pendingClaims = claimsQuery.data ?? [];

  return (
    <div className="space-y-6">
      {/* Hero Card */}
      <div className="relative overflow-hidden rounded-2xl border border-border bg-gradient-to-r from-emerald-500/10 via-teal-500/5 to-transparent p-6 shadow-sm">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xl font-bold tracking-tight text-text">Team Operations & L1 Review Portal</span>
              <Badge tone="success">Line Manager</Badge>
            </div>
            <p className="text-sm text-text-muted">
              {user.department_name ? `${user.department_name} · ` : ''}
              Managing {teamMembers.length} field team member{teamMembers.length === 1 ? '' : 's'}. You hold L1 review authority for bookings and expense claims.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link to="/team-approvals">
              <Button size="sm">
                <ClipboardCheck size={14} /> Team Approvals ({waitingReviews.length})
              </Button>
            </Link>
          </div>
        </div>
      </div>

      {/* 4 Action Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Link to="/team-approvals" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
              <ClipboardCheck size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Team L1 Approvals</h4>
          <p className="mt-1 text-xs text-text-muted">Review team travel requests and recommend Single or Double occupancy.</p>
        </Link>

        <Link to="/expenses" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400">
              <Wallet size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Expense Endorsements</h4>
          <p className="mt-1 text-xs text-text-muted">Verify operational necessity of team reimbursement claims before Finance audit.</p>
        </Link>

        <Link to="/my-team" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-purple-500/10 text-purple-600 dark:text-purple-400">
              <UsersRound size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Roster & Occupancy</h4>
          <p className="mt-1 text-xs text-text-muted">Configure Single or Double room entitlements for each team member.</p>
        </Link>

        <Link to="/projects" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400">
              <FolderKanban size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Campaign Budgets</h4>
          <p className="mt-1 text-xs text-text-muted">Track field movements, personnel deployed, and project expenditures.</p>
        </Link>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Link to="/team-approvals">
          <StatTile
            label="Awaiting L1 recommendation"
            value={reviewsQuery.isPending ? '—' : String(waitingReviews.length)}
            hint={waitingReviews.length > 0 ? 'Admin cannot approve until you act' : 'Queue clear'}
            tone={waitingReviews.length > 0 ? 'warning' : 'default'}
            icon={<ClipboardCheck size={15} />}
          />
        </Link>
        <Link to="/expenses">
          <StatTile
            label="Claims to endorse"
            value={claimsQuery.isPending ? '—' : String(pendingClaims.length)}
            hint="Team reimbursement submissions"
            tone={pendingClaims.length > 0 ? 'warning' : 'default'}
            icon={<Wallet size={15} />}
          />
        </Link>
        <Link to="/my-team">
          <StatTile
            label="Direct team roster"
            value={myTeamQuery.isPending ? '—' : String(teamMembers.length)}
            hint="Field executives and team leads"
            icon={<UsersRound size={15} />}
          />
        </Link>
        <StatTile
          label="Single room entitlements"
          value={myTeamQuery.isPending ? '—' : String(teamMembers.filter((m) => m.hotel_occupancy_entitlement === 'SINGLE').length)}
          hint={`${teamMembers.filter((m) => m.hotel_occupancy_entitlement === 'DOUBLE').length} on Double Sharing`}
          icon={<BedDouble size={15} />}
        />
      </div>

      {/* Awaiting Team Approvals Table */}
      <Card>
        <CardHeader
          title={`Team requests awaiting your L1 recommendation (${waitingReviews.length})`}
          description="Mandatory Two-Tier Approval: Admin approval is physically disabled until you record an approval."
          action={
            <Link to="/team-approvals">
              <Button size="sm" variant="secondary">Open Team Approvals</Button>
            </Link>
          }
        />
        {waitingReviews.length === 0 ? (
          <p className="p-8 text-center text-xs text-text-subtle">No team requests waiting for your recommendation.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-text-muted">
                  <th className="px-4 py-2.5 font-medium">Requester</th>
                  <th className="px-4 py-2.5 font-medium">Type</th>
                  <th className="px-4 py-2.5 font-medium">Route / City</th>
                  <th className="px-4 py-2.5 font-medium">Priority</th>
                  <th className="px-4 py-2.5 text-right font-medium">Action</th>
                </tr>
              </thead>
              <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                {waitingReviews.slice(0, 5).map((req) => (
                  <tr key={req.id}>
                    <td className="px-4 py-3 font-medium">
                      <div>{req.requester_name}</div>
                      <div className="text-2xs text-text-subtle">{req.project_code || 'General'}</div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone="neutral">{REQUEST_TYPE_LABELS[req.request_type]}</Badge>
                    </td>
                    <td className="px-4 py-3 text-text-muted">
                      {req.request_type === 'HOTEL' ? req.hotel_city : `${req.pickup_city || req.origin} → ${req.drop_city || req.destination}`}
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={req.priority === 'HIGH' ? 'danger' : 'neutral'}>{req.priority}</Badge>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Link to="/team-approvals">
                        <Button size="sm" variant="primary">Review</Button>
                      </Link>
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

function FinanceDashboard({ user }: { user: UserProfile }) {
  const claimsQuery = useQuery({
    queryKey: ['expenses'],
    queryFn: () => fetchExpenseClaims(),
  });
  const invoicesQuery = useQuery({
    queryKey: ['invoices'],
    queryFn: () => fetchInvoices(),
  });

  const claims = claimsQuery.data ?? [];
  const pendingAudit = claims.filter((c) => c.status === 'MANAGER_ENDORSED' || c.status === 'SUBMITTED');
  const readyForPayout = claims.filter((c) => c.status === 'FINANCE_APPROVED' || c.status === 'PARTIALLY_APPROVED');
  const settledClaims = claims.filter((c) => c.status === 'SETTLED');
  const totalSettledINR = settledClaims.reduce((sum, c) => sum + Number(c.total_approved_amount || c.total_claimed_amount), 0);
  const invoices = invoicesQuery.data?.items ?? [];
  const pendingInvoices = invoices.filter((i) => i.status === 'DRAFT' || i.status === 'SUBMITTED');

  return (
    <div className="space-y-6">
      {/* Hero Card */}
      <div className="relative overflow-hidden rounded-2xl border border-border bg-gradient-to-r from-emerald-500/10 via-blue-500/5 to-transparent p-6 shadow-sm">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xl font-bold tracking-tight text-text">Corporate Finance Desk — {user.full_name}</span>
              <Badge tone="success">Finance Desk</Badge>
            </div>
            <p className="text-sm text-text-muted">
              Reimbursement audit desk, policy cap enforcement (₹400 food, ₹150 transit), bank disbursements (UTR), and vendor reconciliation.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link to="/expenses">
              <Button size="sm">
                <Wallet size={14} /> Reimbursements Desk
              </Button>
            </Link>
            <Link to="/invoices">
              <Button size="sm" variant="secondary">
                <Receipt size={14} /> Vendor Invoices
              </Button>
            </Link>
          </div>
        </div>
      </div>

      {/* 4 Action Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Link to="/expenses?tab=PENDING" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
              <ClipboardCheck size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Audit Expense Claims</h4>
          <p className="mt-1 text-xs text-text-muted">Audit bills against ₹400 daily food ceiling and record line-item deductions.</p>
        </Link>

        <Link to="/expenses?tab=AUDITED" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400">
              <IndianRupee size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Disburse Payouts (UTR)</h4>
          <p className="mt-1 text-xs text-text-muted">Disburse approved funds via NEFT/RTGS/UPI and capture Bank UTR numbers.</p>
        </Link>

        <Link to="/invoices" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-purple-500/10 text-purple-600 dark:text-purple-400">
              <Receipt size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Vendor Invoices</h4>
          <p className="mt-1 text-xs text-text-muted">Commercial billing packages aggregated strictly from completed, verified trips.</p>
        </Link>

        <Link to="/analytics" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400">
              <Building2 size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Cost Analytics</h4>
          <p className="mt-1 text-xs text-text-muted">Cross-departmental expenditures, uncosted movements, and budget utilization.</p>
        </Link>
      </div>

      {/* KPI Tiles */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Link to="/expenses?tab=PENDING">
          <StatTile
            label="Claims awaiting audit"
            value={claimsQuery.isPending ? '—' : String(pendingAudit.length)}
            hint={pendingAudit.length > 0 ? 'Endorsed by manager' : 'All claims audited'}
            tone={pendingAudit.length > 0 ? 'warning' : 'default'}
            icon={<ClipboardCheck size={15} />}
          />
        </Link>
        <Link to="/expenses?tab=AUDITED">
          <StatTile
            label="Approved (Awaiting UTR)"
            value={claimsQuery.isPending ? '—' : String(readyForPayout.length)}
            hint="Ready for bank disbursement"
            tone={readyForPayout.length > 0 ? 'warning' : 'default'}
            icon={<IndianRupee size={15} />}
          />
        </Link>
        <StatTile
          label="Total settled reimbursements"
          value={claimsQuery.isPending ? '—' : formatMoney(totalSettledINR)}
          hint={`${settledClaims.length} claims settled`}
          icon={<Wallet size={15} />}
        />
        <Link to="/invoices">
          <StatTile
            label="Vendor invoices pending"
            value={invoicesQuery.isPending ? '—' : String(pendingInvoices.length)}
            hint="Awaiting Super Admin lock"
            icon={<Receipt size={15} />}
          />
        </Link>
      </div>

      {/* Pending Claims Queue preview */}
      <Card>
        <CardHeader
          title="Claims awaiting finance audit & settlement"
          description="Enforces flat ₹400/day food ceiling, ₹150 unbilled auto ceiling, and receipts verification."
          action={
            <Link to="/expenses">
              <Button size="sm" variant="secondary">Open Reimbursements Desk</Button>
            </Link>
          }
        />
        {pendingAudit.length === 0 && readyForPayout.length === 0 ? (
          <p className="p-8 text-center text-xs text-text-subtle">No expense claims currently awaiting finance action.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-text-muted">
                  <th className="px-4 py-2.5 font-medium">Claim Ref</th>
                  <th className="px-4 py-2.5 font-medium">Employee</th>
                  <th className="px-4 py-2.5 font-medium">City / Dates</th>
                  <th className="px-4 py-2.5 font-medium">Status</th>
                  <th className="px-4 py-2.5 text-right font-medium">Claimed</th>
                  <th className="px-4 py-2.5 text-right font-medium">Action</th>
                </tr>
              </thead>
              <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                {[...pendingAudit, ...readyForPayout].slice(0, 6).map((c) => (
                  <tr key={c.id}>
                    <td className="px-4 py-3 font-mono font-medium text-text">{c.claim_code}</td>
                    <td className="px-4 py-3">
                      <div>{c.user_name || 'Staff'}</div>
                      <div className="text-2xs text-text-subtle">{c.department_name || '—'}</div>
                    </td>
                    <td className="px-4 py-3 text-text-muted">
                      {c.destination_city} ({c.travel_start_date} to {c.travel_end_date})
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={c.status === 'MANAGER_ENDORSED' ? 'warning' : 'info'}>{c.status}</Badge>
                    </td>
                    <td className="px-4 py-3 text-right font-semibold tabular-nums">{formatMoney(c.total_claimed_amount)}</td>
                    <td className="px-4 py-3 text-right">
                      <Link to="/expenses">
                        <Button size="sm" variant={c.status === 'FINANCE_APPROVED' ? 'primary' : 'secondary'}>
                          {c.status === 'FINANCE_APPROVED' ? 'Record UTR' : 'Audit'}
                        </Button>
                      </Link>
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

function VendorDashboard({ user }: { user: UserProfile }) {
  const ordersQuery = useQuery({
    queryKey: ['requests', 'vendor-orders'],
    queryFn: () => fetchRequests({ mine: false, page_size: 50 }),
  });

  const orders = ordersQuery.data?.items ?? [];
  const readyOrders = orders.filter((o) => o.status === 'READY_FOR_VENDOR' || o.status === 'APPROVED');
  const bookedOrders = orders.filter((o) => o.status === 'BOOKED');
  const hotelOrders = readyOrders.filter((o) => o.request_type === 'HOTEL');
  const cabOrders = readyOrders.filter((o) => o.request_type === 'LOCAL_CAB');

  return (
    <div className="space-y-6">
      {/* Hero Card */}
      <div className="relative overflow-hidden rounded-2xl border border-border bg-gradient-to-r from-purple-500/10 via-indigo-500/5 to-transparent p-6 shadow-sm">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xl font-bold tracking-tight text-text">Contracted Partner Fulfillment Portal</span>
              <Badge tone="neutral" className="bg-purple-100 text-purple-700 dark:bg-purple-950/50 dark:text-purple-300">
                Vendor Partner
              </Badge>
            </div>
            <p className="text-sm text-text-muted">
              {user.vendor_name ? `${user.vendor_name} · ` : ''}
              Fulfill approved requests, submit 2–5 hotel proposals, dispatch cabs/drivers, and issue booking vouchers.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link to="/approvals">
              <Button size="sm">
                <Store size={14} /> Dispatched Orders ({readyOrders.length})
              </Button>
            </Link>
          </div>
        </div>
      </div>

      {/* 4 Action Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Link to="/approvals" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-purple-500/10 text-purple-600 dark:text-purple-400">
              <Store size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Orders for Dispatch</h4>
          <p className="mt-1 text-xs text-text-muted">View requests ready for vendor fulfillment across Flight, Cab, and Hotel.</p>
        </Link>

        <Link to="/approvals" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400">
              <BedDouble size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Hotel Proposals</h4>
          <p className="mt-1 text-xs text-text-muted">Submit 2–5 hotel choices with nightly rates, street addresses, and maps links.</p>
        </Link>

        <Link to="/approvals" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400">
              <Car size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Cab & Driver Details</h4>
          <p className="mt-1 text-xs text-text-muted">Input vehicle model, registration number plate, and driver mobile phone.</p>
        </Link>

        <Link to="/approvals" className="group rounded-xl border border-border bg-surface p-4 transition-all hover:border-brand-strong hover:shadow-md">
          <div className="flex items-center justify-between">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
              <FileCheck size={20} />
            </div>
            <ArrowUpRight size={16} className="text-text-subtle transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-brand-strong" />
          </div>
          <h4 className="mt-3 font-semibold text-text">Confirmed Vouchers</h4>
          <p className="mt-1 text-xs text-text-muted">Upload final confirmed hotel booking vouchers or e-ticket PDFs.</p>
        </Link>
      </div>

      {/* KPI Tiles */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Link to="/approvals">
          <StatTile
            label="Dispatched orders"
            value={ordersQuery.isPending ? '—' : String(readyOrders.length)}
            hint="Ready for fulfillment"
            tone={readyOrders.length > 0 ? 'warning' : 'default'}
            icon={<Store size={15} />}
          />
        </Link>
        <StatTile
          label="Hotel sourcing orders"
          value={ordersQuery.isPending ? '—' : String(hotelOrders.length)}
          hint="Requires 2-5 proposals"
          icon={<BedDouble size={15} />}
        />
        <StatTile
          label="Cab hire dispatches"
          value={ordersQuery.isPending ? '—' : String(cabOrders.length)}
          hint="Driver & vehicle required"
          icon={<Car size={15} />}
        />
        <StatTile
          label="Completed bookings"
          value={ordersQuery.isPending ? '—' : String(bookedOrders.length)}
          hint="Vouchers issued"
          icon={<CheckCircle2 size={15} />}
        />
      </div>

      {/* Dispatches Table */}
      <Card>
        <CardHeader
          title={`Active fulfillment orders (${readyOrders.length})`}
          description="Requests dispatched to your agency for vehicle assignment, hotel proposals, or voucher issuance."
          action={
            <Link to="/approvals">
              <Button size="sm" variant="secondary">Open Fulfillment Queue</Button>
            </Link>
          }
        />
        {readyOrders.length === 0 ? (
          <p className="p-8 text-center text-xs text-text-subtle">No orders currently dispatched to your agency.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-text-muted">
                  <th className="px-4 py-2.5 font-medium">Request #</th>
                  <th className="px-4 py-2.5 font-medium">Type</th>
                  <th className="px-4 py-2.5 font-medium">Traveller</th>
                  <th className="px-4 py-2.5 font-medium">Location</th>
                  <th className="px-4 py-2.5 font-medium">Priority</th>
                  <th className="px-4 py-2.5 text-right font-medium">Fulfill</th>
                </tr>
              </thead>
              <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                {readyOrders.slice(0, 6).map((req) => (
                  <tr key={req.id}>
                    <td className="px-4 py-3 font-mono font-medium">#{req.id}</td>
                    <td className="px-4 py-3">
                      <Badge tone="neutral">{REQUEST_TYPE_LABELS[req.request_type]}</Badge>
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-medium">{req.requester_name}</div>
                      <div className="text-2xs text-text-subtle">{req.project_code || 'General'}</div>
                    </td>
                    <td className="px-4 py-3 text-text-muted">
                      {req.request_type === 'HOTEL' ? req.hotel_city : `${req.pickup_city || req.origin} → ${req.drop_city || req.destination}`}
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={req.priority === 'HIGH' ? 'danger' : 'neutral'}>{req.priority}</Badge>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Link to="/approvals">
                        <Button size="sm" variant="primary">Fulfill</Button>
                      </Link>
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

export default function DashboardPage() {
  const user = useAuth((s) => s.user);

  const isAdmin = isAdminRole(user?.role);
  const firstName = user?.full_name.split(/\s+/)[0] ?? 'there';

  return (
    <div className="space-y-6">
      <PageHeader
        title={`Welcome back, ${firstName}`}
        description={
          isAdmin
            ? 'Central Operations Desk: Filter by dates, campaign, person or destination. All metrics follow.'
            : user?.role === 'FINANCE'
            ? 'Corporate Finance & Settlement Desk: Audit daily allowances, line deductions, and bank payouts.'
            : user?.role === 'MANAGER'
            ? 'Team Management & Approvals Desk: Review travel requests, endorse claims, and configure team occupancy.'
            : user?.role === 'VENDOR'
            ? 'Contracted Fulfillment Partner Desk: Submit hotel proposals, assign cabs/drivers, and issue confirmed tickets.'
            : 'Ground Staff Field Operations: Raise travel requests, track telemetry, and claim reimbursements.'
        }
        actions={
          user?.role === 'GROUND_STAFF' && (
            <Link to="/requests?action=new">
              <Button>
                <CalendarCheck size={16} />
                Raise Request
              </Button>
            </Link>
          )
        }
      />

      {isAdmin && <EmailProblemBanner />}
      {isAdmin && <AdminDashboard />}
      {!isAdmin && user?.role === 'FINANCE' && <FinanceDashboard user={user} />}
      {!isAdmin && user?.role === 'VENDOR' && <VendorDashboard user={user} />}
      {!isAdmin && user?.role === 'MANAGER' && <ManagerDashboard user={user} />}
      {!isAdmin && (!user?.role || user?.role === 'GROUND_STAFF') && user && <GroundStaffDashboard user={user} />}
    </div>
  );
}
