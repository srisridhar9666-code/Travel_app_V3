import { keepPreviousData, useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowRight,
  Building2,
  CalendarCheck,
  CheckSquare,
  Clock,
  IndianRupee,
  MapPin,
  Moon,
  Plane,
  Users,
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
import { Button, Card, CardHeader, PageHeader, Skeleton, ZEBRA_ROWS } from '@/components/ui';
import { fetchFilterOptions, fetchInsights, fetchQueueCounts } from '@/lib/api';
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

export default function DashboardPage() {
  const user = useAuth((s) => s.user);

  // The operational picture, for the people who act on it. Ground staff get
  // their own travel below instead - nothing in the admin half is theirs to do.
  const isAdmin = isAdminRole(user?.role);
  const firstName = user?.full_name.split(/\s+/)[0] ?? 'there';

  return (
    <div className="space-y-6">
      <PageHeader
        title={`Welcome back, ${firstName}`}
        description={
          isAdmin
            ? 'Filter by dates, campaign, person or destination. Every number below follows.'
            : 'Raise a request from My requests. Decisions and tickets arrive in your notifications.'
        }
        actions={
          !isAdmin && (
            <Link to="/requests">
              <Button>
                <CalendarCheck size={16} />
                My requests
              </Button>
            </Link>
          )
        }
      />

      {isAdmin && <EmailProblemBanner />}
      {isAdmin && <AdminDashboard />}

      {/* Ground staff see their own movements. */}
      {!isAdmin && user && (
        <Card>
          <CardHeader
            title="Your travel"
            description="Every trip you were on, including ones a colleague raised."
          />
          <div className="px-4 py-4 sm:px-5">
            <TravelHistoryPanel userId={user.id} compact />
          </div>
        </Card>
      )}
    </div>
  );
}
