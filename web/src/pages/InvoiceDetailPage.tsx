import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Download,
  FileText,
  Pencil,
  Receipt,
  Send,
  Trash2,
  X,
} from 'lucide-react';
import { useState, type ReactNode } from 'react';
import toast from 'react-hot-toast';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { formatMoney } from '@/components/charts';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { PaymentBadge, PaymentFields } from '@/components/InvoicePayment';
import { Modal } from '@/components/Modal';
import { Badge, Button, Card, CardHeader, EmptyState, Field, Skeleton } from '@/components/ui';
import {
  approveInvoice,
  recordInvoicePayment,
  deleteInvoice,
  downloadInvoiceCsv,
  errorMessage,
  fetchInvoice,
  rejectInvoice,
  submitInvoice,
} from '@/lib/api';
import { INVOICE_EVENT_LABELS, dayLabel, periodText } from '@/lib/invoices';
import { formatInstant, todayInIndia } from '@/lib/time';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import {
  INVOICE_STATUS_LABELS,
  INVOICE_STATUS_TONES,
  VENDOR_KIND_LABELS,
  type Invoice,
  type InvoiceEvent,
} from '@/types';

/**
 * One vendor invoice: what it bills, where it stands, and who did what.
 *
 * The buttons offered are the ones the server says this viewer may use now -
 * edit, submit and delete for admins and system admins while it is open,
 * approve and reject for a super admin while it waits - so a button never
 * leads to a refusal.
 */
export default function InvoiceDetailPage() {
  const { id } = useParams();
  const invoiceId = Number(id);
  const invoice = useQuery({
    queryKey: ['invoices', 'detail', invoiceId],
    queryFn: () => fetchInvoice(invoiceId),
  });

  if (invoice.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (invoice.isError || !invoice.data) {
    return (
      <Card>
        <EmptyState
          icon={<Receipt size={28} />}
          title="Could not load this invoice"
          description={errorMessage(invoice.error)}
          action={<Link to="/invoices" className="text-sm text-brand-strong hover:underline">Back to invoices</Link>}
        />
      </Card>
    );
  }
  return <Detail invoice={invoice.data} />;
}

function Detail({ invoice }: { invoice: Invoice }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const role = useAuth((s) => s.user?.role);
  const [deciding, setDeciding] = useState<'approve' | 'reject' | null>(null);
  const [comment, setComment] = useState('');
  const [removing, setRemoving] = useState(false);
  // Approving can record the payment in the same step; or it is recorded later.
  const today = todayInIndia();
  const [paidNow, setPaidNow] = useState(false);
  const [paidOn, setPaidOn] = useState(today);
  const [paymentRef, setPaymentRef] = useState('');
  const [paying, setPaying] = useState<'paid' | 'unpaid' | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['invoices'] });
  const problems = invoice.lines.filter((line) => line.problem);

  const submit = useMutation({
    mutationFn: () => submitInvoice(invoice.id),
    meta: { errorFallback: 'Could not submit the invoice.' },
    onSuccess: (saved) => {
      toast.success(`${saved.number} sent to the super admins for approval`);
      refresh();
    },
  });

  const decide = useMutation({
    mutationFn: (approve: boolean) =>
      approve
        ? approveInvoice(invoice.id, {
            comment: comment.trim() || null,
            expected_total: invoice.total_amount,
            ...(paidNow
              ? { paid: true, paid_on: paidOn, payment_reference: paymentRef.trim() || null }
              : {}),
          })
        : rejectInvoice(invoice.id, comment.trim()),
    meta: { errorFallback: 'Could not save the decision.' },
    onSuccess: (saved, approve) => {
      toast.success(
        approve
          ? saved.paid_on
            ? `${saved.number} approved and marked paid — whoever prepared it is told`
            : `${saved.number} approved, still to be paid — mark it paid once it is`
          : `${saved.number} rejected — the admins are told what to fix`,
      );
      setDeciding(null);
      setComment('');
      refresh();
    },
    // A cost that moved since the page loaded is refused with the new total;
    // show the new figures rather than the old ones.
    onError: () => refresh(),
  });

  const pay = useMutation({
    mutationFn: (paid: boolean) =>
      recordInvoicePayment(invoice.id, {
        paid,
        ...(paid
          ? { paid_on: paidOn, payment_reference: paymentRef.trim() || null }
          : { comment: comment.trim() }),
      }),
    meta: { errorFallback: 'Could not record the payment.' },
    onSuccess: (saved) => {
      toast.success(saved.paid_on ? `${saved.number} marked paid` : `${saved.number} marked not paid`);
      setPaying(null);
      setComment('');
      refresh();
    },
  });

  const openPayment = (next: 'paid' | 'unpaid') => {
    setPaidOn(today);
    setPaymentRef('');
    setComment('');
    setPaying(next);
  };

  const remove = useMutation({
    mutationFn: () => deleteInvoice(invoice.id),
    meta: { errorFallback: 'Could not delete the invoice.' },
    onSuccess: () => {
      toast.success(`${invoice.number} deleted`);
      refresh();
      navigate('/invoices');
    },
  });

  const download = useMutation({
    mutationFn: () => downloadInvoiceCsv(invoice.id),
    meta: { errorFallback: 'Could not download the invoice.' },
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${invoice.number}-${invoice.vendor_name.replace(/[^A-Za-z0-9]+/g, '-')}.csv`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      toast.success('CSV downloaded');
      // The download is in the timeline.
      refresh();
    },
  });

  const rejectReady = comment.trim().length >= 3;

  return (
    <div className="space-y-6">
      <Link to="/invoices" className="inline-flex items-center gap-1.5 text-xs text-text-muted hover:text-text">
        <ArrowLeft size={13} />
        Invoices
      </Link>

      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-mono text-2xl font-semibold tracking-tight sm:text-3xl">{invoice.number}</h1>
            <Badge tone={INVOICE_STATUS_TONES[invoice.status]}>{INVOICE_STATUS_LABELS[invoice.status]}</Badge>
            <PaymentBadge invoice={invoice} />
          </div>
          <p className="mt-1.5 text-sm text-text-muted">
            {invoice.vendor_name} · {periodText(invoice.period_start, invoice.period_end)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {invoice.can_record_payment && !invoice.paid_on && (
            <Button onClick={() => openPayment('paid')}>
              <Check size={14} />
              Mark as paid
            </Button>
          )}
          {invoice.can_decide && (
            <>
              <Button
                onClick={() => {
                  setPaidNow(false);
                  setPaidOn(today);
                  setPaymentRef('');
                  setDeciding('approve');
                }}
              >
                <Check size={14} />
                Approve
              </Button>
              <Button variant="danger" onClick={() => setDeciding('reject')}>
                <X size={14} />
                Reject
              </Button>
            </>
          )}
          {invoice.can_edit && (
            <Button variant="secondary" onClick={() => navigate(`/invoices/${invoice.id}/edit`)}>
              <Pencil size={14} />
              Edit
            </Button>
          )}
          {invoice.can_submit && (
            <Button loading={submit.isPending} onClick={() => submit.mutate()}>
              <Send size={14} />
              Submit for approval
            </Button>
          )}
          <Button variant="secondary" loading={download.isPending} onClick={() => download.mutate()}>
            <Download size={14} />
            CSV
          </Button>
          <Button
            variant="secondary"
            onClick={() => window.open(`/invoices/${invoice.id}/print`, '_blank', 'noopener')}
            title="Opens a printable page - choose Save as PDF in the print dialog"
          >
            <FileText size={14} />
            PDF
          </Button>
          {invoice.can_delete && (
            <Button variant="ghost" onClick={() => setRemoving(true)}>
              <Trash2 size={14} />
              Delete
            </Button>
          )}
        </div>
      </div>

      <StatusNote
        invoice={invoice}
        role={role}
        onUnpaid={invoice.can_record_payment && invoice.paid_on ? () => openPayment('unpaid') : undefined}
      />

      {problems.length > 0 && (
        <div className="rounded-lg bg-danger-soft px-4 py-3 text-sm">
          <p className="flex items-center gap-1.5 font-medium text-danger">
            <AlertTriangle size={14} />
            {problems.length === 1 ? 'One trip' : `${problems.length} trips`} can no longer be billed
          </p>
          <p className="mt-0.5 text-text-muted">
            Edit the invoice to take {problems.length === 1 ? 'it' : 'them'} off before it can be
            {invoice.status === 'SUBMITTED' ? ' approved' : ' submitted'}.
          </p>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Details" />
          <dl className="grid gap-x-6 gap-y-3 p-4 text-sm sm:grid-cols-2 sm:p-5">
            <Fact label="Vendor">
              {invoice.vendor_name}
              <span className="block text-2xs text-text-subtle">{VENDOR_KIND_LABELS[invoice.vendor_kind]}</span>
            </Fact>
            <Fact label="GSTIN">
              {invoice.vendor_gstin ? <span className="font-mono">{invoice.vendor_gstin}</span> : '—'}
            </Fact>
            <Fact label="Period">{periodText(invoice.period_start, invoice.period_end)}</Fact>
            <Fact label="Their bill number">{invoice.vendor_invoice_ref ?? '—'}</Fact>
            <Fact label="Contact">
              {[invoice.vendor_contact_name, invoice.vendor_phone, invoice.vendor_email]
                .filter(Boolean)
                .join(' · ') || '—'}
            </Fact>
            <Fact label="Prepared by">
              {invoice.created_by_name ?? '—'}
              {invoice.updated_by_name && invoice.updated_by_name !== invoice.created_by_name && (
                <span className="block text-2xs text-text-subtle">Last edited by {invoice.updated_by_name}</span>
              )}
            </Fact>
            {invoice.notes && (
              <Fact label="Notes" className="sm:col-span-2">
                {invoice.notes}
              </Fact>
            )}
          </dl>
        </Card>

        <Card>
          <CardHeader title="Total" />
          <div className="p-4 sm:p-5">
            <p className="text-3xl font-semibold tabular-nums">{formatMoney(invoice.total_amount, true)}</p>
            <p className="mt-1 text-xs text-text-muted">
              {invoice.line_count} {invoice.line_count === 1 ? 'trip' : 'trips'} ·{' '}
              {invoice.status === 'APPROVED'
                ? 'frozen when it was approved'
                : 'follows the recorded costs until it is approved'}
            </p>
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title="Trips billed" description="Each amount is the cost recorded on the trip." />
        {invoice.lines.length === 0 ? (
          <EmptyState
            icon={<Receipt size={28} />}
            title="No trips yet"
            description={invoice.can_edit ? 'Edit the invoice to tick the trips it covers.' : undefined}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border text-2xs uppercase tracking-wide text-text-subtle">
                <tr>
                  <th className="px-4 py-2.5 font-medium sm:pl-5">Date</th>
                  <th className="px-2 py-2.5 font-medium">Traveller</th>
                  <th className="hidden px-2 py-2.5 font-medium md:table-cell">Trip</th>
                  <th className="hidden px-2 py-2.5 font-medium lg:table-cell">Reference</th>
                  <th className="px-4 py-2.5 text-right font-medium sm:pr-5">Amount</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {invoice.lines.map((line) => (
                  <tr key={line.id} className={cn(line.problem && 'bg-danger-soft/40')}>
                    <td className="whitespace-nowrap px-4 py-2.5 align-top text-xs sm:pl-5">
                      {dayLabel(line.travel_date)}
                    </td>
                    <td className="px-2 py-2.5 align-top">
                      <p className="font-medium">{line.traveller_name}</p>
                      <p className="text-2xs text-text-subtle">
                        {line.employee_code && `${line.employee_code} · `}
                        {line.project_code}
                      </p>
                      <p className="text-2xs text-text-muted md:hidden">{tripOf(line.description)}</p>
                      {line.problem && (
                        <p className="mt-1 flex items-start gap-1 text-2xs text-danger">
                          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                          {line.problem}
                        </p>
                      )}
                    </td>
                    <td className="hidden max-w-sm px-2 py-2.5 align-top text-xs text-text-muted md:table-cell">
                      {tripOf(line.description)}
                    </td>
                    <td className="hidden px-2 py-2.5 align-top text-xs lg:table-cell">
                      {line.booking_reference ?? '—'}
                      <span className="block text-2xs text-text-subtle">Request {line.request_id}</span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5 text-right align-top tabular-nums sm:pr-5">
                      {formatMoney(line.amount, true)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t border-border">
                <tr>
                  <td className="px-4 py-3 text-xs font-medium sm:pl-5" colSpan={2}>
                    Total
                  </td>
                  <td className="hidden md:table-cell" />
                  <td className="hidden lg:table-cell" />
                  <td className="whitespace-nowrap px-4 py-3 text-right font-semibold tabular-nums sm:pr-5">
                    {formatMoney(invoice.total_amount, true)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader title="Timeline" description="From the activity log: every step, with who took it." />
        <Timeline events={invoice.history} />
      </Card>

      <Modal
        open={deciding !== null}
        onClose={() => setDeciding(null)}
        title={deciding === 'approve' ? `Approve ${invoice.number}` : `Reject ${invoice.number}`}
        description={
          deciding === 'approve'
            ? `${formatMoney(invoice.total_amount, true)} to ${invoice.vendor_name} for ${invoice.line_count} ${invoice.line_count === 1 ? 'trip' : 'trips'}. Once approved it cannot change, and the costs on it are locked. Paying it is a separate step - now, or later.`
            : 'It goes back to the admins to fix and submit again. They are shown your comment.'
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeciding(null)}>
              Cancel
            </Button>
            {deciding === 'approve' ? (
              <Button
                loading={decide.isPending}
                disabled={paidNow && paidOn > today}
                onClick={() => decide.mutate(true)}
              >
                <Check size={14} />
                {paidNow ? 'Approve and mark paid' : 'Approve, not paid yet'}
              </Button>
            ) : (
              <Button
                variant="danger"
                loading={decide.isPending}
                disabled={!rejectReady}
                onClick={() => decide.mutate(false)}
              >
                <X size={14} />
                Reject invoice
              </Button>
            )}
          </>
        }
      >
        {deciding === 'approve' && (
          <div className="mb-4 space-y-3">
            <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Payment">
              {[
                { value: false, title: 'Approved, not paid yet', hint: 'Mark it paid once the money goes.' },
                { value: true, title: 'Approved and paid', hint: 'The money has already gone.' },
              ].map((choice) => (
                <button
                  key={String(choice.value)}
                  type="button"
                  role="radio"
                  aria-checked={paidNow === choice.value}
                  onClick={() => setPaidNow(choice.value)}
                  className={cn(
                    'rounded-md border px-3 py-2.5 text-left transition-colors',
                    paidNow === choice.value
                      ? 'border-primary bg-surface-sunken'
                      : 'border-border hover:border-border-strong',
                  )}
                >
                  <span className="block text-sm font-medium">{choice.title}</span>
                  <span className="block text-2xs text-text-subtle">{choice.hint}</span>
                </button>
              ))}
            </div>
            {paidNow && (
              <PaymentFields
                paidOn={paidOn}
                reference={paymentRef}
                today={today}
                onPaidOn={setPaidOn}
                onReference={setPaymentRef}
              />
            )}
          </div>
        )}
        <Field
          label={deciding === 'approve' ? 'Comment' : 'What needs fixing'}
          htmlFor="invoice-comment"
          required={deciding === 'reject'}
          hint={
            deciding === 'approve'
              ? 'Optional. Whoever prepared it is sent it.'
              : 'Required. Whoever prepared it is sent it.'
          }
        >
          <textarea
            id="invoice-comment"
            value={comment}
            maxLength={500}
            rows={3}
            onChange={(e) => setComment(e.target.value)}
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-base text-text shadow-sm placeholder:text-text-subtle hover:border-border-strong focus:border-border-strong sm:text-sm"
            placeholder={
              deciding === 'approve' ? 'Matches their bill' : 'Two fares differ from their bill'
            }
          />
        </Field>
      </Modal>

      <Modal
        open={paying !== null}
        onClose={() => setPaying(null)}
        title={paying === 'paid' ? `Mark ${invoice.number} paid` : `${invoice.number} is not paid after all`}
        description={
          paying === 'paid'
            ? `${formatMoney(invoice.total_amount, true)} to ${invoice.vendor_name}. Whoever prepared it is told.`
            : 'For a payment recorded by mistake, or one the bank returned. The change and your reason are kept in the log.'
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setPaying(null)}>
              Cancel
            </Button>
            {paying === 'paid' ? (
              <Button loading={pay.isPending} disabled={paidOn > today} onClick={() => pay.mutate(true)}>
                <Check size={14} />
                Mark paid
              </Button>
            ) : (
              <Button
                variant="danger"
                loading={pay.isPending}
                disabled={comment.trim().length < 3}
                onClick={() => pay.mutate(false)}
              >
                Mark not paid
              </Button>
            )}
          </>
        }
      >
        {paying === 'paid' ? (
          <PaymentFields
            paidOn={paidOn}
            reference={paymentRef}
            today={today}
            onPaidOn={setPaidOn}
            onReference={setPaymentRef}
          />
        ) : (
          <Field label="Why?" htmlFor="unpaid-reason" required>
            <textarea
              id="unpaid-reason"
              value={comment}
              maxLength={500}
              rows={3}
              onChange={(e) => setComment(e.target.value)}
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-base text-text shadow-sm placeholder:text-text-subtle hover:border-border-strong focus:border-border-strong sm:text-sm"
              placeholder="The bank returned the transfer"
            />
          </Field>
        )}
      </Modal>

      <ConfirmDialog
        open={removing}
        title={`Delete ${invoice.number}?`}
        confirmLabel="Delete invoice"
        loading={remove.isPending}
        onConfirm={() => remove.mutate()}
        onClose={() => setRemoving(false)}
      >
        <p>
          Its trips become free to go on another invoice. The number is not given out again, and the
          deletion is recorded in the activity log.
        </p>
      </ConfirmDialog>
    </div>
  );
}

/** "Ravi Kumar · Flight: ... · CMP-2026-0002" without the name and campaign,
 *  which have their own columns. */
function tripOf(description: string): string {
  const parts = description.split(' · ');
  return parts.length >= 3 ? parts.slice(1, -1).join(' · ') : description;
}

function Fact({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('min-w-0', className)}>
      <dt className="text-2xs uppercase tracking-wide text-text-subtle">{label}</dt>
      <dd className="mt-0.5 break-words">{children}</dd>
    </div>
  );
}

/** What happens next, in the words of the person looking. */
function StatusNote({
  invoice,
  role,
  onUnpaid,
}: {
  invoice: Invoice;
  role: string | undefined;
  /** A super admin correcting a payment recorded by mistake. */
  onUnpaid?: () => void;
}) {
  if (invoice.status === 'APPROVED') {
    return (
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg bg-success-soft px-4 py-3 text-sm">
          <p className="font-medium text-success">
            Approved by {invoice.decided_by_name ?? 'a super admin'}
            {invoice.decided_at && ` on ${formatInstant(invoice.decided_at)}`}
          </p>
          {invoice.decision_comment && <p className="mt-0.5 text-text-muted">{invoice.decision_comment}</p>}
        </div>
        {invoice.paid_on ? (
          <div className="rounded-lg bg-success-soft px-4 py-3 text-sm">
            <p className="font-medium text-success">
              Paid on {dayLabel(invoice.paid_on)}
              {invoice.payment_reference && ` · ${invoice.payment_reference}`}
            </p>
            <p className="mt-0.5 text-text-muted">
              Recorded by {invoice.paid_by_name ?? 'a super admin'}
              {invoice.paid_at && ` on ${formatInstant(invoice.paid_at)}`}.
              {onUnpaid && (
                <button
                  type="button"
                  onClick={onUnpaid}
                  className="ml-1 text-xs underline underline-offset-2 hover:text-text"
                >
                  Not paid after all?
                </button>
              )}
            </p>
          </div>
        ) : (
          <div className="rounded-lg bg-warning-soft px-4 py-3 text-sm">
            <p className="font-medium text-warning">Not paid yet</p>
            <p className="mt-0.5 text-text-muted">
              {role === 'SUPER_ADMIN'
                ? 'Use Mark as paid once the money has gone to the vendor.'
                : 'A super admin marks it paid once the money has gone.'}
            </p>
          </div>
        )}
      </div>
    );
  }
  if (invoice.decision_comment && (invoice.status === 'REJECTED' || invoice.status === 'DRAFT')) {
    return (
      <div className="rounded-lg bg-danger-soft px-4 py-3 text-sm">
        <p className="font-medium text-danger">
          {invoice.status === 'REJECTED' ? 'Rejected' : 'Last rejected'} by{' '}
          {invoice.decided_by_name ?? 'a super admin'}
          {invoice.decided_at && ` on ${formatInstant(invoice.decided_at)}`}
        </p>
        <p className="mt-0.5 text-text-muted">{invoice.decision_comment}</p>
        {invoice.can_edit && (
          <p className="mt-1 text-xs text-text-subtle">Fix it, then submit it again.</p>
        )}
      </div>
    );
  }
  if (invoice.status === 'SUBMITTED') {
    const text = invoice.can_decide
      ? 'Waiting for your decision. Check the trips against the vendor’s bill, then approve or reject.'
      : role === 'SUPER_ADMIN'
        ? 'You helped prepare this invoice, so another super admin must decide it.'
        : `Waiting for a super admin${invoice.submitted_by_name ? ` - sent by ${invoice.submitted_by_name}` : ''}${invoice.submitted_at ? ` on ${formatInstant(invoice.submitted_at)}` : ''}. It can still be edited; they are told if it changes.`;
    return <div className="rounded-lg bg-warning-soft px-4 py-3 text-sm text-text-muted">{text}</div>;
  }
  return null;
}

function Timeline({ events }: { events: InvoiceEvent[] }) {
  if (events.length === 0) {
    return <p className="p-5 text-sm text-text-muted">Nothing recorded yet.</p>;
  }
  return (
    <ol className="space-y-0 p-4 sm:p-5">
      {events.map((event, index) => (
        <li key={index} className="relative flex gap-3 pb-4 last:pb-0">
          {index < events.length - 1 && (
            <span className="absolute left-[7px] top-4 h-full w-px bg-border" aria-hidden />
          )}
          <span
            className={cn(
              'relative mt-1 h-3.5 w-3.5 shrink-0 rounded-full border-2 border-surface',
              event.action === 'APPROVE'
                ? 'bg-success'
                : event.action === 'REJECT'
                  ? 'bg-danger'
                  : event.action === 'SUBMIT'
                    ? 'bg-warning'
                    : 'bg-border-strong',
            )}
            aria-hidden
          />
          <div className="min-w-0">
            <p className="text-sm">
              <span className="font-medium">{INVOICE_EVENT_LABELS[event.action] ?? event.action}</span>
              {event.actor_name && <span className="text-text-muted"> by {event.actor_name}</span>}
            </p>
            <p className="text-2xs text-text-subtle">{formatInstant(event.at)}</p>
            <p className="mt-0.5 text-xs text-text-muted">{event.summary}</p>
            {event.comment && (
              <p className="mt-1 rounded-md bg-surface-sunken px-2.5 py-1.5 text-xs">“{event.comment}”</p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
