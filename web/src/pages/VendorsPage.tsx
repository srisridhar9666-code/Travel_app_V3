import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Pencil, Plus, Power, Store } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import toast from 'react-hot-toast';

import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Modal } from '@/components/Modal';
import {
  Badge,
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
import { createVendor, errorMessage, setVendorActive, updateVendor } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import {
  VENDOR_KINDS,
  VENDOR_KIND_LABELS,
  isInvoiceEditor,
  type Vendor,
  type VendorKind,
  type VendorPayload,
} from '@/types';

/**
 * The travel agents, cab operators and hotels the organisation pays.
 *
 * Admins and system admins keep the list; the super admin reads it, because
 * they approve the invoices raised against these vendors. A vendor is switched
 * off rather than deleted - past costs and invoices still name it.
 */

interface Draft {
  name: string;
  kind: VendorKind;
  contact_name: string;
  phone: string;
  email: string;
  gstin: string;
  notes: string;
}

const EMPTY: Draft = {
  name: '',
  kind: 'TRAVEL_AGENT',
  contact_name: '',
  phone: '',
  email: '',
  gstin: '',
  notes: '',
};

const draftFrom = (vendor: Vendor): Draft => ({
  name: vendor.name,
  kind: vendor.kind,
  contact_name: vendor.contact_name ?? '',
  phone: vendor.phone ?? '',
  email: vendor.email ?? '',
  gstin: vendor.gstin ?? '',
  notes: vendor.notes ?? '',
});

/** Blanks are sent as null, so clearing a field clears it. */
const payloadFrom = (draft: Draft): VendorPayload => ({
  name: draft.name.trim(),
  kind: draft.kind,
  contact_name: draft.contact_name.trim() || null,
  phone: draft.phone.trim() || null,
  email: draft.email.trim() || null,
  gstin: draft.gstin.replace(/\s+/g, '').toUpperCase() || null,
  notes: draft.notes.trim() || null,
});

/** The server's rule, checked as they type: 15 letters and digits. */
const gstinProblem = (gstin: string) => {
  const cleaned = gstin.replace(/\s+/g, '');
  if (!cleaned) return null;
  return /^[0-9A-Za-z]{15}$/.test(cleaned)
    ? null
    : 'A GSTIN is 15 letters and digits, e.g. 36AABCD1234E1Z5.';
};

export default function VendorsPage() {
  const queryClient = useQueryClient();
  const role = useAuth((s) => s.user?.role);
  const canEdit = isInvoiceEditor(role);
  const vendors = useVendors();

  const [editing, setEditing] = useState<Vendor | 'new' | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [switching, setSwitching] = useState<Vendor | null>(null);
  const [showOff, setShowOff] = useState(false);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['vendors'] });

  const save = useMutation({
    mutationFn: () =>
      editing === 'new' || editing === null
        ? createVendor(payloadFrom(draft))
        : updateVendor(editing.id, payloadFrom(draft)),
    meta: { errorFallback: 'Could not save the vendor.' },
    onSuccess: (saved) => {
      toast.success(editing === 'new' ? `${saved.name} added` : `${saved.name} saved`);
      setEditing(null);
      refresh();
    },
  });

  const toggle = useMutation({
    mutationFn: (vendor: Vendor) => setVendorActive(vendor.id, !vendor.is_active),
    meta: { errorFallback: 'Could not change the vendor.' },
    onSuccess: (saved) => {
      toast.success(saved.is_active ? `${saved.name} switched back on` : `${saved.name} switched off`);
      setSwitching(null);
      refresh();
    },
  });

  const open = (vendor: Vendor | 'new') => {
    setDraft(vendor === 'new' ? EMPTY : draftFrom(vendor));
    setEditing(vendor);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (draft.name.trim().length >= 2 && !gstinProblem(draft.gstin)) save.mutate();
  };

  const all = vendors.data ?? [];
  const offCount = all.filter((v) => !v.is_active).length;
  const rows = showOff ? all : all.filter((v) => v.is_active);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Vendors"
        description={
          canEdit
            ? 'The travel agents, cab operators and hotels you pay. Record one with each cost; invoices are raised against them.'
            : 'The travel agents, cab operators and hotels the organisation pays. Admins keep this list; you approve the invoices raised against it.'
        }
        actions={
          canEdit && (
            <Button onClick={() => open('new')}>
              <Plus size={15} />
              Add vendor
            </Button>
          )
        }
      />

      <Card>
        <CardHeader
          title={`${rows.length} ${rows.length === 1 ? 'vendor' : 'vendors'}`}
          description="A vendor is switched off rather than deleted: past costs and invoices still name it."
          action={
            offCount > 0 && (
              <label className="flex items-center gap-2 text-xs text-text-muted">
                <input
                  type="checkbox"
                  checked={showOff}
                  onChange={(e) => setShowOff(e.target.checked)}
                  className="h-4 w-4 accent-[rgb(var(--primary))]"
                />
                Show switched off ({offCount})
              </label>
            )
          }
        />
        {vendors.isPending ? (
          <div className="space-y-2 p-5">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : vendors.isError ? (
          <EmptyState
            icon={<Store size={28} />}
            title="Could not load the vendors"
            description={errorMessage(vendors.error)}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<Store size={28} />}
            title="No vendors yet"
            description={
              canEdit
                ? 'Add the travel agents, cab operators and hotels you pay.'
                : 'An admin adds them.'
            }
            action={
              canEdit && (
                <Button onClick={() => open('new')}>
                  <Plus size={15} />
                  Add vendor
                </Button>
              )
            }
          />
        ) : (
          // Rounded at the foot, so the banded rows keep the card's corners.
          <div className="overflow-x-auto rounded-b-xl">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border text-2xs uppercase tracking-wide text-text-subtle">
                <tr>
                  <th className="px-4 py-2.5 font-medium sm:px-5">Vendor</th>
                  <th className="hidden px-4 py-2.5 font-medium md:table-cell">Contact</th>
                  <th className="hidden px-4 py-2.5 font-medium lg:table-cell">Used on</th>
                  <th className="hidden px-4 py-2.5 font-medium sm:table-cell">Status</th>
                  {canEdit && <th className="px-4 py-2.5 sm:px-5" aria-label="Actions" />}
                </tr>
              </thead>
              {/* Banded: a vendor runs to several lines, and the band shows
                  where one ends and the next begins. */}
              <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                {rows.map((vendor) => (
                  <tr key={vendor.id} className={cn(!vendor.is_active && 'text-text-muted')}>
                    <td className="px-4 py-3 align-top sm:px-5">
                      <p className="font-medium">{vendor.name}</p>
                      <p className="text-2xs text-text-subtle">
                        {VENDOR_KIND_LABELS[vendor.kind]}
                        {vendor.gstin && <> · GSTIN <span className="font-mono">{vendor.gstin}</span></>}
                      </p>
                      {vendor.notes && (
                        <p className="mt-0.5 max-w-sm text-2xs text-text-subtle">{vendor.notes}</p>
                      )}
                      <Badge tone={vendor.is_active ? 'success' : 'neutral'} className="mt-1 sm:hidden">
                        {vendor.is_active ? 'Active' : 'Switched off'}
                      </Badge>
                    </td>
                    <td className="hidden px-4 py-3 align-top text-xs md:table-cell">
                      {vendor.contact_name && <p>{vendor.contact_name}</p>}
                      {vendor.phone && <p className="text-text-muted">{vendor.phone}</p>}
                      {vendor.email && <p className="text-text-muted">{vendor.email}</p>}
                      {!vendor.contact_name && !vendor.phone && !vendor.email && (
                        <span className="text-text-subtle">—</span>
                      )}
                    </td>
                    <td className="hidden px-4 py-3 align-top text-xs text-text-muted lg:table-cell">
                      {vendor.traveller_count} {vendor.traveller_count === 1 ? 'trip' : 'trips'} ·{' '}
                      {vendor.invoice_count} {vendor.invoice_count === 1 ? 'invoice' : 'invoices'}
                    </td>
                    <td className="hidden px-4 py-3 align-top sm:table-cell">
                      <Badge tone={vendor.is_active ? 'success' : 'neutral'}>
                        {vendor.is_active ? 'Active' : 'Switched off'}
                      </Badge>
                    </td>
                    {canEdit && (
                      <td className="px-4 py-3 align-top sm:px-5">
                        {/* Stacked on a phone, so neither button is cut off. */}
                        <div className="flex flex-col items-end gap-1.5 sm:flex-row sm:justify-end">
                          <Button size="sm" variant="secondary" onClick={() => open(vendor)}>
                            <Pencil size={13} />
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() =>
                              vendor.is_active ? setSwitching(vendor) : toggle.mutate(vendor)
                            }
                            loading={toggle.isPending && toggle.variables?.id === vendor.id}
                          >
                            <Power size={13} />
                            {vendor.is_active ? 'Switch off' : 'Switch on'}
                          </Button>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === 'new' || editing === null ? 'Add a vendor' : `Edit ${editing.name}`}
        description="Every change is recorded in the activity log."
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button
              type="submit"
              form="vendor-form"
              loading={save.isPending}
              disabled={draft.name.trim().length < 2 || !!gstinProblem(draft.gstin)}
            >
              {editing === 'new' ? 'Add vendor' : 'Save'}
            </Button>
          </>
        }
      >
        <form id="vendor-form" onSubmit={submit} className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name" htmlFor="vendor-name" required>
              <Input
                id="vendor-name"
                value={draft.name}
                maxLength={160}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="Sai Travels"
              />
            </Field>
            <Field label="Kind" htmlFor="vendor-kind">
              <Select
                id="vendor-kind"
                value={draft.kind}
                onChange={(e) => setDraft({ ...draft, kind: e.target.value as VendorKind })}
              >
                {VENDOR_KINDS.map((kind) => (
                  <option key={kind} value={kind}>
                    {VENDOR_KIND_LABELS[kind]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Contact person" htmlFor="vendor-contact">
              <Input
                id="vendor-contact"
                value={draft.contact_name}
                maxLength={120}
                onChange={(e) => setDraft({ ...draft, contact_name: e.target.value })}
              />
            </Field>
            <Field label="Phone" htmlFor="vendor-phone">
              <Input
                id="vendor-phone"
                type="tel"
                inputMode="tel"
                value={draft.phone}
                maxLength={32}
                onChange={(e) => setDraft({ ...draft, phone: e.target.value })}
                placeholder="+91 98765 43210"
              />
            </Field>
            <Field label="Email" htmlFor="vendor-email">
              <Input
                id="vendor-email"
                type="email"
                value={draft.email}
                maxLength={255}
                onChange={(e) => setDraft({ ...draft, email: e.target.value })}
                placeholder="accounts@saitravels.in"
              />
            </Field>
            <Field
              label="GSTIN"
              htmlFor="vendor-gstin"
              error={gstinProblem(draft.gstin)}
              hint="Optional. Printed on their invoices."
            >
              <Input
                id="vendor-gstin"
                value={draft.gstin}
                maxLength={20}
                autoCapitalize="characters"
                onChange={(e) => setDraft({ ...draft, gstin: e.target.value })}
                placeholder="36AABCD1234E1Z5"
                aria-invalid={!!gstinProblem(draft.gstin)}
                className="font-mono"
              />
            </Field>
          </div>
          <Field label="Notes" htmlFor="vendor-notes">
            <Input
              id="vendor-notes"
              value={draft.notes}
              maxLength={500}
              onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
              placeholder="Billing cycle, account details, who to call"
            />
          </Field>
        </form>
      </Modal>

      <ConfirmDialog
        open={switching !== null}
        title={`Switch off ${switching?.name ?? 'this vendor'}?`}
        confirmLabel="Switch off"
        tone="primary"
        loading={toggle.isPending}
        onConfirm={() => switching && toggle.mutate(switching)}
        onClose={() => setSwitching(null)}
      >
        <p>
          They will no longer be offered when recording a cost. Costs already paid to them keep
          their name, and their invoices can still be finished. You can switch them back on.
        </p>
      </ConfirmDialog>
    </div>
  );
}
