import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Building2,
  Camera,
  CheckCircle2,
  KeyRound,
  LogOut,
  MapPin,
  ShieldCheck,
  Upload,
} from 'lucide-react';
import toast from 'react-hot-toast';

import { CameraCaptureModal } from '@/components/CameraCaptureModal';
import { TelemetryFilePreview } from '@/components/TelemetryFilePreview';
import { Modal } from '@/components/Modal';
import { Button, Input, Field } from '@/components/ui';
import {
  fetchHotelVerifications,
  forceCloseHotelStay,
  hotelCheckIn,
  hotelCheckOut,
  uploadTelemetryFile,
  verifyHotelStay,
} from '@/lib/api';
import { dayTime } from '@/lib/requests';
import { formatInstant } from '@/lib/time';
import { useAuth } from '@/store/auth';
import type { HotelStayVerification, RequestStatus } from '@/types';

function getCurrentGps(): Promise<{ lat: number; lng: number } | null> {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      () => resolve(null),
      { timeout: 5000 }
    );
  });
}

interface HotelStayVerificationCardProps {
  requestId: number;
  requestStatus: RequestStatus;
  hotelCity?: string | null;
  /** Reviewer mode (Manager / Admin queue): hides traveller check-in/out upload actions, keeps Force Close & Verify. */
  readOnly?: boolean;
  onUpdated?: () => void;
}

export function HotelStayVerificationCard({
  requestId,
  requestStatus,
  hotelCity,
  readOnly = false,
  onUpdated,
}: HotelStayVerificationCardProps) {
  const qc = useQueryClient();
  const me = useAuth((s) => s.user);

  const [checkInOpen, setCheckInOpen] = useState(false);
  const [checkOutVerification, setCheckOutVerification] = useState<HotelStayVerification | null>(null);
  const [verifyRecord, setVerifyRecord] = useState<HotelStayVerification | null>(null);
  const [forceCloseStayRecord, setForceCloseStayRecord] = useState<HotelStayVerification | null>(null);

  const { data: verifications = [], isLoading } = useQuery<HotelStayVerification[]>({
    queryKey: ['hotel-verification', requestId],
    queryFn: () => fetchHotelVerifications(requestId),
    refetchInterval: 8000,
    refetchOnWindowFocus: true,
  });

  const isAdminOrFinance =
    me?.role === 'ADMIN' ||
    me?.role === 'SUPER_ADMIN' ||
    me?.role === 'SYSTEM_ADMIN' ||
    me?.role === 'FINANCE';

  const isManagerOrAdmin =
    me?.role === 'ADMIN' ||
    me?.role === 'SUPER_ADMIN' ||
    me?.role === 'SYSTEM_ADMIN' ||
    me?.role === 'MANAGER';

  const canActionStay = ['APPROVED', 'BOOKED', 'READY_FOR_VENDOR'].includes(requestStatus);

  if (isLoading) {
    return (
      <div className="mt-3 rounded-xl border border-border/60 bg-surface/40 p-4 text-xs text-text-muted animate-pulse">
        Loading hotel stay verification records...
      </div>
    );
  }

  // Active check-in that needs check-out and folio bill
  const activeStay = verifications.find((v) => v.status === 'CHECKED_IN');

  return (
    <div className="mt-3 rounded-xl border border-border/80 bg-surface/70 backdrop-blur-xs p-4 shadow-2xs">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/50 pb-3">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Building2 size={16} />
          </div>
          <div>
            <h4 className="text-sm font-semibold text-text flex flex-wrap items-center gap-2">
              Hotel Stay Verification &amp; Folio Capture
              {activeStay ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-sky-500/15 px-2 py-0.5 text-2xs font-semibold text-sky-700 dark:text-sky-300">
                  <span className="h-1.5 w-1.5 rounded-full bg-sky-500 animate-pulse" />
                  Checked In
                </span>
              ) : verifications.length > 0 ? (
                <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-2xs font-semibold text-emerald-700 dark:text-emerald-300">
                  Checked Out ({verifications.length} {verifications.length === 1 ? 'record' : 'records'})
                </span>
              ) : (
                <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-2xs font-semibold text-amber-800 dark:text-amber-300">
                  Not Checked In
                </span>
              )}
            </h4>
            <p className="text-2xs text-text-subtle">
              On-site room check-in{hotelCity ? ` in ${hotelCity}` : ''}, GPS timestamp, and checkout hotel folio invoice reconciliation.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {!readOnly && canActionStay && !activeStay && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setCheckInOpen(true)}
              className="gap-1.5 text-xs"
            >
              <KeyRound size={13} />
              Check In to Room
            </Button>
          )}

          {!readOnly && canActionStay && activeStay && (
            <Button
              size="sm"
              variant="primary"
              onClick={() => setCheckOutVerification(activeStay)}
              className="gap-1.5 text-xs"
            >
              <LogOut size={13} />
              Check Out &amp; Upload Folio Bill
            </Button>
          )}

          {canActionStay && activeStay && isManagerOrAdmin && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setForceCloseStayRecord(activeStay)}
              className="gap-1.5 text-xs text-danger hover:bg-danger/10 border-danger/40"
              title="Force-close hotel stay if employee forgot to checkout or upload folio"
            >
              <AlertTriangle size={13} />
              Force Close
            </Button>
          )}
        </div>
      </div>

      {verifications.length === 0 ? (
        <div className="py-4 text-center text-xs text-text-muted">
          {readOnly
            ? 'No hotel check-in verification uploaded by the traveller yet.'
            : 'No hotel stay verification recorded yet. Click "Check In to Room" on hotel arrival.'}
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          {verifications.map((v) => (
            <div
              key={v.id}
              className={`rounded-lg border p-3 text-xs transition-colors ${
                v.status === 'CHECKED_IN'
                  ? 'border-info/70 bg-info-soft/40'
                  : 'border-border/70 bg-surface'
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/40 pb-2">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-text">Room #{v.room_number || 'Standard'}</span>
                  {v.user_name && (
                    <span className="text-text-subtle">({v.user_name})</span>
                  )}
                  {v.status === 'CHECKED_IN' && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-info/10 px-2 py-0.5 text-2xs font-medium text-info">
                      <KeyRound size={10} /> Checked In
                    </span>
                  )}
                  {v.status === 'CHECKED_OUT' && !v.is_verified && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-2xs font-medium text-amber-800 dark:text-amber-300">
                      Checked Out · Awaiting Audit
                    </span>
                  )}
                  {v.is_verified && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-2xs font-medium text-emerald-800 dark:text-emerald-300">
                      <ShieldCheck size={11} /> Verified by {v.verified_by_name || 'Finance'}
                    </span>
                  )}
                </div>

                {isAdminOrFinance && v.status === 'CHECKED_OUT' && !v.is_verified && (
                  <Button
                    size="sm"
                    variant="secondary"
                    className="h-7 text-2xs"
                    onClick={() => setVerifyRecord(v)}
                  >
                    Verify Folio Bill
                  </Button>
                )}
              </div>

              {/* Details grid */}
              <div className="mt-2.5 grid gap-3 sm:grid-cols-3">
                {/* Check In */}
                <div className="rounded-md border border-border/50 bg-surface-raised p-2">
                  <span className="block text-2xs font-medium text-text-subtle">Arrival Check-In</span>
                  <p className="mt-0.5 text-xs font-semibold text-text">
                    Room {v.room_number}
                  </p>
                  <p className="text-2xs text-text-muted">
                    {v.check_in_time ? formatInstant(v.check_in_time) : '—'}
                  </p>
                  {v.check_in_lat != null && v.check_in_lng != null && (
                    <div className="mt-1 space-y-0.5">
                      {v.check_in_location_name && (
                        <p className="flex items-start gap-1 text-2xs font-medium text-text">
                          <MapPin size={10} className="mt-0.5 shrink-0 text-primary" />
                          <span>{v.check_in_location_name}</span>
                        </p>
                      )}
                      <a
                        href={`https://www.google.com/maps?q=${v.check_in_lat},${v.check_in_lng}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-2xs text-text-subtle hover:text-primary hover:underline"
                      >
                        {!v.check_in_location_name && <MapPin size={10} />}
                        {v.check_in_lat.toFixed(4)}, {v.check_in_lng.toFixed(4)}
                      </a>
                    </div>
                  )}
                  {v.check_in_photo_url && (
                    <div className="mt-1.5">
                      <TelemetryFilePreview path={v.check_in_photo_url} compact name="Check-In Photo" />
                    </div>
                  )}
                  {v.check_in_remarks && (
                    <p className="mt-1 text-2xs text-text-muted italic">
                      Remarks: &quot;{v.check_in_remarks}&quot;
                    </p>
                  )}
                </div>

                {/* Check Out */}
                <div className="rounded-md border border-border/50 bg-surface-raised p-2">
                  <span className="block text-2xs font-medium text-text-subtle">Departure Check-Out</span>
                  <p className="mt-0.5 text-xs font-semibold text-text">
                    {v.check_out_time ? formatInstant(v.check_out_time) : 'In progress'}
                  </p>
                  {v.check_out_lat != null && v.check_out_lng != null && (
                    <div className="mt-1 space-y-0.5">
                      {v.check_out_location_name && (
                        <p className="flex items-start gap-1 text-2xs font-medium text-text">
                          <MapPin size={10} className="mt-0.5 shrink-0 text-primary" />
                          <span>{v.check_out_location_name}</span>
                        </p>
                      )}
                      <a
                        href={`https://www.google.com/maps?q=${v.check_out_lat},${v.check_out_lng}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-2xs text-text-subtle hover:text-primary hover:underline"
                      >
                        {!v.check_out_location_name && <MapPin size={10} />}
                        {v.check_out_lat.toFixed(4)}, {v.check_out_lng.toFixed(4)}
                      </a>
                    </div>
                  )}
                  {v.check_out_remarks && (
                    <p className="mt-1 text-2xs text-text-muted italic">
                      Remarks: &quot;{v.check_out_remarks}&quot;
                    </p>
                  )}
                </div>

                {/* Hotel Folio Invoice */}
                <div className="rounded-md border border-border/50 bg-surface-raised p-2">
                  <span className="block text-2xs font-medium text-text-subtle">Folio Tax Invoice</span>
                  {v.folio_bill_url ? (
                    <div className="mt-1">
                      <TelemetryFilePreview path={v.folio_bill_url} compact name="Folio Bill" />
                    </div>
                  ) : (
                    <p className="mt-0.5 text-2xs italic text-text-subtle">
                      Pending check-out invoice bill upload
                    </p>
                  )}
                  {v.verification_notes && (
                    <p className="mt-1 text-2xs italic text-text-subtle">
                      Audit note: &quot;{v.verification_notes}&quot;
                    </p>
                  )}
                </div>
              </div>

              {v.force_closed_at && (
                <div className="mt-2 rounded bg-amber-500/10 p-2 text-2xs text-amber-800 dark:text-amber-300 border border-amber-500/30">
                  <strong>Force-closed by {v.force_closed_by_name || 'Manager'}:</strong> &quot;{v.force_close_reason}&quot; on {dayTime(v.force_closed_at)}
                </div>
              )}

              {/* Reimbursement Prompt Alert */}
              {v.reimbursement_prompted && (
                <div className="mt-2.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 p-2.5 text-2xs text-emerald-800 dark:text-emerald-300 flex items-center justify-between">
                  <span className="flex items-center gap-1.5 font-medium">
                    <CheckCircle2 size={12} />
                    Hotel stay completed &amp; folio captured! You can now submit your post-travel expense reimbursement claim.
                  </span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Check In Modal */}
      {checkInOpen && (
        <HotelCheckInModal
          requestId={requestId}
          isOpen={checkInOpen}
          onClose={() => setCheckInOpen(false)}
          onSuccess={() => {
            setCheckInOpen(false);
            qc.invalidateQueries({ queryKey: ['hotel-verification', requestId] });
            onUpdated?.();
          }}
        />
      )}

      {/* Check Out Modal */}
      {checkOutVerification && (
        <HotelCheckOutModal
          requestId={requestId}
          verification={checkOutVerification}
          isOpen={Boolean(checkOutVerification)}
          onClose={() => setCheckOutVerification(null)}
          onSuccess={() => {
            setCheckOutVerification(null);
            qc.invalidateQueries({ queryKey: ['hotel-verification', requestId] });
            onUpdated?.();
          }}
        />
      )}

      {/* Force Close Modal */}
      {forceCloseStayRecord && (
        <ForceCloseHotelStayModal
          requestId={requestId}
          verification={forceCloseStayRecord}
          isOpen={Boolean(forceCloseStayRecord)}
          onClose={() => setForceCloseStayRecord(null)}
          onSuccess={() => {
            setForceCloseStayRecord(null);
            qc.invalidateQueries({ queryKey: ['hotel-verification', requestId] });
            onUpdated?.();
          }}
        />
      )}

      {/* Verify Stay Modal */}
      {verifyRecord && (
        <VerifyHotelStayModal
          requestId={requestId}
          verificationId={verifyRecord.id}
          isOpen={Boolean(verifyRecord)}
          onClose={() => setVerifyRecord(null)}
          onSuccess={() => {
            setVerifyRecord(null);
            qc.invalidateQueries({ queryKey: ['hotel-verification', requestId] });
            onUpdated?.();
          }}
        />
      )}
    </div>
  );
}

function HotelCheckInModal({
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
  const [roomNumber, setRoomNumber] = useState('');
  const [photoUrl, setPhotoUrl] = useState('');
  const [photoSource, setPhotoSource] = useState<'CAMERA' | 'FILE_UPLOAD' | null>(null);
  const [remarks, setRemarks] = useState('');
  const [uploading, setUploading] = useState(false);
  const [cameraModalOpen, setCameraModalOpen] = useState(false);

  const inMutation = useMutation({
    mutationFn: async () => {
      const gps = await getCurrentGps();
      return hotelCheckIn(requestId, {
        room_number: roomNumber.trim(),
        check_in_lat: gps?.lat,
        check_in_lng: gps?.lng,
        check_in_photo_url: photoUrl || null,
        check_in_photo_source: photoSource,
        check_in_remarks: remarks.trim() || null,
      });
    },
    onSuccess: (res) => {
      toast.success(`Checked into room ${res.room_number} with GPS!`);
      onSuccess();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to check in');
    },
  });

  const handlePhotoUpload = async (file: File, source: 'CAMERA' | 'FILE_UPLOAD') => {
    try {
      setUploading(true);
      const res = await uploadTelemetryFile(file);
      setPhotoUrl(res.file_path);
      setPhotoSource(source);
      toast.success(`Check-in photo attached via ${source === 'CAMERA' ? 'Camera' : 'File Upload'}`);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to upload check-in photo');
    } finally {
      setUploading(false);
    }
  };

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title="Hotel Room Check-In"
      description="Record room number assigned upon hotel arrival with verified GPS timestamp and optional photo."
      className="max-w-md"
    >
      <div className="space-y-3">
        <Field label="Assigned Room Number" required htmlFor="room-no">
          <Input
            id="room-no"
            value={roomNumber}
            onChange={(e) => setRoomNumber(e.target.value)}
            placeholder="e.g. 304, Deluxe 2"
          />
        </Field>

        {/* Check-In Photo via Camera or File */}
        <div className="space-y-1.5">
          <label className="text-2xs font-semibold text-text uppercase tracking-wider block">
            Check-In Photo (Camera or Files)
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setCameraModalOpen(true)}
              disabled={uploading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface text-xs font-medium cursor-pointer hover:bg-surface-raised transition-colors text-text"
            >
              <Camera size={14} className="text-primary" />
              <span>Capture via Camera</span>
            </button>

            <label className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface text-xs font-medium cursor-pointer hover:bg-surface-raised transition-colors">
              <Upload size={14} className="text-text-subtle" />
              <span>Upload from Files</span>
              <input
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handlePhotoUpload(f, 'FILE_UPLOAD');
                }}
                disabled={uploading}
              />
            </label>
          </div>
          {photoUrl && (
            <div className="flex items-center gap-1.5 text-2xs text-emerald-600 font-semibold pt-1">
              <span>✓ Photo Attached ({photoSource === 'CAMERA' ? 'Camera' : 'File Upload'})</span>
            </div>
          )}

          {cameraModalOpen && (
            <CameraCaptureModal
              open={cameraModalOpen}
              onClose={() => setCameraModalOpen(false)}
              title="Capture Check-In Photo"
              description="Align your room keycard, door number, or hotel lobby in the viewfinder."
              onCapture={(file) => handlePhotoUpload(file, 'CAMERA')}
            />
          )}
        </div>

        {/* Remarks */}
        <Field label="Remarks" htmlFor="check-in-remarks">
          <Input
            id="check-in-remarks"
            value={remarks}
            onChange={(e) => setRemarks(e.target.value)}
            placeholder="e.g. Early check-in approved, key card received"
          />
        </Field>
      </div>

      <div className="mt-4 flex items-center justify-end gap-2 border-t border-border/50 pt-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={!roomNumber.trim() || inMutation.isPending || uploading}
          loading={inMutation.isPending}
          onClick={() => inMutation.mutate()}
        >
          Confirm Check-In
        </Button>
      </div>
    </Modal>
  );
}

function HotelCheckOutModal({
  requestId,
  verification,
  isOpen,
  onClose,
  onSuccess,
}: {
  requestId: number;
  verification: HotelStayVerification;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [photoUrl, setPhotoUrl] = useState('');
  const [photoSource, setPhotoSource] = useState<'CAMERA' | 'FILE_UPLOAD' | null>(null);
  const [remarks, setRemarks] = useState('');
  const [uploading, setUploading] = useState(false);
  const [cameraModalOpen, setCameraModalOpen] = useState(false);

  const outMutation = useMutation({
    mutationFn: async () => {
      const gps = await getCurrentGps();
      return hotelCheckOut(requestId, verification.id, {
        folio_bill_url: photoUrl || null,
        check_out_photo_source: photoSource,
        check_out_remarks: remarks.trim() || null,
        check_out_lat: gps?.lat,
        check_out_lng: gps?.lng,
      });
    },
    onSuccess: () => {
      toast.success('Check-out recorded and folio bill submitted!');
      onSuccess();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to check out');
    },
  });

  const handlePhotoUpload = async (file: File, source: 'CAMERA' | 'FILE_UPLOAD') => {
    try {
      setUploading(true);
      const res = await uploadTelemetryFile(file);
      setPhotoUrl(res.file_path);
      setPhotoSource(source);
      toast.success(`Hotel folio bill attached via ${source === 'CAMERA' ? 'Camera' : 'File Upload'}`);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to upload folio');
    } finally {
      setUploading(false);
    }
  };

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title="Hotel Check-Out &amp; Folio Capture"
      description={`Checking out of Room ${verification.room_number}. Upload the hotel folio / tax invoice bill.`}
      className="max-w-md"
    >
      <div className="space-y-3">
        {/* Folio Bill Photo / File */}
        <div className="space-y-1.5">
          <label className="text-2xs font-semibold text-text uppercase tracking-wider block">
            Hotel Folio / Final Tax Invoice Bill (Camera or Files) <span className="text-danger">*</span>
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setCameraModalOpen(true)}
              disabled={uploading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface text-xs font-medium cursor-pointer hover:bg-surface-raised transition-colors text-text"
            >
              <Camera size={14} className="text-primary" />
              <span>Capture via Camera</span>
            </button>

            <label className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface text-xs font-medium cursor-pointer hover:bg-surface-raised transition-colors">
              <Upload size={14} className="text-text-subtle" />
              <span>Upload from Files</span>
              <input
                type="file"
                accept="image/*,application/pdf"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handlePhotoUpload(f, 'FILE_UPLOAD');
                }}
                disabled={uploading}
              />
            </label>
          </div>
          {photoUrl && (
            <div className="flex items-center gap-1.5 text-2xs text-emerald-600 font-semibold pt-1">
              <span>✓ Folio Attached ({photoSource === 'CAMERA' ? 'Camera' : 'File Upload'})</span>
            </div>
          )}

          {cameraModalOpen && (
            <CameraCaptureModal
              open={cameraModalOpen}
              onClose={() => setCameraModalOpen(false)}
              title="Capture Hotel Folio / Bill"
              description="Align the hotel bill / tax invoice document in the camera frame and capture."
              onCapture={(file) => handlePhotoUpload(file, 'CAMERA')}
            />
          )}
        </div>

        {/* Remarks */}
        <Field label="Remarks" htmlFor="check-out-remarks">
          <Input
            id="check-out-remarks"
            value={remarks}
            onChange={(e) => setRemarks(e.target.value)}
            placeholder="e.g. Paid extra laundry directly, key cards returned"
          />
        </Field>
      </div>

      <div className="mt-4 flex items-center justify-end gap-2 border-t border-border/50 pt-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={outMutation.isPending || uploading || !photoUrl}
          loading={outMutation.isPending}
          onClick={() => outMutation.mutate()}
        >
          Confirm Check-Out
        </Button>
      </div>
    </Modal>
  );
}

function ForceCloseHotelStayModal({
  requestId,
  verification,
  isOpen,
  onClose,
  onSuccess,
}: {
  requestId: number;
  verification: HotelStayVerification;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [reason, setReason] = useState('');

  const closeMutation = useMutation({
    mutationFn: () => forceCloseHotelStay(requestId, verification.id, reason.trim()),
    onSuccess: () => {
      toast.success('Hotel stay force-closed successfully. Employee is now unblocked.');
      onSuccess();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to force-close hotel stay');
    },
  });

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title="Manager / Admin Force-Close Hotel Stay"
      description="If the employee forgot to check out or submit folio bill, Managers and Admins can force-close this stay so the employee can proceed with future bookings."
      className="max-w-md"
    >
      <div className="space-y-3">
        <div className="rounded-lg bg-amber-500/10 border border-amber-500/30 p-3 text-xs text-amber-800 dark:text-amber-300">
          Stay for Room #{verification.room_number}. Force-closing will mark this stay as checked out and audited without blocking future cab or hotel requests.
        </div>

        <Field label="Reason for Force-Closing Stay" required htmlFor="stay-force-close-reason">
          <Input
            id="stay-force-close-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Employee departed early; hotel front desk confirmed checkout."
          />
        </Field>
      </div>

      <div className="mt-4 flex items-center justify-end gap-2 border-t border-border/50 pt-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="sm"
          className="bg-danger text-white hover:bg-danger/90 border-danger"
          disabled={!reason.trim() || closeMutation.isPending}
          loading={closeMutation.isPending}
          onClick={() => closeMutation.mutate()}
        >
          Force Close Stay
        </Button>
      </div>
    </Modal>
  );
}

function VerifyHotelStayModal({
  requestId,
  verificationId,
  isOpen,
  onClose,
  onSuccess,
}: {
  requestId: number;
  verificationId: number;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [notes, setNotes] = useState('');

  const verifyMutation = useMutation({
    mutationFn: () => verifyHotelStay(requestId, verificationId, { notes: notes || null }),
    onSuccess: () => {
      toast.success('Hotel stay and folio bill verified by Finance');
      onSuccess();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to verify stay');
    },
  });

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title="Audit &amp; Verify Hotel Stay Folio"
      description="Verify hotel tax invoice folio bill against booking and room tariff limit."
      className="max-w-md"
    >
      <div className="space-y-3">
        <Field label="Audit &amp; Verification Notes" htmlFor="stay-audit-notes">
          <Input
            id="stay-audit-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. Folio room tariff and GST verified against booking voucher."
          />
        </Field>
      </div>

      <div className="mt-4 flex items-center justify-end gap-2 border-t border-border/50 pt-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="sm"
          loading={verifyMutation.isPending}
          onClick={() => verifyMutation.mutate()}
        >
          Confirm Audit &amp; Verify
        </Button>
      </div>
    </Modal>
  );
}
