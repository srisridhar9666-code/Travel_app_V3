import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Building2,
  Copy,
  ExternalLink,
  KeyRound,
  MapPin,
  Pencil,
  Plus,
  Power,
  ShieldCheck,
  Store,
  Trash2,
  UserPlus,
} from 'lucide-react';
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
  tabButtonClass,
  ZEBRA_ROWS,
} from '@/components/ui';
import { useVendors } from '@/components/VendorSelect';
import {
  createSavedHotel,
  createVendor,
  createVendorLogin,
  deleteSavedHotel,
  errorMessage,
  fetchSavedHotels,
  fetchTelemetrySettings,
  setVendorActive,
  updateSavedHotel,
  updateTelemetrySettings,
  updateVendor,
} from '@/lib/api';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import {
  VENDOR_KINDS,
  VENDOR_KIND_LABELS,
  isInvoiceEditor,
  type SavedHotel,
  type SavedHotelPayload,
  type Vendor,
  type VendorKind,
  type VendorPayload,
} from '@/types';

interface Draft {
  name: string;
  kind: VendorKind;
  contact_name: string;
  phone: string;
  email: string;
  gstin: string;
  notes: string;
  create_login: boolean;
  login_email: string;
  login_password: string;
}

const EMPTY: Draft = {
  name: '',
  kind: 'TRAVEL_AGENT',
  contact_name: '',
  phone: '',
  email: '',
  gstin: '',
  notes: '',
  create_login: false,
  login_email: '',
  login_password: 'Vendor@123',
};

const draftFrom = (vendor: Vendor): Draft => ({
  name: vendor.name,
  kind: vendor.kind,
  contact_name: vendor.contact_name ?? '',
  phone: vendor.phone ?? '',
  email: vendor.email ?? '',
  gstin: vendor.gstin ?? '',
  notes: vendor.notes ?? '',
  create_login: false,
  login_email: vendor.email ?? '',
  login_password: 'Vendor@123',
});

const payloadFrom = (draft: Draft): VendorPayload => ({
  name: draft.name.trim(),
  kind: draft.kind,
  contact_name: draft.contact_name.trim() || null,
  phone: draft.phone.trim() || null,
  email: draft.email.trim() || null,
  gstin: draft.gstin.replace(/\s+/g, '').toUpperCase() || null,
  notes: draft.notes.trim() || null,
  create_login: draft.create_login,
  login_email: draft.create_login ? (draft.login_email.trim() || draft.email.trim() || null) : null,
  login_password: draft.create_login ? (draft.login_password.trim() || 'Vendor@123') : null,
});

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

  const [activeTab, setActiveTab] = useState<'vendors' | 'hotels' | 'policy'>('vendors');
  const [editing, setEditing] = useState<Vendor | 'new' | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [switching, setSwitching] = useState<Vendor | null>(null);
  const [showOff, setShowOff] = useState(false);

  // Vendor Portal Login Modal
  const [loginVendor, setLoginVendor] = useState<Vendor | null>(null);
  const [loginEmail, setLoginEmail] = useState('');
  const [loginPassword, setLoginPassword] = useState('Vendor@123');
  const [loginFullName, setLoginFullName] = useState('');

  // Saved Hotels state
  const [hotelSearch, setHotelSearch] = useState('');
  const [editingHotel, setEditingHotel] = useState<SavedHotel | 'new' | null>(null);
  const [hotelDraft, setHotelDraft] = useState<SavedHotelPayload>({
    hotel_name: '',
    city: '',
    state: '',
    address: '',
    landmark: '',
    google_maps_url: '',
    room_category: 'Standard',
    nightly_rate: 2000,
    contact_phone: '',
    amenities: ['Free Wi-Fi', 'Breakfast Included'],
    vendor_notes: '',
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['vendors'] });
    queryClient.invalidateQueries({ queryKey: ['saved-hotels'] });
  };

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

  // Portal Login Mutation
  const saveLogin = useMutation({
    mutationFn: () => {
      if (!loginVendor) throw new Error('No vendor selected');
      return createVendorLogin(loginVendor.id, {
        email: loginEmail.trim(),
        password: loginPassword.trim(),
        full_name: loginFullName.trim() || loginVendor.contact_name || loginVendor.name,
      });
    },
    onSuccess: (res) => {
      toast.success(res.message);
      setLoginVendor(null);
      refresh();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to update portal login');
    },
  });

  // Saved Hotels Query
  const { data: savedHotels = [], isLoading: hotelsLoading } = useQuery({
    queryKey: ['saved-hotels', hotelSearch],
    queryFn: () => fetchSavedHotels({ query: hotelSearch || undefined }),
  });

  // Saved Hotels Mutations
  const saveHotelMutation = useMutation({
    mutationFn: () =>
      editingHotel === 'new' || editingHotel === null
        ? createSavedHotel(hotelDraft)
        : updateSavedHotel(editingHotel.id, hotelDraft),
    onSuccess: () => {
      toast.success(editingHotel === 'new' ? 'Hotel added to directory' : 'Hotel updated');
      setEditingHotel(null);
      refresh();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to save hotel');
    },
  });

  const deleteHotelMutation = useMutation({
    mutationFn: (id: number) => deleteSavedHotel(id),
    onSuccess: () => {
      toast.success('Hotel removed from directory');
      refresh();
    },
  });

  // Telemetry Settings Query & Mutation
  const { data: telemetrySettings, isLoading: settingsLoading } = useQuery({
    queryKey: ['telemetry-settings'],
    queryFn: fetchTelemetrySettings,
  });

  const [policyCheckoutReq, setPolicyCheckoutReq] = useState<boolean | null>(null);
  const [policyMaxMissed, setPolicyMaxMissed] = useState<number | null>(null);
  const [policyAiAutofill, setPolicyAiAutofill] = useState<boolean | null>(null);

  const checkoutRequired = policyCheckoutReq !== null ? policyCheckoutReq : (telemetrySettings?.require_telemetry_checkout ?? true);
  const maxMissedPhotos = policyMaxMissed !== null ? policyMaxMissed : (telemetrySettings?.max_monthly_missed_photos ?? 5);
  const aiAutofillEnabled = policyAiAutofill !== null ? policyAiAutofill : (telemetrySettings?.ai_ticket_autofill_enabled ?? true);

  const saveSettingsMutation = useMutation({
    mutationFn: () =>
      updateTelemetrySettings({
        require_telemetry_checkout: checkoutRequired,
        max_monthly_missed_photos: maxMissedPhotos,
        ai_ticket_autofill_enabled: aiAutofillEnabled,
      }),
    onSuccess: () => {
      toast.success('Governance & AI settings updated');
      queryClient.invalidateQueries({ queryKey: ['telemetry-settings'] });
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to update settings');
    },
  });

  const open = (vendor: Vendor | 'new') => {
    setDraft(vendor === 'new' ? EMPTY : draftFrom(vendor));
    setEditing(vendor);
  };

  const openLoginModal = (vendor: Vendor) => {
    setLoginVendor(vendor);
    setLoginEmail(vendor.portal_email || vendor.email || '');
    setLoginPassword('Vendor@123');
    setLoginFullName(vendor.contact_name || vendor.name);
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
        title="Vendors & Stays Directory"
        description="Manage contracted travel operators, vendor portal logins, saved hotel catalog, and active trip regularization rules."
        actions={
          canEdit && (
            <div className="flex items-center gap-2">
              {activeTab === 'hotels' ? (
                <Button
                  onClick={() => {
                    setHotelDraft({
                      hotel_name: '',
                      city: '',
                      state: '',
                      address: '',
                      landmark: '',
                      google_maps_url: '',
                      room_category: 'Standard',
                      nightly_rate: 2000,
                      contact_phone: '',
                      amenities: ['Free Wi-Fi', 'Breakfast Included'],
                      vendor_notes: '',
                    });
                    setEditingHotel('new');
                  }}
                >
                  <Plus size={15} />
                  Add Hotel
                </Button>
              ) : (
                <Button onClick={() => open('new')}>
                  <Plus size={15} />
                  Add Vendor
                </Button>
              )}
            </div>
          )
        }
      />

      {/* Tabs navigation */}
      <div className="flex flex-wrap gap-1 border-b border-border text-sm font-medium">
        <button
          type="button"
          onClick={() => setActiveTab('vendors')}
          className={tabButtonClass(activeTab === 'vendors', 'underline')}
        >
          <Store size={15} />
          Vendors & Portal Logins ({all.length})
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('hotels')}
          className={tabButtonClass(activeTab === 'hotels', 'underline')}
        >
          <Building2 size={15} />
          Hotel Directory ({savedHotels.length})
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('policy')}
          className={tabButtonClass(activeTab === 'policy', 'underline')}
        >
          <ShieldCheck size={15} />
          Settings &amp; Governance
        </button>
      </div>

      {/* TAB 1: Vendors & Portal Logins */}
      {activeTab === 'vendors' && (
        <Card>
          <CardHeader
            title={`${rows.length} ${rows.length === 1 ? 'vendor' : 'vendors'}`}
            description="Manage vendor accounts, commercial details, and portal access credentials."
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
            <div className="overflow-x-auto rounded-b-xl">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-border text-2xs uppercase tracking-wide text-text-subtle">
                  <tr>
                    <th className="px-4 py-2.5 font-medium sm:px-5">Vendor</th>
                    <th className="hidden px-4 py-2.5 font-medium md:table-cell">Contact & Portal Login</th>
                    <th className="hidden px-4 py-2.5 font-medium lg:table-cell">Used on</th>
                    <th className="hidden px-4 py-2.5 font-medium sm:table-cell">Status</th>
                    {canEdit && <th className="px-4 py-2.5 sm:px-5 text-right font-medium">Actions</th>}
                  </tr>
                </thead>
                <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                  {rows.map((vendor) => (
                    <tr key={vendor.id} className={cn(!vendor.is_active && 'text-text-muted')}>
                      <td className="px-4 py-3 align-top sm:px-5">
                        <p className="font-medium text-text">{vendor.name}</p>
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
                        {vendor.contact_name && <p className="font-medium text-text">{vendor.contact_name}</p>}
                        {vendor.phone && <p className="text-text-muted">{vendor.phone}</p>}
                        {vendor.email && <p className="text-text-muted">{vendor.email}</p>}
                        {/* Portal login status */}
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                          {vendor.portal_email ? (
                            <>
                              <span className="inline-flex items-center gap-1 text-2xs text-text font-mono bg-surface-raised px-2 py-0.5 rounded border border-border">
                                <KeyRound size={11} className="text-primary" />
                                {vendor.portal_email}
                              </span>
                              {canEdit && (
                                <button
                                  type="button"
                                  onClick={() => openLoginModal(vendor)}
                                  className="text-2xs text-primary hover:underline font-medium ml-1"
                                >
                                  Reset login
                                </button>
                              )}
                            </>
                          ) : (
                            canEdit && (
                              <button
                                type="button"
                                onClick={() => openLoginModal(vendor)}
                                className="inline-flex items-center gap-1 text-2xs text-primary hover:underline font-medium"
                              >
                                <UserPlus size={11} />
                                Create portal login
                              </button>
                            )
                          )}
                        </div>
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
      )}

      {/* TAB 2: Hotel Directory */}
      {activeTab === 'hotels' && (
        <Card>
          <CardHeader
            title="Saved Hotel Directory"
            description="Catalog of verified hotels that vendors and operations can pick to instantly auto-fill proposal forms."
            action={
              <div className="flex items-center gap-2">
                <Input
                  value={hotelSearch}
                  onChange={(e) => setHotelSearch(e.target.value)}
                  placeholder="Filter by hotel, city..."
                  className="w-48 text-xs"
                />
              </div>
            }
          />
          {hotelsLoading ? (
            <div className="space-y-2 p-5">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : savedHotels.length === 0 ? (
            <EmptyState
              icon={<Building2 size={28} />}
              title="No saved hotels found"
              description="Add frequently used hotels to the directory for quick auto-fill when raising options."
              action={
                canEdit && (
                  <Button
                    onClick={() => {
                      setHotelDraft({
                        hotel_name: '',
                        city: '',
                        state: '',
                        address: '',
                        landmark: '',
                        google_maps_url: '',
                        room_category: 'Standard',
                        nightly_rate: 2000,
                        contact_phone: '',
                        amenities: ['Free Wi-Fi', 'Breakfast Included'],
                        vendor_notes: '',
                      });
                      setEditingHotel('new');
                    }}
                  >
                    <Plus size={15} />
                    Add Hotel
                  </Button>
                )
              }
            />
          ) : (
            <div className="overflow-x-auto rounded-b-xl">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-border text-2xs uppercase tracking-wide text-text-subtle">
                  <tr>
                    <th className="px-4 py-2.5 font-medium sm:px-5">Hotel & Location</th>
                    <th className="hidden px-4 py-2.5 font-medium md:table-cell">Category & Rate</th>
                    <th className="hidden px-4 py-2.5 font-medium lg:table-cell">Amenities & Notes</th>
                    <th className="hidden px-4 py-2.5 font-medium sm:table-cell">Vendor / Source</th>
                    {canEdit && <th className="px-4 py-2.5 sm:px-5 text-right font-medium">Actions</th>}
                  </tr>
                </thead>
                <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                  {savedHotels.map((hotel) => (
                    <tr key={hotel.id}>
                      <td className="px-4 py-3 align-top sm:px-5">
                        <div className="flex items-center gap-1.5">
                          <p className="font-medium text-text">{hotel.hotel_name}</p>
                          {hotel.google_maps_url && (
                            <a
                              href={hotel.google_maps_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-text-subtle hover:text-primary"
                              title="View on Google Maps"
                            >
                              <ExternalLink size={12} />
                            </a>
                          )}
                        </div>
                        <p className="text-xs text-text-muted flex items-center gap-1 mt-0.5">
                          <MapPin size={11} className="text-text-subtle" />
                          {hotel.city}{hotel.state ? `, ${hotel.state}` : ''}
                        </p>
                        <p className="text-2xs text-text-subtle mt-0.5">{hotel.address}</p>
                        {hotel.landmark && (
                          <p className="text-2xs text-text-subtle">Near: {hotel.landmark}</p>
                        )}
                      </td>
                      <td className="hidden px-4 py-3 align-top text-xs md:table-cell">
                        <p className="font-semibold text-text">₹{Number(hotel.nightly_rate).toLocaleString('en-IN')}<span className="text-2xs font-normal text-text-subtle">/night</span></p>
                        <Badge tone="neutral" className="mt-1 text-2xs">
                          {hotel.room_category}
                        </Badge>
                      </td>
                      <td className="hidden px-4 py-3 align-top text-xs text-text-muted lg:table-cell">
                        {hotel.amenities && hotel.amenities.length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {hotel.amenities.map((a, i) => (
                              <span key={i} className="text-2xs bg-surface-raised px-1.5 py-0.5 rounded border border-border">
                                {a}
                              </span>
                            ))}
                          </div>
                        )}
                        {hotel.vendor_notes && (
                          <p className="mt-1 text-2xs text-text-subtle italic">{hotel.vendor_notes}</p>
                        )}
                      </td>
                      <td className="hidden px-4 py-3 align-top text-xs sm:table-cell">
                        <Badge tone="info">
                          {hotel.vendor_name || 'Organization Directory'}
                        </Badge>
                      </td>
                      {canEdit && (
                        <td className="px-4 py-3 align-top sm:px-5">
                          <div className="flex items-center justify-end gap-1.5">
                            <Button
                              size="sm"
                              variant="ghost"
                              className="text-text-subtle hover:text-danger"
                              onClick={() => deleteHotelMutation.mutate(hotel.id)}
                            >
                              <Trash2 size={13} />
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
      )}

      {/* TAB 3: Trip Lock & Regularization Settings */}
      {activeTab === 'policy' && (
        <Card className="max-w-2xl">
          <CardHeader
            title="Active Booking Lock & Missed Photos Allowance"
            description="Configure whether employees must complete trip checkout before booking again, and their monthly grace allowance."
          />
          <div className="p-5 space-y-5">
            {settingsLoading ? (
              <Skeleton className="h-24 w-full" />
            ) : (
              <>
                <div className="rounded-xl border border-border bg-surface-raised/40 p-4 space-y-3">
                  <label className="flex items-start gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={checkoutRequired}
                      onChange={(e) => setPolicyCheckoutReq(e.target.checked)}
                      className="mt-1 h-4 w-4 rounded accent-[rgb(var(--primary))]"
                    />
                    <div>
                      <p className="text-sm font-semibold text-text">
                        Enforce Trip Checkout Before Next Booking
                      </p>
                      <p className="text-xs text-text-muted mt-0.5">
                        When enabled, an employee cannot book another cab or hotel if they have uncompleted trips without checkout photos, unless within their monthly regularization allowance or unlocked by their Manager/Admin.
                      </p>
                    </div>
                  </label>
                </div>

                <div className="rounded-xl border border-border bg-surface-raised/40 p-4 space-y-2">
                  <Field
                    label="Monthly Missed Photos Regularization Allowance"
                    htmlFor="max-missed"
                    hint="Number of missed/unclosed trips allowed per employee per month before their booking is locked. If exceeded, a Manager or Admin must unlock them."
                  >
                    <Input
                      id="max-missed"
                      type="number"
                      min="0"
                      max="50"
                      value={maxMissedPhotos}
                      onChange={(e) => setPolicyMaxMissed(parseInt(e.target.value) || 0)}
                      className="max-w-xs"
                    />
                  </Field>
                </div>

                <div className="rounded-xl border border-border bg-surface-raised/40 p-4 space-y-3">
                  <label className="flex items-start gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={aiAutofillEnabled}
                      onChange={(e) => setPolicyAiAutofill(e.target.checked)}
                      className="mt-1 h-4 w-4 rounded accent-[rgb(var(--primary))]"
                    />
                    <div>
                      <p className="text-sm font-semibold text-text">
                        AI Ticket Autofilling on Upload
                      </p>
                      <p className="text-xs text-text-muted mt-0.5">
                        When enabled, uploading booked ticket PDFs or images runs AI extraction to automatically prefill PNR, carrier, fare, and departure/arrival times. Turn off to attach files directly and enter booking details manually.
                      </p>
                    </div>
                  </label>
                </div>

                <div className="flex justify-end pt-2">
                  <Button
                    variant="primary"
                    loading={saveSettingsMutation.isPending}
                    onClick={() => saveSettingsMutation.mutate()}
                  >
                    Save Policy Settings
                  </Button>
                </div>
              </>
            )}
          </div>
        </Card>
      )}

      {/* Add / Edit Vendor Modal */}
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

          {/* Option to create portal login account directly */}
          {editing === 'new' && (
            <div className="rounded-xl border border-border/80 bg-surface-raised/50 p-3 space-y-2 mt-2">
              <label className="flex items-center gap-2 text-xs font-medium text-text cursor-pointer">
                <input
                  type="checkbox"
                  checked={draft.create_login}
                  onChange={(e) => setDraft({ ...draft, create_login: e.target.checked })}
                  className="h-4 w-4 rounded accent-[rgb(var(--primary))]"
                />
                <span>Create portal login credentials for this vendor</span>
              </label>
              {draft.create_login && (
                <div className="grid gap-3 pt-2 sm:grid-cols-2">
                  <Field label="Portal Login Email" htmlFor="new-login-email" required>
                    <Input
                      id="new-login-email"
                      type="email"
                      value={draft.login_email || draft.email}
                      onChange={(e) => setDraft({ ...draft, login_email: e.target.value })}
                      placeholder="vendor@company.com"
                    />
                  </Field>
                  <Field label="Initial Password" htmlFor="new-login-password" required>
                    <Input
                      id="new-login-password"
                      value={draft.login_password}
                      onChange={(e) => setDraft({ ...draft, login_password: e.target.value })}
                      placeholder="Vendor@123"
                    />
                  </Field>
                </div>
              )}
            </div>
          )}
        </form>
      </Modal>

      {/* Vendor Portal Login Management Modal */}
      <Modal
        open={loginVendor !== null}
        onClose={() => setLoginVendor(null)}
        title={loginVendor ? `Portal Login: ${loginVendor.name}` : ''}
        description="Vendor will sign in to their dedicated portal using these credentials."
        footer={
          <>
            <Button variant="secondary" onClick={() => setLoginVendor(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={saveLogin.isPending}
              disabled={!loginEmail.trim() || !loginPassword.trim()}
              onClick={() => saveLogin.mutate()}
            >
              Save Credentials
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Portal Login Email" htmlFor="modal-login-email" required>
            <Input
              id="modal-login-email"
              type="email"
              value={loginEmail}
              onChange={(e) => setLoginEmail(e.target.value)}
              placeholder="vendor@company.com"
            />
          </Field>
          <Field label="Portal Contact Name" htmlFor="modal-login-name">
            <Input
              id="modal-login-name"
              value={loginFullName}
              onChange={(e) => setLoginFullName(e.target.value)}
              placeholder="e.g. Rajesh Sharma"
            />
          </Field>
          <Field
            label="Password"
            htmlFor="modal-login-password"
            required
            hint="Set or reset a simple password (e.g. Vendor@123) for the vendor."
          >
            <div className="flex gap-2">
              <Input
                id="modal-login-password"
                value={loginPassword}
                onChange={(e) => setLoginPassword(e.target.value)}
                placeholder="Vendor@123"
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => {
                  navigator.clipboard.writeText(loginPassword);
                  toast.success('Password copied');
                }}
              >
                <Copy size={13} />
              </Button>
            </div>
          </Field>
        </div>
      </Modal>

      {/* Add Hotel to Directory Modal */}
      <Modal
        open={editingHotel !== null}
        onClose={() => setEditingHotel(null)}
        title="Add Hotel to Directory"
        description="Saved hotels appear in the auto-fill selector when creating hotel stay proposals."
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditingHotel(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={saveHotelMutation.isPending}
              disabled={!hotelDraft.hotel_name.trim() || !hotelDraft.city.trim() || !hotelDraft.address.trim()}
              onClick={() => saveHotelMutation.mutate()}
            >
              Save to Directory
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Hotel Name" htmlFor="h-name" required>
              <Input
                id="h-name"
                value={hotelDraft.hotel_name}
                onChange={(e) => setHotelDraft({ ...hotelDraft, hotel_name: e.target.value })}
                placeholder="e.g. Lemon Tree Premier"
              />
            </Field>
            <Field label="City" htmlFor="h-city" required>
              <Input
                id="h-city"
                value={hotelDraft.city}
                onChange={(e) => setHotelDraft({ ...hotelDraft, city: e.target.value })}
                placeholder="e.g. Hyderabad"
              />
            </Field>
            <Field label="Room Category" htmlFor="h-cat">
              <Input
                id="h-cat"
                value={hotelDraft.room_category}
                onChange={(e) => setHotelDraft({ ...hotelDraft, room_category: e.target.value })}
                placeholder="Standard, Deluxe"
              />
            </Field>
            <Field label="Typical Nightly Rate (₹)" htmlFor="h-rate">
              <Input
                id="h-rate"
                type="number"
                min="0"
                value={hotelDraft.nightly_rate || ''}
                onChange={(e) => setHotelDraft({ ...hotelDraft, nightly_rate: parseFloat(e.target.value) || 0 })}
                placeholder="2500"
              />
            </Field>
            <Field label="Landmark / Area" htmlFor="h-land" className="sm:col-span-2">
              <Input
                id="h-land"
                value={hotelDraft.landmark || ''}
                onChange={(e) => setHotelDraft({ ...hotelDraft, landmark: e.target.value })}
                placeholder="Near Hitec City Metro"
              />
            </Field>
            <Field label="Full Street Address" htmlFor="h-addr" required className="sm:col-span-2">
              <Input
                id="h-addr"
                value={hotelDraft.address}
                onChange={(e) => setHotelDraft({ ...hotelDraft, address: e.target.value })}
                placeholder="Plot No 2, Survey No 64, Hitec City, Hyderabad"
              />
            </Field>
            <Field label="Google Maps Link" htmlFor="h-map" className="sm:col-span-2">
              <Input
                id="h-map"
                value={hotelDraft.google_maps_url || ''}
                onChange={(e) => setHotelDraft({ ...hotelDraft, google_maps_url: e.target.value })}
                placeholder="https://maps.google.com/..."
              />
            </Field>
            <Field label="Contact Phone" htmlFor="h-phone">
              <Input
                id="h-phone"
                value={hotelDraft.contact_phone || ''}
                onChange={(e) => setHotelDraft({ ...hotelDraft, contact_phone: e.target.value })}
                placeholder="+91 40 4423 0000"
              />
            </Field>
            <Field label="Vendor / Operator" htmlFor="h-vendor">
              <Select
                id="h-vendor"
                value={hotelDraft.vendor_id ?? ''}
                onChange={(e) => setHotelDraft({ ...hotelDraft, vendor_id: e.target.value ? Number(e.target.value) : null })}
              >
                <option value="">Organization General</option>
                {all.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Notes / Inclusions" htmlFor="h-notes" className="sm:col-span-2">
              <Input
                id="h-notes"
                value={hotelDraft.vendor_notes || ''}
                onChange={(e) => setHotelDraft({ ...hotelDraft, vendor_notes: e.target.value })}
                placeholder="e.g. Free Wi-Fi, 24-hr room service, complimentary breakfast"
              />
            </Field>
          </div>
        </div>
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
