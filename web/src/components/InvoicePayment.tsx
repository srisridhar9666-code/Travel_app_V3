import { Badge, Field, Input } from '@/components/ui';
import { dayLabel } from '@/lib/invoices';
import type { InvoiceSummary } from '@/types';

/**
 * Approved is not paid. An approved invoice is either still to be paid or paid
 * on a given day, and the super admin records which - when approving, or later
 * when the money goes. These are the pieces the list and the detail share.
 */

/** "Paid" or "Not paid yet" beside an approved invoice; nothing otherwise. */
export function PaymentBadge({ invoice }: { invoice: Pick<InvoiceSummary, 'status' | 'paid_on'> }) {
  if (invoice.status !== 'APPROVED') return null;
  return invoice.paid_on ? (
    <Badge tone="success">Paid {dayLabel(invoice.paid_on)}</Badge>
  ) : (
    <Badge tone="warning">Not paid yet</Badge>
  );
}

/** The day the money went and the bank's reference for it. */
export function PaymentFields({
  paidOn,
  reference,
  today,
  onPaidOn,
  onReference,
}: {
  paidOn: string;
  reference: string;
  /** India's today: a payment is recorded once it has happened. */
  today: string;
  onPaidOn: (value: string) => void;
  onReference: (value: string) => void;
}) {
  const future = paidOn > today;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field
        label="Paid on"
        htmlFor="paid-on"
        required
        error={future ? 'Record it once it is paid.' : undefined}
      >
        <Input
          id="paid-on"
          type="date"
          value={paidOn}
          max={today}
          onChange={(e) => onPaidOn(e.target.value)}
        />
      </Field>
      <Field label="Payment reference" htmlFor="paid-ref" hint="UTR, cheque or transfer number.">
        <Input
          id="paid-ref"
          value={reference}
          maxLength={80}
          onChange={(e) => onReference(e.target.value)}
          placeholder="UTR 2026100512345"
        />
      </Field>
    </div>
  );
}
