import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Camera,
  Clock,
  Gauge,
  MapPin,
  Plus,
  ShieldCheck,
  Upload,
} from 'lucide-react';
import toast from 'react-hot-toast';

import { CameraCaptureModal } from '@/components/CameraCaptureModal';
import { TelemetryFilePreview } from '@/components/TelemetryFilePreview';
import { Modal } from '@/components/Modal';
import { Button, Input, Field } from '@/components/ui';
import {
  endCabTrip,
  fetchCabTelemetries,
  forceCloseCabTelemetry,
  startCabTrip,
  uploadTelemetryFile,
  verifyCabTelemetry,
} from '@/lib/api';
import { formatInstant } from '@/lib/time';
import { useAuth } from '@/store/auth';
import type { CabTelemetry, RequestStatus } from '@/types';

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

interface CabTelemetryCardProps {
  requestId: number;
  requestStatus: RequestStatus;
  cabTrip?: string | null;
  assignedDriverName?: string | null;
  assignedDriverPhone?: string | null;
  assignedVehicleNumber?: string | null;
  /** Reviewer mode (Manager / Admin queue): hides traveller trip-upload actions, keeps Force Close & Verify. */
  readOnly?: boolean;
  onUpdated?: () => void;
}

export function CabTelemetryCard({
  requestId,
  requestStatus,
  cabTrip,
  assignedDriverName,
  assignedDriverPhone,
  assignedVehicleNumber,
  readOnly = false,
  onUpdated,
}: CabTelemetryCardProps) {
  const qc = useQueryClient();
  const me = useAuth((s) => s.user);

  const [startModalOpen, setStartModalOpen] = useState(false);
  const [endModalTelemetry, setEndModalTelemetry] = useState<CabTelemetry | null>(null);
  const [verifyModalTelemetry, setVerifyModalTelemetry] = useState<CabTelemetry | null>(null);
  const [forceCloseTelemetry, setForceCloseTelemetry] = useState<CabTelemetry | null>(null);

  const { data: telemetries = [], isLoading } = useQuery<CabTelemetry[]>({
    queryKey: ['cab-telemetry', requestId],
    queryFn: () => fetchCabTelemetries(requestId),
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

  const canRecordTrip = ['APPROVED', 'BOOKED', 'READY_FOR_VENDOR'].includes(requestStatus);

  if (isLoading) {
    return (
      <div className="mt-3 rounded-xl border border-border/60 bg-surface/40 p-4 text-xs text-text-muted animate-pulse">
        Loading cab telemetry records...
      </div>
    );
  }

  // Active in-progress trip that needs an end-of-day reading
  const activeTrip = telemetries.find((t) => t.status === 'IN_PROGRESS');

  return (
    <div className="mt-3 rounded-xl border border-border/80 bg-surface/70 backdrop-blur-xs p-4 shadow-2xs">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/50 pb-3">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
            <Gauge size={16} />
          </div>
          <div>
            <h4 className="text-sm font-semibold text-text flex flex-wrap items-center gap-2">
              In-Transit Odometer Telemetry
              {activeTrip ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-sky-500/15 px-2 py-0.5 text-2xs font-semibold text-sky-700 dark:text-sky-300">
                  <span className="h-1.5 w-1.5 rounded-full bg-sky-500 animate-pulse" />
                  Trip Started · In Progress
                </span>
              ) : telemetries.length > 0 ? (
                <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-2xs font-semibold text-emerald-700 dark:text-emerald-300">
                  Trip Completed ({telemetries.length} {telemetries.length === 1 ? 'trip' : 'trips'})
                </span>
              ) : (
                <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-2xs font-semibold text-amber-800 dark:text-amber-300">
                  Trip Not Started
                </span>
              )}
            </h4>
            <p className="text-2xs text-text-subtle">
              Daily GPS and odometer photo tracking for {cabTrip ? cabTrip.toLowerCase() : 'cab'} trips against package &amp; vendor billing.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {!readOnly && canRecordTrip && !activeTrip && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setStartModalOpen(true)}
              className="gap-1.5 text-xs"
            >
              <Plus size={13} />
              Start Morning Trip
            </Button>
          )}

          {!readOnly && canRecordTrip && activeTrip && (
            <Button
              size="sm"
              variant="primary"
              onClick={() => setEndModalTelemetry(activeTrip)}
              className="gap-1.5 text-xs"
            >
              <Clock size={13} />
              Record Evening End Trip
            </Button>
          )}

          {canRecordTrip && activeTrip && isManagerOrAdmin && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setForceCloseTelemetry(activeTrip)}
              className="gap-1.5 text-xs text-danger hover:bg-danger/10 border-danger/40"
              title="Force-close trip if employee forgot or could not submit ending photo"
            >
              <AlertTriangle size={13} />
              Force Close
            </Button>
          )}
        </div>
      </div>

      {telemetries.length === 0 ? (
        <div className="py-4 text-center text-xs text-text-muted">
          {readOnly
            ? 'No cab odometer telemetry uploaded by the traveller yet.'
            : 'No cab trips recorded yet. Click "Start Morning Trip" when dispatching for the day.'}
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          {telemetries.map((t) => (
            <div
              key={t.id}
              className={`rounded-lg border p-3 text-xs transition-colors ${
                t.status === 'IN_PROGRESS'
                  ? 'border-info/70 bg-info-soft/40'
                  : 'border-border/70 bg-surface'
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/40 pb-2">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-text">Trip #{t.id}</span>
                  {t.status === 'IN_PROGRESS' && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-info/10 px-2 py-0.5 text-2xs font-medium text-info">
                      <Clock size={10} className="animate-spin" /> In Progress
                    </span>
                  )}
                  {t.status === 'COMPLETED' && !t.is_verified && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-2xs font-medium text-amber-800 dark:text-amber-300">
                      Completed · Awaiting Audit
                    </span>
                  )}
                  {t.is_verified && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-2xs font-medium text-emerald-800 dark:text-emerald-300">
                      <ShieldCheck size={11} /> Verified by {t.verified_by_name || 'Finance'}
                    </span>
                  )}
                </div>

                {/* Audit & Verify button */}
                {isAdminOrFinance && t.status === 'COMPLETED' && !t.is_verified && (
                  <Button
                    size="sm"
                    variant="secondary"
                    className="h-7 text-2xs"
                    onClick={() => setVerifyModalTelemetry(t)}
                  >
                    Verify Telemetry
                  </Button>
                )}
              </div>

              {/* Trip details grid */}
              <div className="mt-2.5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {/* Morning Start */}
                <div className="rounded-md border border-border/50 bg-surface-raised p-2">
                  <span className="block text-2xs font-medium text-text-subtle">Morning Start</span>
                  <p className="mt-0.5 text-xs font-semibold text-text">
                    {t.start_odometer_km != null ? `${t.start_odometer_km} km` : '—'}
                  </p>
                  <p className="text-2xs text-text-muted">
                    {t.start_time_ist ? formatInstant(t.start_time_ist) : '—'}
                  </p>
                  {t.start_gps_lat != null && t.start_gps_lng != null && (
                    <div className="mt-1 space-y-0.5">
                      {t.start_location_name && (
                        <p className="flex items-start gap-1 text-2xs font-medium text-text">
                          <MapPin size={10} className="mt-0.5 shrink-0 text-emerald-600" />
                          <span>{t.start_location_name}</span>
                        </p>
                      )}
                      <a
                        href={`https://www.google.com/maps?q=${t.start_gps_lat},${t.start_gps_lng}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-2xs text-text-subtle hover:text-primary hover:underline"
                      >
                        {!t.start_location_name && <MapPin size={10} />}
                        {t.start_gps_lat.toFixed(4)}, {t.start_gps_lng.toFixed(4)}
                      </a>
                    </div>
                  )}
                  {t.start_odometer_photo_url && (
                    <div className="mt-1.5">
                      <TelemetryFilePreview path={t.start_odometer_photo_url} compact name="Morning Odometer" />
                    </div>
                  )}
                </div>

                {/* Evening End */}
                <div className="rounded-md border border-border/50 bg-surface-raised p-2">
                  <span className="block text-2xs font-medium text-text-subtle">Evening End</span>
                  <p className="mt-0.5 text-xs font-semibold text-text">
                    {t.end_odometer_km != null ? `${t.end_odometer_km} km` : 'Pending'}
                  </p>
                  <p className="text-2xs text-text-muted">
                    {t.end_time_ist ? formatInstant(t.end_time_ist) : 'In progress'}
                  </p>
                  {t.end_gps_lat != null && t.end_gps_lng != null && (
                    <div className="mt-1 space-y-0.5">
                      {t.end_location_name && (
                        <p className="flex items-start gap-1 text-2xs font-medium text-text">
                          <MapPin size={10} className="mt-0.5 shrink-0 text-emerald-600" />
                          <span>{t.end_location_name}</span>
                        </p>
                      )}
                      <a
                        href={`https://www.google.com/maps?q=${t.end_gps_lat},${t.end_gps_lng}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-2xs text-text-subtle hover:text-primary hover:underline"
                      >
                        {!t.end_location_name && <MapPin size={10} />}
                        {t.end_gps_lat.toFixed(4)}, {t.end_gps_lng.toFixed(4)}
                      </a>
                    </div>
                  )}
                  {t.end_odometer_photo_url && (
                    <div className="mt-1.5">
                      <TelemetryFilePreview path={t.end_odometer_photo_url} compact name="Evening Odometer" />
                    </div>
                  )}
                </div>

                {/* Calculated Distance */}
                <div className="rounded-md border border-border/50 bg-surface-raised p-2">
                  <span className="block text-2xs font-medium text-text-subtle">Total Distance</span>
                  <p className="mt-0.5 text-base font-bold text-text">
                    {t.total_km != null ? `${t.total_km} km` : '—'}
                  </p>
                  {t.total_duration_minutes != null && (
                    <p className="text-2xs text-text-muted">{t.total_duration_minutes} minutes duration</p>
                  )}
                  {t.is_local_exceeded && (
                    <span className="mt-1 inline-flex items-center gap-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-2xs font-medium text-amber-800 dark:text-amber-300">
                      <AlertTriangle size={10} /> +{t.local_excess_km} km over 80 km cap
                    </span>
                  )}
                </div>

                {/* Car & Driver */}
                <div className="rounded-md border border-border/50 bg-surface-raised p-2">
                  <span className="block text-2xs font-medium text-text-subtle">Assigned Cab</span>
                  <p className="mt-0.5 text-xs font-semibold text-text">
                    {t.vehicle_number || 'Vehicle Assigned'}
                  </p>
                  <p className="text-2xs text-text-muted">
                    Driver: {t.driver_name || 'Assigned'} {t.driver_phone && `(${t.driver_phone})`}
                  </p>
                  {t.verification_notes && (
                    <p className="mt-1 text-2xs italic text-text-subtle">
                      Audit note: &quot;{t.verification_notes}&quot;
                    </p>
                  )}
                </div>
              </div>

              {t.force_closed_at && (
                <div className="mt-2 rounded bg-amber-500/10 p-2 text-2xs text-amber-800 dark:text-amber-300 border border-amber-500/30">
                  <strong>Force-closed by {t.force_closed_by_name || 'Manager'}:</strong> &quot;{t.force_close_reason}&quot; on {formatInstant(t.force_closed_at)}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Start Trip Modal */}
      {startModalOpen && (
        <StartCabTripModal
          requestId={requestId}
          assignedDriverName={assignedDriverName}
          assignedDriverPhone={assignedDriverPhone}
          assignedVehicleNumber={assignedVehicleNumber}
          isOpen={startModalOpen}
          onClose={() => setStartModalOpen(false)}
          onSuccess={() => {
            setStartModalOpen(false);
            qc.invalidateQueries({ queryKey: ['cab-telemetry', requestId] });
            onUpdated?.();
          }}
        />
      )}

      {/* End Trip Modal */}
      {endModalTelemetry && (
        <EndCabTripModal
          requestId={requestId}
          telemetry={endModalTelemetry}
          isOpen={Boolean(endModalTelemetry)}
          onClose={() => setEndModalTelemetry(null)}
          onSuccess={() => {
            setEndModalTelemetry(null);
            qc.invalidateQueries({ queryKey: ['cab-telemetry', requestId] });
            onUpdated?.();
          }}
        />
      )}

      {/* Force Close Modal */}
      {forceCloseTelemetry && (
        <ForceCloseCabTripModal
          requestId={requestId}
          telemetry={forceCloseTelemetry}
          isOpen={Boolean(forceCloseTelemetry)}
          onClose={() => setForceCloseTelemetry(null)}
          onSuccess={() => {
            setForceCloseTelemetry(null);
            qc.invalidateQueries({ queryKey: ['cab-telemetry', requestId] });
            onUpdated?.();
          }}
        />
      )}

      {/* Verify Telemetry Modal */}
      {verifyModalTelemetry && (
        <VerifyTelemetryModal
          requestId={requestId}
          telemetryId={verifyModalTelemetry.id}
          isOpen={Boolean(verifyModalTelemetry)}
          onClose={() => setVerifyModalTelemetry(null)}
          onSuccess={() => {
            setVerifyModalTelemetry(null);
            qc.invalidateQueries({ queryKey: ['cab-telemetry', requestId] });
            onUpdated?.();
          }}
        />
      )}
    </div>
  );
}

function StartCabTripModal({
  requestId,
  assignedDriverName,
  assignedDriverPhone,
  assignedVehicleNumber,
  isOpen,
  onClose,
  onSuccess,
}: {
  requestId: number;
  assignedDriverName?: string | null;
  assignedDriverPhone?: string | null;
  assignedVehicleNumber?: string | null;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [km, setKm] = useState<number | ''>('');
  const [driverName, setDriverName] = useState(assignedDriverName || '');
  const [driverPhone, setDriverPhone] = useState(assignedDriverPhone || '');
  const [vehicleNumber, setVehicleNumber] = useState(assignedVehicleNumber || '');
  const [differentCab, setDifferentCab] = useState(false);
  const [photoUrl, setPhotoUrl] = useState('');
  const [photoSource, setPhotoSource] = useState<'CAMERA' | 'FILE_UPLOAD' | null>(null);
  const [uploading, setUploading] = useState(false);
  const [cameraModalOpen, setCameraModalOpen] = useState(false);

  const startMutation = useMutation({
    mutationFn: async () => {
      const gps = await getCurrentGps();
      return startCabTrip(requestId, {
        start_odometer_km: Number(km),
        start_odometer_photo_url: photoUrl || null,
        start_photo_source: photoSource || undefined,
        start_gps_lat: gps?.lat,
        start_gps_lng: gps?.lng,
        driver_name: driverName.trim() || undefined,
        driver_phone: driverPhone.trim() || undefined,
        vehicle_number: vehicleNumber.trim() || undefined,
      });
    },
    onSuccess: () => {
      toast.success('Morning trip started successfully with GPS!');
      onSuccess();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to start trip');
    },
  });

  const handlePhotoUpload = async (file: File, source: 'CAMERA' | 'FILE_UPLOAD') => {
    try {
      setUploading(true);
      const res = await uploadTelemetryFile(file);
      setPhotoUrl(res.file_path);
      setPhotoSource(source);
      toast.success(`Odometer photo attached via ${source === 'CAMERA' ? 'Camera' : 'File Upload'}`);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to upload photo');
    } finally {
      setUploading(false);
    }
  };

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title="Start Morning Cab Trip"
      description="Record starting odometer reading and snap/upload photo before departing."
      className="max-w-md"
    >
      <div className="space-y-3">
        <Field label="Start Odometer Reading (KM)" required htmlFor="start-km">
          <Input
            id="start-km"
            type="number"
            min="0"
            step="0.1"
            value={km}
            onChange={(e) => setKm(e.target.value === '' ? '' : parseFloat(e.target.value))}
            placeholder="e.g. 14250.0"
          />
        </Field>

        {/* Camera or File selection */}
        <div className="space-y-1.5">
          <label className="text-2xs font-semibold text-text uppercase tracking-wider block">
            Odometer Photo (Camera or Files) <span className="text-danger">*</span>
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
              title="Capture Starting Odometer Photo"
              description="Align the dashboard odometer display in the camera frame and tap to capture."
              onCapture={(file) => handlePhotoUpload(file, 'CAMERA')}
            />
          )}
        </div>

        {/* Assigned cab details and different cab toggle */}
        <div className="rounded-lg border border-border/60 bg-surface-raised/60 p-3 space-y-2 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-2xs font-bold uppercase tracking-wider text-text-subtle">
              Driver &amp; Vehicle Details
            </span>
            <button
              type="button"
              onClick={() => setDifferentCab(!differentCab)}
              className="text-2xs font-medium text-primary hover:underline"
            >
              {differentCab ? 'Use assigned details' : 'Different cab? Enter manually'}
            </button>
          </div>

          {!differentCab ? (
            <div className="grid grid-cols-2 gap-2 text-2xs pt-0.5">
              <div>
                <span className="text-text-muted">Driver: </span>
                <strong className="text-text">{driverName || 'Pre-assigned by vendor'}</strong>
              </div>
              <div>
                <span className="text-text-muted">Phone: </span>
                <strong className="text-text">{driverPhone || '—'}</strong>
              </div>
              <div className="col-span-2">
                <span className="text-text-muted">Vehicle No: </span>
                <strong className="text-text font-mono uppercase">
                  {vehicleNumber || 'Pre-assigned cab'}
                </strong>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2 pt-1">
              <Field label="Driver Name" htmlFor="driver-name">
                <Input
                  id="driver-name"
                  value={driverName}
                  onChange={(e) => setDriverName(e.target.value)}
                  placeholder="e.g. Rajesh Kumar"
                />
              </Field>
              <Field label="Driver Phone" htmlFor="driver-phone">
                <Input
                  id="driver-phone"
                  value={driverPhone}
                  onChange={(e) => setDriverPhone(e.target.value)}
                  placeholder="e.g. 9876543210"
                />
              </Field>
              <Field label="Vehicle No." htmlFor="vehicle-num" className="col-span-2">
                <Input
                  id="vehicle-num"
                  value={vehicleNumber}
                  onChange={(e) => setVehicleNumber(e.target.value)}
                  placeholder="e.g. DL01AB1234"
                />
              </Field>
            </div>
          )}
        </div>
      </div>

      <div className="mt-4 flex items-center justify-end gap-2 border-t border-border/50 pt-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={km === '' || startMutation.isPending || uploading}
          loading={startMutation.isPending}
          onClick={() => startMutation.mutate()}
        >
          Confirm &amp; Start Trip
        </Button>
      </div>
    </Modal>
  );
}

function EndCabTripModal({
  requestId,
  telemetry,
  isOpen,
  onClose,
  onSuccess,
}: {
  requestId: number;
  telemetry: CabTelemetry;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [km, setKm] = useState<number | ''>('');
  const [photoUrl, setPhotoUrl] = useState('');
  const [photoSource, setPhotoSource] = useState<'CAMERA' | 'FILE_UPLOAD' | null>(null);
  const [uploading, setUploading] = useState(false);
  const [cameraModalOpen, setCameraModalOpen] = useState(false);

  const endMutation = useMutation({
    mutationFn: async () => {
      const gps = await getCurrentGps();
      return endCabTrip(requestId, telemetry.id, {
        end_odometer_km: Number(km),
        end_odometer_photo_url: photoUrl || null,
        end_photo_source: photoSource || undefined,
        end_gps_lat: gps?.lat,
        end_gps_lng: gps?.lng,
      });
    },
    onSuccess: (res) => {
      toast.success(`Trip completed! Total distance: ${res.total_km} km`);
      onSuccess();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to complete trip');
    },
  });

  const handlePhotoUpload = async (file: File, source: 'CAMERA' | 'FILE_UPLOAD') => {
    try {
      setUploading(true);
      const res = await uploadTelemetryFile(file);
      setPhotoUrl(res.file_path);
      setPhotoSource(source);
      toast.success(`Ending odometer photo attached via ${source === 'CAMERA' ? 'Camera' : 'File Upload'}`);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to upload photo');
    } finally {
      setUploading(false);
    }
  };

  const computedDelta =
    km !== '' && telemetry.start_odometer_km != null
      ? Math.max(0, Number(km) - telemetry.start_odometer_km)
      : null;

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title="Record Evening End Trip"
      description={`Morning start was ${telemetry.start_odometer_km} km. Enter evening odometer reading.`}
      className="max-w-md"
    >
      <div className="space-y-3">
        <Field label="End Odometer Reading (KM)" required htmlFor="end-km">
          <Input
            id="end-km"
            type="number"
            min={telemetry.start_odometer_km || 0}
            step="0.1"
            value={km}
            onChange={(e) => setKm(e.target.value === '' ? '' : parseFloat(e.target.value))}
            placeholder={`Must be ≥ ${telemetry.start_odometer_km || 0}`}
          />
        </Field>

        {computedDelta !== null && (
          <div className="rounded-md bg-surface-raised p-2.5 text-xs">
            <span className="text-text-muted">Computed Day Trip Distance: </span>
            <span className="font-bold text-text text-sm">{computedDelta.toFixed(1)} km</span>
            {computedDelta > 80 && (
              <span className="ml-2 inline-flex items-center gap-1 text-2xs text-amber-600 font-medium">
                <AlertTriangle size={11} /> +{(computedDelta - 80).toFixed(1)} km over 80 km limit
              </span>
            )}
          </div>
        )}

        {/* Camera or File selection */}
        <div className="space-y-1.5">
          <label className="text-2xs font-semibold text-text uppercase tracking-wider block">
            Ending Odometer Photo (Camera or Files) <span className="text-danger">*</span>
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
              title="Capture Ending Odometer Photo"
              description="Align the vehicle ending odometer in the camera frame and tap to capture."
              onCapture={(file) => handlePhotoUpload(file, 'CAMERA')}
            />
          )}
        </div>
      </div>

      <div className="mt-4 flex items-center justify-end gap-2 border-t border-border/50 pt-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={
            km === '' ||
            (telemetry.start_odometer_km != null && Number(km) < telemetry.start_odometer_km) ||
            endMutation.isPending ||
            uploading
          }
          loading={endMutation.isPending}
          onClick={() => endMutation.mutate()}
        >
          Submit &amp; End Trip
        </Button>
      </div>
    </Modal>
  );
}

function ForceCloseCabTripModal({
  requestId,
  telemetry,
  isOpen,
  onClose,
  onSuccess,
}: {
  requestId: number;
  telemetry: CabTelemetry;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [reason, setReason] = useState('');

  const closeMutation = useMutation({
    mutationFn: () => forceCloseCabTelemetry(requestId, telemetry.id, reason.trim()),
    onSuccess: () => {
      toast.success('Trip force-closed successfully. Employee is now unblocked.');
      onSuccess();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to force-close trip');
    },
  });

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title="Manager / Admin Force-Close Cab Trip"
      description="If the employee forgot to end the trip or check out, Managers and Admins can force-close this trip so the employee can proceed with future bookings."
      className="max-w-md"
    >
      <div className="space-y-3">
        <div className="rounded-lg bg-amber-500/10 border border-amber-500/30 p-3 text-xs text-amber-800 dark:text-amber-300">
          Trip #{telemetry.id} started at {telemetry.start_odometer_km} km. Force-closing will mark this trip as completed without blocking future cab or hotel requests.
        </div>

        <Field label="Reason for Force-Closing Trip" required htmlFor="force-close-reason">
          <Input
            id="force-close-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Employee forgot to end trip; driver confirmed tour completed."
          />
        </Field>
      </div>

      <div className="mt-4 flex items-center justify-end gap-2 border-t border-border/50 pt-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="danger"
          size="sm"
          disabled={!reason.trim() || closeMutation.isPending}
          loading={closeMutation.isPending}
          onClick={() => closeMutation.mutate()}
        >
          Force Close &amp; Unblock Employee
        </Button>
      </div>
    </Modal>
  );
}

function VerifyTelemetryModal({
  requestId,
  telemetryId,
  isOpen,
  onClose,
  onSuccess,
}: {
  requestId: number;
  telemetryId: number;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [notes, setNotes] = useState('');

  const verifyMutation = useMutation({
    mutationFn: () => verifyCabTelemetry(requestId, telemetryId, { notes: notes || null }),
    onSuccess: () => {
      toast.success('Cab telemetry verified and audited against invoice');
      onSuccess();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to verify telemetry');
    },
  });

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title="Audit &amp; Verify Cab Telemetry"
      description="Verify GPS timestamps, odometer photos, and distance against contracted vendor billing."
      className="max-w-md"
    >
      <div className="space-y-3">
        <Field label="Verification Notes / Reconciliation" htmlFor="audit-notes">
          <Input
            id="audit-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. Odometer photos matched vendor daily log sheet."
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
