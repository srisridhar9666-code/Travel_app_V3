import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Plus, Receipt } from 'lucide-react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';

import { formatMoney } from '@/components/charts';
import { PaymentBadge } from '@/components/InvoicePayment';
import { Badge, Button, Card, EmptyState, PageHeader, Skeleton } from '@/components/ui';
import { errorMessage, fetchInvoices } from '@/lib/api';
import { periodText } from '@/lib/invoices';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import {
  INVOICE_STATUS_LABELS,
  INVOICE_STATUS_TONES,
  isInvoiceEditor,
  type InvoiceList,
} from '@/types';

/** Approved is split in two: still to be paid, and paid. */
type Tab = 'DRAFT' | 'SUBMITTED' | 'UNPAID' | 'PAID' | 'REJECTED' | 'ALL';

const TAB_LABELS: Record<Tab, string> = {
  DRAFT: 'Draft',
  SUBMITTED: 'Submitted',
  UNPAID: 'Approved · to pay',
  PAID: 'Paid',
  REJECTED: 'Rejected',
  ALL: 'All',
};

const TABS: Tab[] = ['DRAFT', 'SUBMITTED', 'UNPAID', 'PAID', 'REJECTED', 'ALL'];

/** What each tab asks the server for. */
const TAB_QUERY: Record<Tab, Parameters<typeof fetchInvoices>[0]> = {
  DRAFT: { status: 'DRAFT' },
  SUBMITTED: { status: 'SUBMITTED' },
  UNPAID: { payment: 'unpaid' },
  PAID: { payment: 'paid' },
  REJECTED: { status: 'REJECTED' },
  ALL: {},
};

function tabCount(tab: Tab, list: InvoiceList | undefined): number | undefined {
  if (!list) return undefined;
  if (tab === 'ALL') return list.total;
  if (tab === 'UNPAID') return list.payment_counts.unpaid;
  if (tab === 'PAID') return list.payment_counts.paid;
  return list.counts[tab];
}

/**
 * Vendor invoices: what the organisation owes each vendor for a period, added
 * up from the costs the desk recorded. Admins and system admins prepare them;
 * only the super admin approves. The super admin lands on what is waiting for
 * them.
 */
export default function InvoicesPage() {
  const navigate = useNavigate();
  const role = useAuth((s) => s.user?.role);
  const canEdit = isInvoiceEditor(role);
  const [params, setParams] = useSearchParams();
  // "APPROVED" is what older links say; it means the ones still to pay.
  const asked = params.get('status') === 'APPROVED' ? 'UNPAID' : (params.get('status') as Tab | null);
  const fallback: Tab = role === 'SUPER_ADMIN' ? 'SUBMITTED' : 'ALL';
  const tab: Tab = asked && asked in TAB_LABELS ? asked : fallback;
  const setTab = (next: Tab) =>
    setParams(next === fallback ? {} : { status: next }, { replace: true });

  const invoices = useQuery({
    queryKey: ['invoices', 'list', tab],
    queryFn: () => fetchInvoices(TAB_QUERY[tab]),
    placeholderData: keepPreviousData,
  });
  const rows = invoices.data?.items ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Invoices"
        description={
          canEdit
            ? 'Match a vendor’s bill against the trips you booked with them. The total comes from the recorded costs, and a super admin approves it.'
            : 'Vendor invoices the admins prepared. You approve or reject the ones waiting for you.'
        }
        actions={
          canEdit && (
            <Button onClick={() => navigate('/invoices/new')}>
              <Plus size={15} />
              New invoice
            </Button>
          )
        }
      />

      <div className="flex flex-wrap gap-1 border-b border-border" role="tablist">
        {TABS.map((key) => {
          const count = tabCount(key, invoices.data);
          return (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
              className={cn(
                '-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs transition-colors',
                tab === key
                  ? 'border-primary font-medium text-text'
                  : 'border-transparent text-text-muted hover:text-text',
              )}
            >
              {TAB_LABELS[key]}
              {count !== undefined && (
                <span
                  className={cn(
                    'rounded-full px-1.5 text-2xs tabular-nums',
                    (key === 'SUBMITTED' || key === 'UNPAID') && count > 0
                      ? 'bg-warning-soft text-warning'
                      : 'bg-surface-sunken text-text-subtle',
                  )}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <Card>
        {invoices.isPending ? (
          <div className="space-y-2 p-5">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : invoices.isError ? (
          <EmptyState
            icon={<Receipt size={28} />}
            title="Could not load the invoices"
            description={errorMessage(invoices.error)}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<Receipt size={28} />}
            title={tab === 'ALL' ? 'No invoices yet' : `Nothing ${TAB_LABELS[tab].toLowerCase()}`}
            description={
              tab === 'SUBMITTED'
                ? 'Nothing is waiting for a super admin.'
                : tab === 'UNPAID'
                  ? 'Every approved invoice is paid.'
                  : tab === 'PAID'
                    ? 'A super admin marks an approved invoice paid once the money goes.'
                    : canEdit
                  ? 'Start one from a vendor and a date range; the booked trips with their costs are listed to pick from.'
                  : 'Admins and system admins prepare invoices.'
            }
            action={
              canEdit &&
              tab !== 'UNPAID' &&
              tab !== 'PAID' && (
                <Button onClick={() => navigate('/invoices/new')}>
                  <Plus size={15} />
                  New invoice
                </Button>
              )
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border text-2xs uppercase tracking-wide text-text-subtle">
                <tr>
                  <th className="px-4 py-2.5 font-medium sm:px-5">Invoice</th>
                  <th className="px-4 py-2.5 font-medium">Vendor</th>
                  <th className="hidden px-4 py-2.5 font-medium md:table-cell">Period</th>
                  <th className="hidden px-4 py-2.5 text-right font-medium lg:table-cell">Trips</th>
                  <th className="px-4 py-2.5 text-right font-medium">Total</th>
                  <th className="hidden px-4 py-2.5 font-medium sm:table-cell">Status</th>
                  <th className="hidden px-4 py-2.5 font-medium lg:table-cell sm:pr-5">Created by</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((invoice) => (
                  <tr
                    key={invoice.id}
                    className="cursor-pointer transition-colors hover:bg-surface-sunken"
                    onClick={() => navigate(`/invoices/${invoice.id}`)}
                  >
                    <td className="whitespace-nowrap px-4 py-3 sm:px-5">
                      <Link
                        to={`/invoices/${invoice.id}`}
                        onClick={(e) => e.stopPropagation()}
                        className="font-mono text-xs font-medium text-brand-strong underline-offset-2 hover:underline"
                      >
                        {invoice.number}
                      </Link>
                      {invoice.vendor_invoice_ref && (
                        <p className="text-2xs text-text-subtle">Bill {invoice.vendor_invoice_ref}</p>
                      )}
                      {/* On a phone the status sits under the number, not in a
                          column off the edge of the screen. */}
                      <span className="mt-1 flex flex-wrap gap-1 sm:hidden">
                        <Badge tone={INVOICE_STATUS_TONES[invoice.status]}>
                          {INVOICE_STATUS_LABELS[invoice.status]}
                        </Badge>
                        <PaymentBadge invoice={invoice} />
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <p className="font-medium">{invoice.vendor_name}</p>
                      <p className="text-2xs text-text-subtle md:hidden">
                        {periodText(invoice.period_start, invoice.period_end)}
                      </p>
                    </td>
                    <td className="hidden whitespace-nowrap px-4 py-3 text-xs text-text-muted md:table-cell">
                      {periodText(invoice.period_start, invoice.period_end)}
                    </td>
                    <td className="hidden px-4 py-3 text-right tabular-nums text-text-muted lg:table-cell">
                      {invoice.line_count}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right font-medium tabular-nums">
                      {formatMoney(invoice.total_amount, true)}
                    </td>
                    <td className="hidden whitespace-nowrap px-4 py-3 sm:table-cell">
                      <span className="flex flex-wrap gap-1">
                        <Badge tone={INVOICE_STATUS_TONES[invoice.status]}>
                          {INVOICE_STATUS_LABELS[invoice.status]}
                        </Badge>
                        <PaymentBadge invoice={invoice} />
                      </span>
                    </td>
                    <td className="hidden px-4 py-3 text-xs text-text-muted lg:table-cell sm:pr-5">
                      {invoice.created_by_name ?? '—'}
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
