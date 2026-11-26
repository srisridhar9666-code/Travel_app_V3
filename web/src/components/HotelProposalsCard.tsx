import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Building2,
  CheckCircle2,
  ExternalLink,
  MapPin,
  Plus,
  Trash2,
  Sparkles,
} from 'lucide-react';
import toast from 'react-hot-toast';

import { Modal } from '@/components/Modal';
import { Button, Input, Field } from '@/components/ui';
import {
  fetchHotelProposals,
  fetchSavedHotels,
  selectHotelProposal,
  submitHotelProposals,
} from '@/lib/api';
import { useAuth } from '@/store/auth';
import type {
  HotelProposal,
  HotelProposalCreateItem,
  RequestStatus,
} from '@/types';

const formatInr = (val: number | string) =>
  `₹${Number(val || 0).toLocaleString('en-IN')}`;

interface HotelProposalsCardProps {
  requestId: number;
  requestStatus: RequestStatus;
  requesterId: number;
  onUpdated?: () => void;
}

export function HotelProposalsCard({
  requestId,
  requestStatus,
  requesterId,
  onUpdated,
}: HotelProposalsCardProps) {
  const qc = useQueryClient();
  const me = useAuth((s) => s.user);
  const [modalOpen, setModalOpen] = useState(false);

  const { data: proposals = [], isLoading } = useQuery<HotelProposal[]>({
    queryKey: ['hotel-proposals', requestId],
    queryFn: () => fetchHotelProposals(requestId),
  });

  const selectMutation = useMutation({
    mutationFn: (proposalId: number) => selectHotelProposal(requestId, proposalId),
    onSuccess: (updated) => {
      toast.success(`Selected ${updated.hotel_name} as preferred hotel`);
      qc.invalidateQueries({ queryKey: ['hotel-proposals', requestId] });
      qc.invalidateQueries({ queryKey: ['requests'] });
      qc.invalidateQueries({ queryKey: ['request', requestId] });
      onUpdated?.();
    },
    onError: (err: any) => {
      const msg = err?.response?.data?.detail || 'Failed to select hotel proposal';
      toast.error(msg);
    },
  });

  const isAdmin = me?.role === 'ADMIN' || me?.role === 'SUPER_ADMIN' || me?.role === 'SYSTEM_ADMIN';
  const canSeeRates = isAdmin || me?.role === 'VENDOR' || me?.role === 'FINANCE' || me?.role === 'MANAGER';
  const isVendor = me?.role === 'VENDOR';
  const isRequester = me?.id === requesterId;

  const canSelect =
    (isRequester || isAdmin) &&
    ['SUBMITTED', 'PARTIALLY_APPROVED', 'APPROVED', 'READY_FOR_VENDOR'].includes(requestStatus);

  const canSubmit =
    (isVendor || isAdmin) &&
    ['SUBMITTED', 'PARTIALLY_APPROVED', 'APPROVED', 'READY_FOR_VENDOR'].includes(requestStatus);

  const hasSelected = proposals.some((p) => p.status === 'SELECTED');

  if (isLoading) {
    return (
      <div className="mt-4 rounded-xl border border-border/60 bg-surface/40 p-4 text-xs text-text-muted animate-pulse">
        Loading hotel proposals...
      </div>
    );
  }

  if (proposals.length === 0 && !canSubmit) {
    return null;
  }

  return (
    <div className="mt-4 rounded-xl border border-border/80 bg-surface/70 backdrop-blur-xs p-4 shadow-2xs">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/50 pb-3">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Building2 size={16} />
          </div>
          <div>
            <h4 className="text-sm font-semibold text-text flex items-center gap-2">
              Hotel Sourcing Options
              {proposals.length > 0 && (
                <span className="rounded-full bg-surface-raised px-2 py-0.5 text-2xs font-medium text-text-muted border border-border/60">
                  {proposals.length} options
                </span>
              )}
            </h4>
            <p className="text-2xs text-text-subtle">
              {hasSelected
                ? 'A preferred hotel proposal has been selected for booking.'
                : 'Curated corporate lodging options evaluated against city policy rate caps.'}
            </p>
          </div>
        </div>

        {canSubmit && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setModalOpen(true)}
            className="gap-1.5 text-xs"
          >
            <Plus size={13} />
            Submit Proposals
          </Button>
        )}
      </div>

      {proposals.length === 0 ? (
        <div className="py-6 text-center text-xs text-text-muted">
          No hotel proposals have been submitted yet. Awaiting vendor sourcing.
        </div>
      ) : (
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {proposals.map((prop) => {
            const isSelected = prop.status === 'SELECTED';
            return (
              <div
                key={prop.id}
                className={`relative flex flex-col justify-between rounded-xl border p-3.5 transition-all ${
                  isSelected
                    ? 'border-emerald-500/80 bg-emerald-50/50 shadow-xs dark:border-emerald-500/40 dark:bg-emerald-950/20'
                    : 'border-border/70 bg-surface hover:border-border hover:shadow-2xs'
                }`}
              >
                <div>
                  <div className="flex items-start justify-between gap-2">
                    <h5 className="font-semibold text-sm text-text leading-tight">
                      {prop.hotel_name}
                    </h5>
                    {isSelected && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-2xs font-semibold text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-300">
                        <CheckCircle2 size={11} /> Selected
                      </span>
                    )}
                  </div>

                  <p className="mt-0.5 text-2xs text-text-muted font-medium">
                    {prop.room_category} Room
                  </p>

                  <div className="mt-2 flex items-start gap-1.5 text-2xs text-text-muted">
                    <MapPin size={12} className="mt-0.5 shrink-0 text-text-subtle" />
                    <span>
                      {prop.address}
                      {prop.landmark && (
                        <span className="text-text-subtle"> (near {prop.landmark})</span>
                      )}
                    </span>
                  </div>

                  {prop.google_maps_url && (
                    <a
                      href={prop.google_maps_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-1.5 inline-flex items-center gap-1 text-2xs font-medium text-primary hover:underline"
                    >
                      View on Google Maps <ExternalLink size={10} />
                    </a>
                  )}

                  {/* Amenities */}
                  {prop.amenities && prop.amenities.length > 0 && (
                    <div className="mt-2.5 flex flex-wrap gap-1">
                      {prop.amenities.map((item, idx) => (
                        <span
                          key={idx}
                          className="inline-flex items-center gap-1 rounded-md bg-surface-raised px-1.5 py-0.5 text-2xs text-text-muted border border-border/50"
                        >
                          <Sparkles size={10} className="text-amber-500" />
                          {item}
                        </span>
                      ))}
                    </div>
                  )}

                  {prop.vendor_notes && (
                    <p className="mt-2 text-2xs italic text-text-subtle border-t border-border/40 pt-1.5">
                      Note: "{prop.vendor_notes}"
                    </p>
                  )}
                </div>

                {((canSeeRates && prop.nightly_rate != null) || (canSelect && !isSelected)) && (
                <div className="mt-4 pt-3 border-t border-border/60">
                  {canSeeRates && prop.nightly_rate != null && (
                    <>
                      <div className="flex items-baseline justify-between">
                        <div>
                          <span className="text-base font-bold text-text">
                            {formatInr(prop.nightly_rate)}
                          </span>
                          <span className="text-2xs text-text-muted"> / night</span>
                        </div>

                        {/* Policy Compliance Pill */}
                        {(prop.is_within_policy ?? prop.is_policy_compliant ?? true) ? (
                          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-2xs font-medium text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300">
                            <CheckCircle2 size={10} /> Within Policy
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-2xs font-medium text-amber-900 dark:bg-amber-950/60 dark:text-amber-300">
                            <AlertTriangle size={10} /> +{formatInr(prop.excess_per_night ?? prop.excess_amount ?? 0)} excess
                          </span>
                        )}
                      </div>

                      {(prop.policy_limit_per_night ?? prop.policy_rate_limit) && (
                        <p className="mt-0.5 text-2xs text-text-subtle">
                          City Cap: {formatInr(prop.policy_limit_per_night ?? prop.policy_rate_limit ?? 0)}/night
                        </p>
                      )}
                    </>
                  )}

                  {/* Select button */}
                  {canSelect && !isSelected && (
                    <Button
                      size="sm"
                      variant="primary"
                      className={canSeeRates && prop.nightly_rate != null ? 'mt-3 w-full text-xs' : 'w-full text-xs'}
                      loading={selectMutation.isPending && selectMutation.variables === prop.id}
                      onClick={() => selectMutation.mutate(prop.id)}
                    >
                      Select Preferred Hotel
                    </Button>
                  )}
                </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Vendor / Admin Multi-Proposal Form Modal */}
      {modalOpen && (
        <HotelProposalsModal
          requestId={requestId}
          isOpen={modalOpen}
          onClose={() => setModalOpen(false)}
          onSuccess={() => {
            setModalOpen(false);
            qc.invalidateQueries({ queryKey: ['hotel-proposals', requestId] });
            qc.invalidateQueries({ queryKey: ['requests'] });
            onUpdated?.();
          }}
        />
      )}
    </div>
  );
}

function HotelProposalsModal({
  requestId,
  isOpen,
  onClose,
  onSuccess,
}: {
  requestId: number;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const { data: savedHotels = [] } = useQuery({
    queryKey: ['saved-hotels'],
    queryFn: () => fetchSavedHotels(),
  });

  const [items, setItems] = useState<HotelProposalCreateItem[]>([
    {
      hotel_name: '',
      address: '',
      landmark: '',
      google_maps_url: '',
      room_category: 'Standard',
      nightly_rate: 0,
      amenities: [],
      vendor_notes: '',
      save_to_directory: false,
    },
    {
      hotel_name: '',
      address: '',
      landmark: '',
      google_maps_url: '',
      room_category: 'Deluxe',
      nightly_rate: 0,
      amenities: [],
      vendor_notes: '',
      save_to_directory: false,
    },
  ]);

  const submitMutation = useMutation({
    mutationFn: () => submitHotelProposals(requestId, items),
    onSuccess: (res) => {
      toast.success(`Successfully submitted ${res.length} hotel proposals!`);
      onSuccess();
    },
    onError: (err: any) => {
      const msg = err?.response?.data?.detail || 'Failed to submit proposals';
      toast.error(msg);
    },
  });

  const updateItem = (index: number, patch: Partial<HotelProposalCreateItem>) => {
    setItems((prev) =>
      prev.map((it, i) => (i === index ? { ...it, ...patch } : it))
    );
  };

  const addItem = () => {
    if (items.length >= 5) {
      toast.error('Maximum 5 proposals per request');
      return;
    }
    setItems((prev) => [
      ...prev,
      {
        hotel_name: '',
        address: '',
        landmark: '',
        google_maps_url: '',
        room_category: 'Standard',
        nightly_rate: 0,
        amenities: [],
        vendor_notes: '',
      },
    ]);
  };

  const removeItem = (index: number) => {
    if (items.length <= 1) {
      toast.error('At least 1 hotel proposal option is required');
      return;
    }
    setItems((prev) => prev.filter((_, i) => i !== index));
  };

  const isFormValid = items.every(
    (it) => it.hotel_name.trim() && it.address.trim() && it.nightly_rate > 0
  );

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title="Submit Hotel Sourcing Proposals"
      description="Curate between 1 and 5 hotel options for the employee/admin to review and select."
      className="max-w-3xl"
    >
      <div className="space-y-4 max-h-[70vh] overflow-y-auto px-1">
        {items.map((item, idx) => (
          <div
            key={idx}
            className="rounded-xl border border-border/70 bg-surface/80 p-4 relative"
          >
            <div className="flex items-center justify-between border-b border-border/40 pb-2 mb-3">
              <span className="text-xs font-semibold text-text flex items-center gap-1.5">
                <Building2 size={13} className="text-primary" />
                Option #{idx + 1}
              </span>
              {items.length > 1 && (
                <button
                  type="button"
                  onClick={() => removeItem(idx)}
                  className="text-text-subtle hover:text-danger p-1 rounded transition-colors"
                >
                  <Trash2 size={13} />
                </button>
              )}
            </div>

            {savedHotels.length > 0 && (
              <div className="mb-3 rounded-lg border border-border/70 bg-surface-raised/40 p-2 flex flex-wrap items-center justify-between gap-2">
                <span className="text-2xs font-medium text-text-subtle flex items-center gap-1">
                  <Sparkles size={11} className="text-primary" />
                  Auto-fill from saved directory:
                </span>
                <select
                  className="text-xs rounded border border-border bg-surface px-2 py-1 text-text focus:outline-none focus:ring-1 focus:ring-primary max-w-xs"
                  onChange={(e) => {
                    const hotelId = Number(e.target.value);
                    if (!hotelId) return;
                    const found = savedHotels.find((h) => h.id === hotelId);
                    if (found) {
                      updateItem(idx, {
                        hotel_name: found.hotel_name,
                        address: found.address,
                        landmark: found.landmark || '',
                        google_maps_url: found.google_maps_url || '',
                        room_category: found.room_category || 'Standard',
                        nightly_rate: Number(found.nightly_rate) || 0,
                        vendor_notes: found.vendor_notes || '',
                        amenities: found.amenities && found.amenities.length > 0 ? found.amenities : [],
                      });
                      toast.success(`Auto-filled ${found.hotel_name}`);
                    }
                  }}
                  defaultValue=""
                >
                  <option value="" disabled>Choose from saved hotels...</option>
                  {savedHotels.map((h) => (
                    <option key={h.id} value={h.id}>
                      {h.hotel_name} ({h.city}) — ₹{Number(h.nightly_rate).toLocaleString('en-IN')}/night
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Hotel Name" required htmlFor={`hotel-${idx}`}>
                <Input
                  id={`hotel-${idx}`}
                  value={item.hotel_name}
                  onChange={(e) => updateItem(idx, { hotel_name: e.target.value })}
                  placeholder="e.g. Lemon Tree Premier"
                />
              </Field>

              <Field label="Nightly Quoted Rate (₹)" required htmlFor={`rate-${idx}`}>
                <Input
                  id={`rate-${idx}`}
                  type="number"
                  min="1"
                  value={item.nightly_rate || ''}
                  onChange={(e) =>
                    updateItem(idx, { nightly_rate: parseFloat(e.target.value) || 0 })
                  }
                  placeholder="e.g. 1800"
                />
              </Field>

              <Field label="Room Category" htmlFor={`cat-${idx}`}>
                <Input
                  id={`cat-${idx}`}
                  value={item.room_category}
                  onChange={(e) => updateItem(idx, { room_category: e.target.value })}
                  placeholder="e.g. Standard, Executive Deluxe"
                />
              </Field>

              <Field label="Landmark / Area" htmlFor={`land-${idx}`}>
                <Input
                  id={`land-${idx}`}
                  value={item.landmark || ''}
                  onChange={(e) => updateItem(idx, { landmark: e.target.value })}
                  placeholder="e.g. Near Hitec City Metro"
                />
              </Field>

              <Field label="Full Address" required htmlFor={`addr-${idx}`} className="sm:col-span-2">
                <Input
                  id={`addr-${idx}`}
                  value={item.address}
                  onChange={(e) => updateItem(idx, { address: e.target.value })}
                  placeholder="Street address, city, pin code"
                />
              </Field>

              <Field label="Google Maps Link" htmlFor={`map-${idx}`} className="sm:col-span-2">
                <Input
                  id={`map-${idx}`}
                  value={item.google_maps_url || ''}
                  onChange={(e) => updateItem(idx, { google_maps_url: e.target.value })}
                  placeholder="https://maps.google.com/..."
                />
              </Field>

              <Field label="Amenities Included (optional)" className="sm:col-span-2">
                <div className="flex flex-wrap gap-3 pt-1">
                  {['Free Wi-Fi', 'Breakfast Included', 'AC', 'Parking', '24-hr Check-in'].map((amenity) => {
                    const checked = (item.amenities ?? []).includes(amenity);
                    return (
                      <label
                        key={amenity}
                        className="inline-flex items-center gap-1.5 cursor-pointer text-xs text-text select-none rounded-md border border-border bg-surface-sunken/50 px-2.5 py-1 hover:bg-surface-sunken"
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) => {
                            const prev = item.amenities ?? [];
                            const next = e.target.checked
                              ? [...prev, amenity]
                              : prev.filter((a) => a !== amenity);
                            updateItem(idx, { amenities: next });
                          }}
                          className="h-3.5 w-3.5 rounded accent-[rgb(var(--primary))]"
                        />
                        <span>{amenity}</span>
                      </label>
                    );
                  })}
                </div>
              </Field>

              <Field label="Vendor Notes / Inclusions" htmlFor={`notes-${idx}`} className="sm:col-span-2">
                <Input
                  id={`notes-${idx}`}
                  value={item.vendor_notes || ''}
                  onChange={(e) => updateItem(idx, { vendor_notes: e.target.value })}
                  placeholder="e.g. Complimentary breakfast, 24-hr check-in allowed"
                />
              </Field>

              <div className="sm:col-span-2 pt-1">
                <label className="flex items-center gap-2 text-xs font-medium text-text cursor-pointer">
                  <input
                    type="checkbox"
                    checked={item.save_to_directory ?? false}
                    onChange={(e) => updateItem(idx, { save_to_directory: e.target.checked })}
                    className="h-4 w-4 rounded accent-[rgb(var(--primary))]"
                  />
                  <span>Save this hotel to directory for future bookings</span>
                </label>
              </div>
            </div>
          </div>
        ))}

        {items.length < 5 && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={addItem}
            className="w-full gap-1.5"
          >
            <Plus size={13} />
            Add Another Hotel Option ({items.length}/5)
          </Button>
        )}
      </div>

      <div className="mt-5 flex items-center justify-end gap-2 border-t border-border/60 pt-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={!isFormValid || submitMutation.isPending}
          loading={submitMutation.isPending}
          onClick={() => submitMutation.mutate()}
        >
          Submit {items.length} Proposals
        </Button>
      </div>
    </Modal>
  );
}
