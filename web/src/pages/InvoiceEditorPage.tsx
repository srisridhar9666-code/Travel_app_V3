import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ArrowLeft, Lock, Receipt, Send } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { formatMoney } from '@/components/charts';
import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Select,
  Skeleton,
  ZEBRA_ROWS,
} from '@/components/ui';
import { useVendors } from '@/components/VendorSelect';
import {
  createInvoice,
  errorMessage,
  fetchEligible,
  fetchInvoice,
  fetchWhyNotListed,
  submitInvoice,
  updateInvoice,
  type WhyNotListed,
} from '@/lib/api';
import { dayLabel, lastMonth, sumAmounts } from '@/lib/invoices';
import { todayInIndia } from '@/lib/time';
import { cn } from '@/lib/utils';
import { VENDOR_KIND_LABELS, type EligibleRow, type Invoice, type InvoicePayload } from '@/types';

/**
 * Preparing a vendor's invoice: pick the vendor and the period, then tick the
 * booked trips their bill covers. Every amount is the cost the desk recorded,
 * so the total adds itself up and cannot be typed - that is the point of
 * reconciling against the vendor's own bill.
 */
export default function InvoiceEditorPage() {
  const { id } = useParams();
  const invoiceId = id ? Number(id) : null;
  const existing = useQuery({
    queryKey: ['invoices', 'detail', invoiceId],
    queryFn: () => fetchInvoice(invoiceId!),
    enabled: invoiceId !== null,
  });

  if (invoiceId !== null) {
    if (existing.isPending) {
      return (
        <div className="space-y-4">
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-48 w-full" />
        </div>
      );
    }
    if (existing.isError || !existing.data) {
      return (
        <Card>
          <EmptyState
            icon={<Receipt size={28} />}
            title="Could not load this invoice"
            description={errorMessage(existing.error)}
            action={<Link to="/invoices" className="text-sm text-brand-strong hover:underline">Back to invoices</Link>}
          />
        </Card>
      );
    }
    if (!existing.data.can_edit) {
      return (
        <Card>
          <EmptyState
            icon={<Lock size={28} />}
            title={`${existing.data.number} can no longer be edited`}
            description="An approved invoice is a record of what was paid. Admins and system admins edit invoices until a super admin approves them."
            action={
              <Link to={`/invoices/${existing.data.id}`} className="text-sm text-brand-strong hover:underline">
                Open the invoice
              </Link>
            }
          />
        </Card>
      );
    }
  }
  return <InvoiceForm key={invoiceId ?? 'new'} invoice={existing.data ?? null} />;
}

/** The empty list explained: what is in the period, and what to do about it. */
function whyText(why: WhyNotListed | undefined, includeUnassigned: boolean): string {
  if (!why) return 'Only booked trips with a cost entered, travelling in these dates, can be billed.';
  if (why.trips_in_period === 0) {
    return 'No trips travel in these dates. Try a wider date range, e.g. "This month so far".';
  }
  const parts: string[] = [];
  const n = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;
  if (why.booked_without_cost) {
    parts.push(`${n(why.booked_without_cost, 'booked trip has', 'booked trips have')} no cost yet - enter it on Approvals, Booked tab, Cost`);
  }
  if (why.not_booked_yet) parts.push(`${n(why.not_booked_yet, 'trip is', 'trips are')} not marked booked yet`);
  if (why.other_vendor) parts.push(`${n(why.other_vendor, 'trip was', 'trips were')} paid to another vendor`);
  if (why.no_vendor_recorded && !includeUnassigned) {
    parts.push(`${n(why.no_vendor_recorded, 'trip has', 'trips have')} no vendor recorded - tick "Also list trips with no vendor recorded"`);
  }
  if (why.already_invoiced) parts.push(`${n(why.already_invoiced, 'trip is', 'trips are')} already on another invoice`);
  return parts.length
    ? `In these dates: ${parts.join('; ')}.`
    : 'Only booked trips with a cost entered, travelling in these dates, can be billed.';
}

function InvoiceForm({ invoice }: { invoice: Invoice | null }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const vendors = useVendors();
  const initial = invoice
    ? { start: invoice.period_start, end: invoice.period_end }
    : lastMonth();

  const [vendorId, setVendorId] = useState<number | ''>(invoice?.vendor_id ?? '');
  const [start, setStart] = useState(initial.start);
  const [end, setEnd] = useState(initial.end);
  // On by default: trips booked before vendors were recorded have none, and
  // hiding them made a vendor look as if it had nothing to bill.
  const [includeUnassigned, setIncludeUnassigned] = useState(true);
  const [selected, setSelected] = useState<Set<number>>(
    () => new Set(invoice?.lines.map((line) => line.traveller_id) ?? []),
  );
  const [reference, setReference] = useState(invoice?.vendor_invoice_ref ?? '');
  const [notes, setNotes] = useState(invoice?.notes ?? '');

  const periodOk = Boolean(start && end && start <= end);
  const eligible = useQuery({
    queryKey: ['invoices', 'eligible', vendorId, start, end, invoice?.id, includeUnassigned],
    queryFn: () =>
      fetchEligible({
        vendor_id: vendorId as number,
        start,
        end,
        invoice_id: invoice?.id,
        include_unassigned: includeUnassigned,
      }),
    enabled: vendorId !== '' && periodOk,
    placeholderData: keepPreviousData,
  });
  const rows = useMemo(() => eligible.data ?? [], [eligible.data]);
  const why = useQuery({
    queryKey: ['invoices', 'why', vendorId, start, end],
    queryFn: () => fetchWhyNotListed({ vendor_id: vendorId as number, start, end }),
    enabled: vendorId !== '' && periodOk && eligible.isSuccess && rows.length === 0,
  });
  const pickable = rows.filter((row) => !row.problem);

  // A new vendor or period can leave trips out, and a line that can no longer
  // be billed cannot stay ticked: what is ticked is always something saving
  // would accept.
  useEffect(() => {
    if (!eligible.data) return;
    const ok = new Set(eligible.data.filter((r) => !r.problem).map((r) => r.traveller_id));
    setSelected((previous) => {
      const next = new Set([...previous].filter((id) => ok.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }, [eligible.data]);

  const chosen = rows.filter((row) => selected.has(row.traveller_id));
  const total = sumAmounts(chosen.map((row) => row.amount));
  const allTicked = pickable.length > 0 && pickable.every((row) => selected.has(row.traveller_id));
  const someTicked = chosen.length > 0 && !allTicked;
  const headerBox = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (headerBox.current) headerBox.current.indeterminate = someTicked;
  }, [someTicked]);

  const toggle = (id: number) =>
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleAll = () =>
    setSelected(allTicked ? new Set() : new Set(pickable.map((row) => row.traveller_id)));

  // Set once a new invoice exists, so a submit that fails after the save does
  // not leave the admin on a form that would create a second draft.
  const created = useRef<Invoice | null>(null);
  const persist = useMutation({
    mutationFn: async (andSubmit: boolean) => {
      const payload: InvoicePayload = {
        vendor_id: vendorId as number,
        period_start: start,
        period_end: end,
        traveller_ids: [...selected],
        vendor_invoice_ref: reference.trim() || null,
        notes: notes.trim() || null,
      };
      const saved = invoice ? await updateInvoice(invoice.id, payload) : await createInvoice(payload);
      created.current = saved;
      return andSubmit ? submitInvoice(saved.id) : saved;
    },
    meta: { errorFallback: 'Could not save the invoice.' },
    onSuccess: (saved, andSubmit) => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] });
      toast.success(
        andSubmit
          ? `${saved.number} sent to the super admins for approval`
          : invoice?.status === 'SUBMITTED'
            ? `${saved.number} saved — the super admins are told it changed`
            : `${saved.number} saved as a draft`,
      );
      navigate(`/invoices/${saved.id}`);
    },
    onError: () => {
      if (!invoice && created.current) {
        queryClient.invalidateQueries({ queryKey: ['invoices'] });
        navigate(`/invoices/${created.current.id}`);
      }
    },
  });

  const vendorList = vendors.data ?? [];
  const vendor = vendorList.find((v) => v.id === vendorId);
  const submitted = invoice?.status === 'SUBMITTED';
  const canSave = vendorId !== '' && periodOk && !persist.isPending;
  const thisMonth = todayInIndia().slice(0, 8);

  return (
    <div className="space-y-6">
      <Link
        to={invoice ? `/invoices/${invoice.id}` : '/invoices'}
        className="inline-flex items-center gap-1.5 text-xs text-text-muted hover:text-text"
      >
        <ArrowLeft size={13} />
        {invoice ? invoice.number : 'Invoices'}
      </Link>
      <PageHeader
        title={invoice ? `Edit ${invoice.number}` : 'New invoice'}
        description="Pick the vendor and the dates their bill covers, then tick the trips on it. Amounts are the costs recorded on each trip; the total cannot be typed."
      />

      {invoice?.status === 'REJECTED' && (
        <div className="rounded-lg bg-danger-soft px-4 py-3 text-sm">
          <p className="font-medium text-danger">
            Rejected{invoice.decided_by_name ? ` by ${invoice.decided_by_name}` : ''}
          </p>
          {invoice.decision_comment && <p className="mt-0.5 text-text-muted">{invoice.decision_comment}</p>}
          <p className="mt-1 text-xs text-text-subtle">
            Saving puts it back to draft. Submit it again when it is fixed.
          </p>
        </div>
      )}
      {submitted && (
        <div className="rounded-lg bg-warning-soft px-4 py-3 text-sm text-text-muted">
          This invoice is waiting for a super admin. It stays submitted when you save, and they are
          told it changed.
        </div>
      )}

      <Card>
        <CardHeader title="Vendor and period" />
        <div className="grid gap-4 p-4 sm:grid-cols-2 sm:p-5 lg:grid-cols-4">
          <Field label="Vendor" htmlFor="invoice-vendor" required className="sm:col-span-2">
            <Select
              id="invoice-vendor"
              value={vendorId === '' ? '' : String(vendorId)}
              onChange={(e) => setVendorId(e.target.value === '' ? '' : Number(e.target.value))}
              disabled={vendors.isPending}
            >
              <option value="">{vendors.isPending ? 'Loading vendors…' : 'Choose the vendor'}</option>
              {vendorList.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} · {VENDOR_KIND_LABELS[v.kind]}
                  {v.is_active ? '' : ' (switched off)'}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="From" htmlFor="invoice-start" required>
            <Input
              id="invoice-start"
              type="date"
              value={start}
              max={end || undefined}
              onChange={(e) => setStart(e.target.value)}
            />
          </Field>
          <Field
            label="To"
            htmlFor="invoice-end"
            required
            error={start && end && start > end ? 'The period ends before it starts.' : null}
          >
            <Input
              id="invoice-end"
              type="date"
              value={end}
              min={start || undefined}
              onChange={(e) => setEnd(e.target.value)}
            />
          </Field>
          <div className="flex flex-wrap items-center gap-2 sm:col-span-2 lg:col-span-4">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                const month = lastMonth();
                setStart(month.start);
                setEnd(month.end);
              }}
            >
              Last month
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setStart(`${thisMonth}01`);
                setEnd(todayInIndia());
              }}
            >
              This month so far
            </Button>
            <label className="ml-auto flex items-center gap-2 text-xs text-text-muted">
              <input
                type="checkbox"
                checked={includeUnassigned}
                onChange={(e) => setIncludeUnassigned(e.target.checked)}
                className="h-4 w-4 accent-[rgb(var(--primary))]"
              />
              Also list trips with no vendor recorded
            </label>
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Trips on this invoice"
          description={
            vendor
              ? `Booked trips with a cost, paid to ${vendor.name}${includeUnassigned ? ' or to nobody recorded' : ''}, travelling ${dayLabel(start)} to ${dayLabel(end)}, and on no other invoice.`
              : 'Choose a vendor to see their booked trips.'
          }
        />
        {vendorId === '' || !periodOk ? (
          <EmptyState
            icon={<Receipt size={28} />}
            title={vendorId === '' ? 'Choose a vendor first' : 'Fix the dates'}
            description="The trips that can go on the invoice are listed here."
          />
        ) : eligible.isPending ? (
          <div className="space-y-2 p-5">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : eligible.isError ? (
          <EmptyState
            icon={<Receipt size={28} />}
            title="Could not load the trips"
            description={errorMessage(eligible.error)}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<Receipt size={28} />}
            title="No trips to bill"
            description={whyText(why.data, includeUnassigned)}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border text-2xs uppercase tracking-wide text-text-subtle">
                <tr>
                  <th className="w-10 px-4 py-2.5 sm:pl-5">
                    <input
                      ref={headerBox}
                      type="checkbox"
                      checked={allTicked}
                      onChange={toggleAll}
                      disabled={pickable.length === 0}
                      aria-label="Select every trip"
                      className="h-4 w-4 accent-[rgb(var(--primary))]"
                    />
                  </th>
                  <th className="px-2 py-2.5 font-medium">Date</th>
                  <th className="px-2 py-2.5 font-medium">Traveller</th>
                  <th className="hidden px-2 py-2.5 font-medium md:table-cell">Trip</th>
                  <th className="hidden px-2 py-2.5 font-medium lg:table-cell">Campaign</th>
                  <th className="px-4 py-2.5 text-right font-medium sm:pr-5">Cost</th>
                </tr>
              </thead>
              <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                {rows.map((row) => (
                  <TripRow
                    key={row.traveller_id}
                    row={row}
                    ticked={selected.has(row.traveller_id)}
                    onToggle={() => toggle(row.traveller_id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3 sm:px-5">
          <p className="text-xs text-text-muted">
            {chosen.length} of {pickable.length} {pickable.length === 1 ? 'trip' : 'trips'} ticked
          </p>
          <div className="text-right">
            <p className="text-2xs uppercase tracking-wide text-text-subtle">Total</p>
            <p className="text-xl font-semibold tabular-nums" aria-live="polite">
              {formatMoney(total, true)}
            </p>
            <p className="text-2xs text-text-subtle">Added up from the recorded costs; it cannot be typed.</p>
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader title="The vendor’s bill" />
        <div className="grid gap-4 p-4 sm:grid-cols-2 sm:p-5">
          <Field
            label="Their bill number"
            htmlFor="invoice-ref"
            hint="As printed on the vendor's invoice, to match the two."
          >
            <Input
              id="invoice-ref"
              value={reference}
              maxLength={80}
              onChange={(e) => setReference(e.target.value)}
              placeholder="SAI/2026/118"
            />
          </Field>
          <Field label="Notes" htmlFor="invoice-notes" hint="For the super admin approving it.">
            <Input
              id="invoice-notes"
              value={notes}
              maxLength={1000}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Two fares differ from their bill by ₹40 - agreed by phone"
            />
          </Field>
        </div>
      </Card>

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button
          variant="ghost"
          onClick={() => navigate(invoice ? `/invoices/${invoice.id}` : '/invoices')}
        >
          Cancel
        </Button>
        {submitted ? (
          <Button loading={persist.isPending} disabled={!canSave || chosen.length === 0} onClick={() => persist.mutate(false)}>
            Save changes
          </Button>
        ) : (
          <>
            <Button
              variant="secondary"
              loading={persist.isPending && persist.variables === false}
              disabled={!canSave}
              onClick={() => persist.mutate(false)}
            >
              Save draft
            </Button>
            <Button
              loading={persist.isPending && persist.variables === true}
              disabled={!canSave || chosen.length === 0}
              title={chosen.length === 0 ? 'Tick at least one trip first' : undefined}
              onClick={() => persist.mutate(true)}
            >
              <Send size={14} />
              Submit for approval
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function TripRow({ row, ticked, onToggle }: { row: EligibleRow; ticked: boolean; onToggle: () => void }) {
  const blocked = Boolean(row.problem);
  return (
    // Important, so ticked, blocked and hovered rows show over the table's
    // stripes rather than vanishing on every other row.
    <tr
      className={cn(
        'transition-colors',
        blocked ? '!bg-danger-soft/40' : 'cursor-pointer hover:!bg-surface-sunken',
        ticked && '!bg-brand-soft/40',
      )}
      onClick={blocked ? undefined : onToggle}
    >
      <td className="px-4 py-2.5 align-top sm:pl-5">
        <input
          type="checkbox"
          checked={ticked}
          disabled={blocked}
          onChange={onToggle}
          onClick={(e) => e.stopPropagation()}
          aria-label={`Bill ${row.traveller_name}'s trip on ${dayLabel(row.travel_date)}`}
          className="h-4 w-4 accent-[rgb(var(--primary))]"
        />
      </td>
      <td className="whitespace-nowrap px-2 py-2.5 align-top text-xs">{dayLabel(row.travel_date)}</td>
      <td className="px-2 py-2.5 align-top">
        <p className="font-medium">{row.traveller_name}</p>
        <p className="text-2xs text-text-subtle">
          {row.employee_code && `${row.employee_code} · `}
          {row.booking_reference ? `Ref ${row.booking_reference}` : `Request ${row.request_id}`}
          {row.vendor_id === null && ' · no vendor recorded'}
        </p>
        <p className="text-2xs text-text-muted md:hidden">{row.trip}</p>
        {row.problem && (
          <p className="mt-1 flex items-start gap-1 text-2xs text-danger">
            <AlertTriangle size={12} className="mt-0.5 shrink-0" />
            {row.problem} It comes off when you save.
          </p>
        )}
      </td>
      <td className="hidden max-w-xs px-2 py-2.5 align-top text-xs text-text-muted md:table-cell">
        {row.trip}
      </td>
      <td className="hidden px-2 py-2.5 align-top text-xs lg:table-cell">
        <span className="font-mono">{row.project_code}</span>
        <p className="text-2xs text-text-subtle">{row.project_name}</p>
      </td>
      <td className="whitespace-nowrap px-4 py-2.5 text-right align-top tabular-nums sm:pr-5">
        {formatMoney(row.amount, true)}
      </td>
    </tr>
  );
}
