import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import { format } from 'date-fns';
import {
  BedDouble,
  Car,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  History,
  MapPin,
  Moon,
  Plane,
  Search,
  SlidersHorizontal,
  Train,
  Users,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { useSearchParams } from 'react-router-dom';

import { Combobox } from '@/components/Combobox';
import {
  CityField,
  DepartmentSelect,
  departmentChange,
  departmentParam,
  peopleChoices,
  stateChange,
} from '@/components/ReportFilters';
import {
  DateRangePicker,
  PRESET_LABELS,
  describeRange,
  rangeFor,
  type DateRange,
  type RangePreset,
} from '@/components/DateRangePicker';
import { StatTile, formatMoney } from '@/components/charts';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  ItemCard,
  ItemList,
  Select,
  Skeleton,
  ZEBRA_ROWS,
} from '@/components/ui';
import { MAX_LOG_ROWS, errorMessage, fetchFilterOptions, fetchTravelLogs } from '@/lib/api';
import { downloadCsv, slug } from '@/lib/csv';
import { routeLabel } from '@/lib/places';
import { fileStamp } from '@/lib/time';
import { cn } from '@/lib/utils';
import {
  PRIORITY_LABELS,
  REQUEST_TYPE_LABELS,
  TRAVELLER_STATUS_LABELS,
  TRAVEL_MODE_LABELS,
  type RequestType,
  type TravelLogEntry,
  type TravellerStatus,
} from '@/types';

/** Which traveller statuses each choice in the Status menu stands for. No
 *  statuses sent means every status, which is the default. */
const STATUS_CHOICES: Record<string, { label: string; statuses: TravellerStatus[] | undefined }> = {
  all: { label: 'Every status', statuses: undefined },
  travelled: { label: 'Travelled or going', statuses: ['PENDING', 'APPROVED', 'BOOKED'] },
  BOOKED: { label: 'Booked', statuses: ['BOOKED'] },
  APPROVED: { label: 'Approved, not booked', statuses: ['APPROVED'] },
  PENDING: { label: 'Awaiting a decision', statuses: ['PENDING'] },
  REJECTED: { label: 'Rejected', statuses: ['REJECTED'] },
  CANCELLED: { label: 'Cancelled', statuses: ['CANCELLED'] },
};
const DEFAULT_STATUS = 'all';

/** Rows per page. The server caps a page at MAX_LOG_ROWS; these are what a
 *  person scrolls comfortably. */
const PAGE_SIZES = [25, 50, 100, 200];
const DEFAULT_PAGE_SIZE = 50;

const STATUS_TONE: Record<TravellerStatus, 'success' | 'warning' | 'danger' | 'info' | 'neutral'> = {
  BOOKED: 'success',
  APPROVED: 'info',
  PENDING: 'warning',
  REJECTED: 'danger',
  CANCELLED: 'neutral',
};

function TypeIcon({ entry }: { entry: TravelLogEntry }) {
  const size = 15;
  if (entry.request_type === 'HOTEL') return <BedDouble size={size} />;
  if (entry.request_type === 'LOCAL_CAB') return <Car size={size} />;
  if (entry.mode === 'TRAIN') return <Train size={size} />;
  if (entry.mode === 'BUS') return <Car size={size} />;
  return <Plane size={size} />;
}

function when(entry: TravelLogEntry) {
  if (!entry.started_on) return 'Date not set';
  const start = new Date(`${entry.started_on}T00:00:00`);
  if (entry.request_type === 'HOTEL' && entry.check_out) {
    return `${format(start, 'd MMM')} – ${format(new Date(`${entry.check_out}T00:00:00`), 'd MMM yyyy')}`;
  }
  const time = entry.start_at ? format(new Date(entry.start_at), ', h:mm a') : '';
  return `${format(start, 'EEE d MMM yyyy')}${time}`;
}

function kind(entry: TravelLogEntry) {
  if (entry.request_type === 'LONG_DISTANCE' && entry.mode) return TRAVEL_MODE_LABELS[entry.mode];
  return REQUEST_TYPE_LABELS[entry.request_type];
}

function place(entry: TravelLogEntry) {
  if (entry.request_type === 'HOTEL') {
    return [entry.hotel_city, entry.hotel_state].filter(Boolean).join(', ') || 'Hotel';
  }
  if (!entry.origin && !entry.destination) return '?';
  return routeLabel(entry);
}

/** Every row matching the filters - not just the page on screen - as a
 *  spreadsheet. A cab's address and the city it is in are separate columns, so
 *  the sheet can be sorted by city. */
function exportCsv(entries: TravelLogEntry[], label: string) {
  const header = [
    'Date', 'Time', 'Employee', 'Employee code', 'Type', 'From', 'From city', 'From state',
    'To', 'To city', 'To state', 'Hotel', 'Hotel state', 'Nights', 'Campaign', 'Status',
    'Priority', 'PNR / booking ref', 'Travelled with', 'Cost (INR)', 'Reason',
  ];
  const rows = entries.map((e) => [
    e.started_on, e.start_at ? e.start_at.slice(11, 16) : '', e.full_name,
    e.employee_code, kind(e), e.origin, e.pickup_city, e.origin_state,
    e.destination, e.drop_city, e.destination_state, e.hotel_city,
    e.hotel_state, e.nights, e.project_name ?? e.project_code,
    TRAVELLER_STATUS_LABELS[e.status], e.priority ? PRIORITY_LABELS[e.priority] : '',
    e.booking_reference, e.companions.join('; '), e.cost_amount, e.travel_reason,
  ]);
  downloadCsv(`travel-log-${slug(label)}-${fileStamp()}.csv`, header, rows);
}

function Pager({
  page,
  pages,
  total,
  pageSize,
  shown,
  busy,
  onPage,
  onPageSize,
}: {
  page: number;
  pages: number;
  total: number;
  pageSize: number;
  shown: number;
  busy: boolean;
  onPage: (page: number) => void;
  onPageSize: (size: number) => void;
}) {
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = (page - 1) * pageSize + shown;
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-border px-4 py-3 sm:px-5">
      <p className="text-sm text-text-muted tabular-nums" aria-live="polite">
        Showing <span className="font-medium text-text">{first}–{last}</span> of{' '}
        <span className="font-medium text-text">{total}</span>
      </p>
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        <label className="flex items-center gap-2 text-sm text-text-muted">
          <span className="hidden sm:inline">Rows per page</span>
          <span className="sm:hidden">Rows</span>
          <Select
            aria-label="Rows per page"
            value={String(pageSize)}
            onChange={(e) => onPageSize(Number(e.target.value))}
            className="h-9 w-20"
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </Select>
        </label>
        <span className="text-sm text-text-muted tabular-nums">
          Page {page} of {pages}
        </span>
        <div className="flex gap-1.5">
          <Button
            variant="secondary"
            size="sm"
            disabled={page <= 1 || busy}
            onClick={() => onPage(page - 1)}
            aria-label="Previous page"
          >
            <ChevronLeft size={16} />
            <span className="hidden sm:inline">Previous</span>
          </Button>
          <Button
            variant="secondary"
            size="sm"
            disabled={page >= pages || busy}
            onClick={() => onPage(page + 1)}
            aria-label="Next page"
          >
            <span className="hidden sm:inline">Next</span>
            <ChevronRight size={16} />
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function TravelLogsPage() {
  const [params, setParams] = useSearchParams();

  // Filters live in the URL, so a filtered log can be bookmarked or pasted to
  // a colleague and opens exactly as it was.
  const preset = (params.get('range') as RangePreset) || 'last_month';
  const range: DateRange =
    preset === 'custom'
      ? rangeFor('custom', { since: params.get('since') ?? '', until: params.get('until') ?? '' })
      : rangeFor(PRESET_LABELS[preset] ? preset : 'last_month');
  const userId = params.get('user') ? Number(params.get('user')) : undefined;
  const projectId = params.get('campaign') ? Number(params.get('campaign')) : undefined;
  const departmentId = departmentParam(params.get('department'));
  const requestType = (params.get('type') as RequestType) || undefined;
  const state = params.get('state') || '';
  const city = params.get('city') || '';
  const askedStatus = params.get('status') ?? '';
  const statusKey = STATUS_CHOICES[askedStatus] ? askedStatus : DEFAULT_STATUS;
  const search = params.get('q') || '';
  const page = Math.max(1, Math.floor(Number(params.get('page'))) || 1);
  const pageSize = PAGE_SIZES.includes(Number(params.get('size')))
    ? Number(params.get('size'))
    : DEFAULT_PAGE_SIZE;
  const [searchDraft, setSearchDraft] = useState(search);
  const [showFilters, setShowFilters] = useState(false);

  /** Any change but a page turn starts again from page 1: page 4 of the old
   *  filters means nothing under the new ones. */
  const update = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (!('page' in changes)) next.delete('page');
    setParams(next, { replace: true });
  };

  const setRange = (next: DateRange) =>
    update({
      range: next.preset,
      since: next.preset === 'custom' ? next.since : undefined,
      until: next.preset === 'custom' ? next.until : undefined,
    });

  const options = useQuery({ queryKey: ['filter-options'], queryFn: fetchFilterOptions });

  const person = peopleChoices(options.data, userId, departmentId);

  const filters = {
    since: range.since || undefined,
    until: range.until || undefined,
    user_id: userId,
    project_id: projectId,
    department_id: departmentId,
    request_type: requestType,
    state: state || undefined,
    city: city || undefined,
    status: STATUS_CHOICES[statusKey].statuses,
    search: search || undefined,
  };

  // A new page, size or filter starts at the top of the table, not wherever
  // the last one was scrolled to.
  const scroller = useRef<HTMLDivElement>(null);
  const viewKey = JSON.stringify([filters, page, pageSize]);
  useEffect(() => {
    scroller.current?.scrollTo({ top: 0 });
  }, [viewKey]);

  const log = useQuery({
    queryKey: ['travel-logs', filters, page, pageSize],
    queryFn: () => fetchTravelLogs({ ...filters, page, page_size: pageSize }),
    placeholderData: keepPreviousData,
  });

  const activeCount = [
    userId,
    projectId,
    departmentId !== undefined,
    requestType,
    state,
    city,
    search,
    statusKey !== DEFAULT_STATUS,
  ].filter(Boolean).length;
  const filtersActive = activeCount > 0;
  const rangeLabel = describeRange(range);
  const heading = person.name ? `${person.name} · ${rangeLabel}` : rangeLabel;

  // The export is every match, fetched afresh, not the page on screen.
  const exporting = useMutation({
    mutationFn: () => fetchTravelLogs({ ...filters, page: 1, page_size: MAX_LOG_ROWS }),
    onSuccess: (all) => {
      exportCsv(all.entries, heading);
      if (all.truncated) {
        toast(`Exported the newest ${all.entries.length} of ${all.total}. Narrow the dates for the rest.`);
      } else {
        toast.success(`Exported ${all.entries.length} ${all.entries.length === 1 ? 'row' : 'rows'}`);
      }
    },
    meta: { errorFallback: 'Could not export the log.' },
  });

  const data = log.data;
  const total = data?.total ?? 0;
  const stats = [
    { label: 'Movements', value: data ? String(data.summary.movements) : '—', icon: <History size={14} /> },
    { label: 'People', value: data ? String(data.summary.people) : '—', icon: <Users size={14} /> },
    { label: 'Places', value: data ? String(data.summary.places) : '—', icon: <MapPin size={14} /> },
    { label: 'Hotel nights', value: data ? String(data.summary.nights) : '—', icon: <Moon size={14} /> },
    {
      label: 'Booked spend',
      value: data?.summary.spent ? formatMoney(data.summary.spent) : '—',
      icon: <span className="text-xs font-semibold leading-none">₹</span>,
    },
  ];

  return (
    // From lg up the page is pinned to the window: the filters and totals stay
    // put and only the table scrolls. Below that there is not the height for
    // it, so the page scrolls and the table keeps its own bounded scroll.
    <div className="flex flex-col gap-5 lg:h-[calc(100dvh-8rem-1px)] lg:min-h-[38rem] lg:gap-4">
      {/* Shorter than the usual page header from lg up: every line here is a
          line of table that does not fit. */}
      <div className="shrink-0">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl lg:text-2xl">Travel logs</h1>
        <p className="mt-1.5 max-w-2xl text-sm text-text-muted lg:mt-1 lg:max-w-none">
          Where every employee went and when. Pick a person and a month, or search the whole team.
        </p>
      </div>

      <Card className="shrink-0 p-4 sm:p-5 lg:p-4">
        {/* Nine filters stacked on a phone are a screen of form before the
            first row of results, so below lg they fold away. */}
        <button
          type="button"
          onClick={() => setShowFilters((open) => !open)}
          aria-expanded={showFilters}
          aria-controls="log-filters"
          className="flex w-full items-center justify-between gap-3 text-left lg:hidden"
        >
          <span className="flex items-center gap-2 text-sm font-semibold">
            <SlidersHorizontal size={16} className="text-text-subtle" />
            Filters
            {activeCount > 0 && <Badge tone="info">{activeCount} on</Badge>}
          </span>
          <span className="flex items-center gap-1.5 text-sm text-text-muted">
            {rangeLabel}
            <ChevronDown size={16} className={cn('transition-transform', showFilters && 'rotate-180')} />
          </span>
        </button>
        <div
          id="log-filters"
          className={cn(
            'grid gap-4 sm:grid-cols-2 lg:grid lg:grid-cols-4 lg:gap-x-4 lg:gap-y-3 xl:grid-cols-5',
            showFilters ? 'mt-4 lg:mt-0' : 'hidden',
          )}
        >
          <Field label="Employee" htmlFor="log-person">
            <Combobox
              id="log-person"
              value={person.label}
              options={[...person.byLabel.keys()]}
              loading={options.isPending}
              placeholder="All employees"
              emptyText="Nobody by that name."
              onChange={(label) => update({ user: String(person.byLabel.get(label) ?? '') })}
              action={
                userId
                  ? { label: 'All employees', icon: <Users size={16} className="mt-0.5 shrink-0 text-text-subtle" />, onSelect: () => update({ user: undefined }) }
                  : undefined
              }
            />
          </Field>
          <Field label="When" htmlFor="log-range" className="xl:col-span-1">
            <DateRangePicker id="log-range" value={range} onChange={setRange} />
          </Field>
          <Field label="Campaign" htmlFor="log-campaign">
            <Select
              id="log-campaign"
              value={projectId ? String(projectId) : ''}
              onChange={(e) => update({ campaign: e.target.value })}
            >
              <option value="">All campaigns</option>
              {(options.data?.projects ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.code} — {p.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Department" htmlFor="log-department">
            <DepartmentSelect
              id="log-department"
              options={options.data}
              value={departmentId}
              onChange={(next) => update(departmentChange(options.data, userId, next))}
            />
          </Field>
          <Field label="Search" htmlFor="log-search">
            <form
              className="relative"
              onSubmit={(e) => {
                e.preventDefault();
                update({ q: searchDraft.trim() });
              }}
            >
              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-subtle" />
              <Input
                id="log-search"
                value={searchDraft}
                onChange={(e) => setSearchDraft(e.target.value)}
                onBlur={() => searchDraft.trim() !== search && update({ q: searchDraft.trim() })}
                placeholder="Place, PNR, reason…"
                className="pl-9"
              />
            </form>
          </Field>
          <Field label="Type" htmlFor="log-type">
            <Select
              id="log-type"
              value={requestType ?? ''}
              onChange={(e) => update({ type: e.target.value })}
            >
              <option value="">All types</option>
              {(Object.keys(REQUEST_TYPE_LABELS) as RequestType[]).map((t) => (
                <option key={t} value={t}>
                  {REQUEST_TYPE_LABELS[t]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Destination state" htmlFor="log-state">
            <Select
              id="log-state"
              value={state}
              onChange={(e) => update(stateChange(options.data, city, e.target.value))}
            >
              <option value="">All states</option>
              {(options.data?.states ?? []).map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Destination city" htmlFor="log-city">
            <CityField
              id="log-city"
              options={options.data}
              state={state}
              city={city}
              loading={options.isPending}
              onChange={(next) => update(next)}
            />
          </Field>
          <Field label="Status" htmlFor="log-status">
            <Select
              id="log-status"
              value={statusKey}
              onChange={(e) => update({ status: e.target.value === DEFAULT_STATUS ? undefined : e.target.value })}
            >
              {Object.entries(STATUS_CHOICES).map(([key, choice]) => (
                <option key={key} value={key}>
                  {choice.label}
                </option>
              ))}
            </Select>
          </Field>
          <div className="flex items-end">
            {filtersActive && (
              <Button
                variant="ghost"
                onClick={() => {
                  setSearchDraft('');
                  // Filters only: the date range and rows per page stay as chosen.
                  const kept = new URLSearchParams({ range: preset });
                  for (const key of ['since', 'until', 'size']) {
                    const value = params.get(key);
                    if (value) kept.set(key, value);
                  }
                  setParams(kept, { replace: true });
                }}
              >
                <X size={16} />
                Clear filters
              </Button>
            )}
          </div>
        </div>
      </Card>

      {/* Phones and tablets get tiles; from lg the same figures sit in the
          table's header, so the rows get the height. */}
      <div className="grid shrink-0 grid-cols-2 gap-3 sm:grid-cols-3 lg:hidden">
        {stats.map((stat, index) => (
          <div key={stat.label} className={cn(index === stats.length - 1 && 'col-span-2 sm:col-span-1')}>
            <StatTile compact label={stat.label} value={stat.value} icon={stat.icon} />
          </div>
        ))}
      </div>

      {/* No min-h-0 here: the card may not shrink below the scroller's floor,
          so on a short window the page scrolls a little rather than the table
          collapsing to nothing and the pager being cut off. */}
      <Card className="flex flex-col overflow-hidden lg:flex-1">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-border px-4 py-3.5 sm:px-5">
          <div className="mr-auto min-w-0">
            <h2 className="text-base font-semibold tracking-tight">{heading}</h2>
            <p className="mt-0.5 text-xs text-text-muted">
              {data
                ? `${total} ${total === 1 ? 'entry' : 'entries'}${filtersActive ? ' matching the filters' : ''}` +
                  (statusKey === DEFAULT_STATUS ? '. Totals count only trips that went or are going ahead.' : '')
                : 'Loading…'}
            </p>
          </div>
          <dl className="hidden items-center gap-x-6 lg:flex">
            {stats.slice(1).map((stat) => (
              <div key={stat.label} className="min-w-0">
                <dt className="flex items-center gap-1.5 text-xs text-text-muted">
                  <span className="text-text-subtle">{stat.icon}</span>
                  {stat.label}
                </dt>
                <dd className="mt-0.5 text-lg font-semibold leading-6 tracking-tight">{stat.value}</dd>
              </div>
            ))}
          </dl>
          <Button
            variant="secondary"
            size="sm"
            loading={exporting.isPending}
            disabled={!data || total === 0}
            onClick={() => exporting.mutate()}
            title="Download every matching row, not just this page"
          >
            {!exporting.isPending && <Download size={15} />}
            Export CSV
          </Button>
        </div>

        {log.isPending ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : log.isError ? (
          <EmptyState icon={<History size={28} />} title="Could not load the log" description={errorMessage(log.error)} />
        ) : data!.entries.length === 0 ? (
          <EmptyState
            icon={<MapPin size={28} />}
            title="No travel in this period"
            description="Widen the dates or clear a filter."
          />
        ) : (
          <>
            {/* The only scrolling part of the page from lg up. Below lg it is
                bounded too, so a long page of rows cannot push the pager off
                the bottom of a phone. */}
            <div
              className={cn(
                'max-h-[70dvh] overflow-auto overscroll-contain lg:max-h-none lg:min-h-[12rem] lg:flex-1',
                log.isPlaceholderData && 'opacity-60 transition-opacity',
              )}
              ref={scroller}
              tabIndex={0}
              aria-label="Travel log entries"
            >
              {/* Phones get cards; a seven-column table does not fit a hand. */}
              <ItemList className="md:hidden">
                {data!.entries.map((entry) => (
                  <ItemCard key={entry.traveller_id} accent={STATUS_TONE[entry.status]} className="space-y-1.5">
                    <div className="flex items-start justify-between gap-3">
                      <button
                        type="button"
                        className="text-left text-sm font-semibold hover:underline"
                        onClick={() => update({ user: String(entry.user_id) })}
                      >
                        {entry.full_name}
                      </button>
                      <Badge tone={STATUS_TONE[entry.status]}>{TRAVELLER_STATUS_LABELS[entry.status]}</Badge>
                    </div>
                    <p className="flex items-center gap-2 text-sm">
                      <span className="text-text-subtle"><TypeIcon entry={entry} /></span>
                      {place(entry)}
                    </p>
                    <p className="text-xs text-text-muted">
                      {when(entry)} · {kind(entry)}
                      {entry.nights != null && ` · ${entry.nights} night${entry.nights === 1 ? '' : 's'}`}
                    </p>
                    <p className="text-xs text-text-subtle">
                      {entry.project_name ?? entry.project_code}
                      {entry.booking_reference && ` · PNR ${entry.booking_reference}`}
                      {entry.cost_amount && ` · ${formatMoney(entry.cost_amount)}`}
                    </p>
                    {entry.companions.length > 0 && (
                      <p className="text-xs text-text-subtle">With {entry.companions.join(', ')}</p>
                    )}
                  </ItemCard>
                ))}
              </ItemList>

              <table className="hidden w-full text-sm md:table">
                {/* Sticky, so the column names stay in view while the rows
                    scroll under them. The inset shadow is the bottom rule: a
                    border on a sticky row scrolls away with the rows. */}
                <thead className="sticky top-0 z-10 bg-surface-sunken shadow-[inset_0_-1px_0_rgb(var(--border))]">
                  <tr className="text-left text-xs font-medium text-text-muted">
                    <th className="px-4 py-2.5 font-medium">Date</th>
                    <th className="px-4 py-2.5 font-medium">Employee</th>
                    <th className="px-4 py-2.5 font-medium">Where</th>
                    <th className="px-4 py-2.5 font-medium">Type</th>
                    <th className="px-4 py-2.5 font-medium">Campaign</th>
                    <th className="px-4 py-2.5 font-medium">Status</th>
                    <th className="px-4 py-2.5 text-right font-medium">Cost</th>
                  </tr>
                </thead>
                <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                  {data!.entries.map((entry) => (
                    // Full sunken and important: a /60 hover under the /50
                    // stripe would not show on every other row.
                    <tr key={entry.traveller_id} className="align-top hover:!bg-surface-sunken">
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums text-text-muted lg:py-2.5">{when(entry)}</td>
                      <td className="px-4 py-3 lg:py-2.5">
                        <button
                          type="button"
                          className="text-left font-medium hover:underline"
                          title="Show only this person"
                          onClick={() => update({ user: String(entry.user_id) })}
                        >
                          {entry.full_name}
                        </button>
                        {entry.employee_code && (
                          <span className="block font-mono text-xs text-text-subtle">{entry.employee_code}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 lg:py-2.5">
                        <span className="block">{place(entry)}</span>
                        {entry.companions.length > 0 && (
                          <span className="block text-xs text-text-subtle">With {entry.companions.join(', ')}</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 lg:py-2.5 text-text-muted">
                        <span className="inline-flex items-center gap-1.5">
                          <TypeIcon entry={entry} />
                          {kind(entry)}
                        </span>
                        {entry.nights != null && (
                          <span className="block text-xs text-text-subtle">
                            {entry.nights} night{entry.nights === 1 ? '' : 's'}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 lg:py-2.5 text-text-muted">
                        <span className="font-mono text-xs">{entry.project_code}</span>
                        {entry.project_name && <span className="block text-xs text-text-subtle">{entry.project_name}</span>}
                      </td>
                      <td className="px-4 py-3 lg:py-2.5">
                        <Badge tone={STATUS_TONE[entry.status]}>{TRAVELLER_STATUS_LABELS[entry.status]}</Badge>
                        {entry.priority === 'HIGH' && (
                          <span className="mt-1 block text-xs font-medium text-danger">High priority</span>
                        )}
                        {entry.booking_reference && (
                          <span className="mt-1 block font-mono text-xs text-text-subtle">PNR {entry.booking_reference}</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 lg:py-2.5 text-right tabular-nums">
                        {entry.cost_amount ? formatMoney(entry.cost_amount) : <span className="text-text-subtle">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="shrink-0">
              <Pager
                page={data!.page}
                pages={data!.pages}
                total={total}
                pageSize={data!.page_size}
                shown={data!.entries.length}
                busy={log.isFetching}
                onPage={(next) => update({ page: next > 1 ? String(next) : undefined })}
                onPageSize={(size) =>
                  update({ size: size === DEFAULT_PAGE_SIZE ? undefined : String(size) })
                }
              />
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
