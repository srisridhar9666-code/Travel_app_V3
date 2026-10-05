import { useQuery } from '@tanstack/react-query';
import { Printer, X } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';

import { formatMoney } from '@/components/charts';
import { COMPANY_NAME } from '@/components/Logo';
import { Button, Spinner } from '@/components/ui';
import { errorMessage, fetchInvoice, markInvoicePrinted } from '@/lib/api';
import { dayLabel, periodText } from '@/lib/invoices';
import { formatInstant } from '@/lib/time';
import { INVOICE_STATUS_LABELS, VENDOR_KIND_LABELS, type Invoice } from '@/types';

/**
 * An invoice laid out for paper: the browser's print dialog turns it into a
 * PDF ("Save as PDF"), so there is no PDF library to keep up to date.
 *
 * Drawn outside the app shell and always in the light theme - a dark page
 * prints as a slab of toner - with the light logo files named directly,
 * because the `dark:` swaps in the shared logo components follow the app's
 * theme, not this page's. Opening it is logged, like a CSV download.
 */
export default function InvoicePrintPage() {
  const { id } = useParams();
  const invoiceId = Number(id);
  const [params] = useSearchParams();
  const invoice = useQuery({
    queryKey: ['invoices', 'detail', invoiceId],
    queryFn: () => fetchInvoice(invoiceId),
  });
  // Once each per visit, however often the effect below runs (React runs it
  // twice in development, and a refetch hands it new data).
  const logged = useRef(false);
  const printed = useRef(false);

  useEffect(() => {
    const data = invoice.data;
    if (!data) return;
    if (!logged.current) {
      logged.current = true;
      markInvoicePrinted(data.id).catch(() => undefined);
    }
    const previous = document.title;
    // The print dialog offers the page title as the PDF's file name.
    document.title = `${data.number} - ${data.vendor_name}`;
    // `?print=0` shows the page without opening the dialog.
    const timer =
      params.get('print') === '0'
        ? undefined
        : window.setTimeout(() => {
            if (printed.current) return;
            printed.current = true;
            window.print();
          }, 600);
    return () => {
      window.clearTimeout(timer);
      document.title = previous;
    };
  }, [invoice.data, params]);

  return (
    <div data-theme="light" className="min-h-dvh bg-canvas text-text print:bg-white">
      <style>{'@page { size: A4; margin: 14mm; }'}</style>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-surface px-4 py-3 print:hidden">
        <p className="text-xs text-text-muted">
          To save a PDF, choose <span className="font-medium text-text">Save as PDF</span> as the
          printer.
        </p>
        <div className="flex gap-2">
          <Button size="sm" onClick={() => window.print()} disabled={!invoice.data}>
            <Printer size={14} />
            Print or save as PDF
          </Button>
          <Button size="sm" variant="secondary" onClick={() => window.close()}>
            <X size={14} />
            Close
          </Button>
        </div>
      </div>

      {invoice.isPending ? (
        <div className="grid place-items-center py-24">
          <Spinner className="h-5 w-5" />
        </div>
      ) : invoice.isError || !invoice.data ? (
        <p className="px-6 py-24 text-center text-sm text-danger">
          {errorMessage(invoice.error, 'Could not load this invoice.')}
        </p>
      ) : (
        <Sheet invoice={invoice.data} />
      )}
    </div>
  );
}

function Sheet({ invoice }: { invoice: Invoice }) {
  const approved = invoice.status === 'APPROVED';
  return (
    <article className="mx-auto my-6 max-w-3xl bg-surface px-6 py-8 shadow-sm sm:px-10 print:my-0 print:max-w-none print:px-0 print:py-0 print:shadow-none">
      <header className="flex flex-wrap items-center justify-between gap-6 border-b border-border pb-6">
        {/* The icon beside the wordmark, as in the sidebar: the stacked logo
            shrinks to a smudge at a letterhead's height. */}
        <div className="flex items-center gap-2.5">
          <img src="/brand/mark.png" alt="" className="h-14 w-14 select-none object-contain" />
          <img
            src="/brand/wordmark-light.png"
            alt="Sriyatra - Your Travel Desk"
            className="h-14 w-auto select-none object-contain"
          />
        </div>
        <div className="flex items-center gap-3">
          <p className="text-right text-xs font-semibold leading-snug">{COMPANY_NAME}</p>
          <img
            src="/brand/company-logo-light.png"
            alt="DesignBoxed"
            className="h-16 w-auto select-none object-contain"
          />
        </div>
      </header>

      <section className="mt-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-2xs uppercase tracking-[0.14em] text-text-subtle">Vendor invoice</p>
          <h1 className="mt-1 font-mono text-2xl font-semibold">{invoice.number}</h1>
        </div>
        <p
          className={
            approved
              ? 'rounded-full border border-success px-3 py-1 text-xs font-semibold text-success'
              : 'rounded-full border border-warning px-3 py-1 text-xs font-semibold text-warning'
          }
        >
          {INVOICE_STATUS_LABELS[invoice.status]}
        </p>
      </section>

      <section className="mt-6 grid gap-6 text-sm sm:grid-cols-2 print:grid-cols-2">
        <div>
          <p className="text-2xs uppercase tracking-wide text-text-subtle">Vendor</p>
          <p className="mt-1 font-semibold">{invoice.vendor_name}</p>
          <p className="text-xs text-text-muted">{VENDOR_KIND_LABELS[invoice.vendor_kind]}</p>
          {invoice.vendor_gstin && (
            <p className="mt-1 text-xs">
              GSTIN <span className="font-mono">{invoice.vendor_gstin}</span>
            </p>
          )}
          {invoice.vendor_contact_name && <p className="text-xs">{invoice.vendor_contact_name}</p>}
          {invoice.vendor_phone && <p className="text-xs">{invoice.vendor_phone}</p>}
          {invoice.vendor_email && <p className="text-xs">{invoice.vendor_email}</p>}
        </div>
        <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-xs">
          <dt className="text-text-subtle">Period</dt>
          <dd>{periodText(invoice.period_start, invoice.period_end)}</dd>
          <dt className="text-text-subtle">Their bill number</dt>
          <dd>{invoice.vendor_invoice_ref ?? '—'}</dd>
          <dt className="text-text-subtle">Prepared by</dt>
          <dd>{invoice.created_by_name ?? '—'}</dd>
          {invoice.submitted_at && (
            <>
              <dt className="text-text-subtle">Submitted</dt>
              <dd>
                {formatInstant(invoice.submitted_at)}
                {invoice.submitted_by_name && ` by ${invoice.submitted_by_name}`}
              </dd>
            </>
          )}
          {approved && invoice.decided_at && (
            <>
              <dt className="text-text-subtle">Approved</dt>
              <dd>
                {formatInstant(invoice.decided_at)}
                {invoice.decided_by_name && ` by ${invoice.decided_by_name}`}
              </dd>
              <dt className="text-text-subtle">Payment</dt>
              <dd>
                {invoice.paid_on
                  ? `Paid on ${dayLabel(invoice.paid_on)}${invoice.payment_reference ? `, ref ${invoice.payment_reference}` : ''}`
                  : 'Not paid yet'}
              </dd>
            </>
          )}
        </dl>
      </section>

      <table className="mt-8 w-full text-left text-xs">
        <thead className="border-y border-border-strong text-2xs uppercase tracking-wide text-text-muted">
          <tr>
            <th className="py-2 pr-2 font-medium">No.</th>
            <th className="py-2 pr-2 font-medium">Date</th>
            <th className="py-2 pr-2 font-medium">Description</th>
            <th className="py-2 pr-2 font-medium">Reference</th>
            <th className="py-2 text-right font-medium">Amount (INR)</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {invoice.lines.map((line, index) => (
            <tr key={line.id} className="break-inside-avoid">
              <td className="py-2 pr-2 align-top tabular-nums text-text-muted">{index + 1}</td>
              <td className="whitespace-nowrap py-2 pr-2 align-top">{dayLabel(line.travel_date)}</td>
              <td className="py-2 pr-2 align-top">{line.description}</td>
              <td className="py-2 pr-2 align-top">{line.booking_reference ?? '—'}</td>
              <td className="whitespace-nowrap py-2 text-right align-top tabular-nums">
                {formatMoney(line.amount, true)}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-border-strong">
            <td colSpan={4} className="py-3 text-sm font-semibold">
              Total ({invoice.line_count} {invoice.line_count === 1 ? 'trip' : 'trips'})
            </td>
            <td className="whitespace-nowrap py-3 text-right text-sm font-semibold tabular-nums">
              {formatMoney(invoice.total_amount, true)}
            </td>
          </tr>
        </tfoot>
      </table>

      {invoice.notes && (
        <section className="mt-6 text-xs">
          <p className="text-2xs uppercase tracking-wide text-text-subtle">Notes</p>
          <p className="mt-1">{invoice.notes}</p>
        </section>
      )}

      <section className="mt-6 rounded-md border border-border px-4 py-3 text-xs">
        {approved ? (
          <p>
            Approved by <span className="font-semibold">{invoice.decided_by_name ?? 'a super admin'}</span>
            {invoice.decided_at && ` on ${formatInstant(invoice.decided_at)}`}.
            {invoice.decision_comment && ` “${invoice.decision_comment}”`}
          </p>
        ) : (
          <p className="font-medium text-warning">
            Not approved - {INVOICE_STATUS_LABELS[invoice.status].toLowerCase()}. This copy is for
            checking only; amounts can still change until a super admin approves it.
          </p>
        )}
      </section>

      <footer className="mt-10 border-t border-border pt-3 text-2xs text-text-subtle">
        Generated by Sriyatra - Your Travel Desk, {COMPANY_NAME}, on{' '}
        {formatInstant(new Date().toISOString())}. Amounts are the trip costs recorded on the travel
        desk.
      </footer>
    </article>
  );
}
