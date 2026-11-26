import { useEffect, useRef, useState } from 'react';
import { Camera, FlipHorizontal, RefreshCw, AlertCircle, Upload } from 'lucide-react';
import { Modal } from '@/components/Modal';
import { Button } from '@/components/ui';

interface CameraCaptureModalProps {
  open: boolean;
  onClose: () => void;
  onCapture: (file: File) => void;
  title?: string;
  description?: string;
}

export function CameraCaptureModal({
  open,
  onClose,
  onCapture,
  title = 'Take Photo with Camera',
  description = 'Align your camera and click the shutter button to take a photo.',
}: CameraCaptureModalProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [capturedBlob, setCapturedBlob] = useState<Blob | null>(null);
  const [capturedUrl, setCapturedUrl] = useState<string | null>(null);
  const [facingMode, setFacingMode] = useState<'environment' | 'user'>('environment');
  const [error, setError] = useState<string | null>(null);
  const [hasMultipleCameras, setHasMultipleCameras] = useState(false);
  const [loadingCamera, setLoadingCamera] = useState(true);

  // Stop camera tracks cleanly
  const stopStream = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
  };

  // Start camera stream
  const startCamera = async (mode: 'environment' | 'user') => {
    stopStream();
    setError(null);
    setLoadingCamera(true);

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setError('Live camera capture is not supported on this browser or connection. You can select a photo file instead.');
      setLoadingCamera(false);
      return;
    }

    try {
      const constraints: MediaStreamConstraints = {
        video: {
          facingMode: { ideal: mode },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: false,
      };

      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia(constraints);
      } catch (err: any) {
        // Fallback without ideal width/height if mobile constraints fail
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: mode },
          audio: false,
        });
      }

      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        videoRef.current.onloadedmetadata = () => {
          videoRef.current?.play().catch(() => {});
          setLoadingCamera(false);
        };
      } else {
        setLoadingCamera(false);
      }

      // Check if device has multiple cameras
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const videoInputs = devices.filter((d) => d.kind === 'videoinput');
        setHasMultipleCameras(videoInputs.length > 1);
      } catch {
        setHasMultipleCameras(false);
      }
    } catch (err: any) {
      console.error('Camera access error:', err);
      let msg = 'Could not access camera. Please allow camera permissions or upload from files.';
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        msg = 'Camera permission was denied. Please allow camera access in your browser settings or select a file.';
      } else if (err.name === 'NotFoundError' || err.name === 'DevicesNotFoundError') {
        msg = 'No camera found on this device. You can upload a photo file instead.';
      } else if (err.name === 'NotReadableError' || err.name === 'TrackStartError') {
        msg = 'Camera is currently in use by another application.';
      }
      setError(msg);
      setLoadingCamera(false);
    }
  };

  useEffect(() => {
    if (open) {
      setCapturedBlob(null);
      setCapturedUrl(null);
      startCamera(facingMode);
    } else {
      stopStream();
    }
    return () => {
      stopStream();
    };
  }, [open]);

  // Handle camera flip (front/rear)
  const toggleCamera = () => {
    const nextMode = facingMode === 'environment' ? 'user' : 'environment';
    setFacingMode(nextMode);
    startCamera(nextMode);
  };

  // Capture current video frame to canvas
  const takePhoto = () => {
    if (!videoRef.current) return;
    const video = videoRef.current;
    const width = video.videoWidth || 1280;
    const height = video.videoHeight || 720;

    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Flip horizontally if using front/user camera
    if (facingMode === 'user') {
      ctx.translate(width, 0);
      ctx.scale(-1, 1);
    }

    ctx.drawImage(video, 0, 0, width, height);

    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        setCapturedBlob(blob);
        const url = URL.createObjectURL(blob);
        setCapturedUrl(url);
        stopStream();
      },
      'image/jpeg',
      0.92
    );
  };

  // Retake photo
  const retake = () => {
    if (capturedUrl) {
      URL.revokeObjectURL(capturedUrl);
    }
    setCapturedBlob(null);
    setCapturedUrl(null);
    startCamera(facingMode);
  };

  // Confirm photo
  const confirmPhoto = () => {
    if (!capturedBlob) return;
    const filename = `camera_capture_${Date.now()}.jpg`;
    const file = new File([capturedBlob], filename, { type: 'image/jpeg' });
    stopStream();
    onCapture(file);
    onClose();
  };

  // File fallback
  const handleFallbackFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) {
      stopStream();
      onCapture(f);
      onClose();
    }
  };

  return (
    <Modal
      open={open}
      onClose={() => {
        stopStream();
        onClose();
      }}
      title={title}
      description={description}
      className="max-w-lg"
    >
      <div className="space-y-4">
        {error ? (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-800 dark:text-amber-300 space-y-3">
            <div className="flex items-start gap-2">
              <AlertCircle size={16} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
              <div>
                <p className="font-semibold">{error}</p>
                <p className="mt-1 text-2xs text-text-muted">
                  You can click below to choose or snap an image using your device&apos;s file browser.
                </p>
              </div>
            </div>

            <div>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={handleFallbackFile}
              />
              <Button
                variant="secondary"
                size="sm"
                className="w-full gap-2"
                onClick={() => fileInputRef.current?.click()}
              >
                <Upload size={14} />
                Select Photo from Files
              </Button>
            </div>
          </div>
        ) : capturedUrl ? (
          // Captured photo preview
          <div className="space-y-3">
            <div className="relative aspect-4/3 w-full overflow-hidden rounded-xl bg-black border border-border flex items-center justify-center">
              <img
                src={capturedUrl}
                alt="Captured Preview"
                className="h-full w-full object-contain"
              />
              <span className="absolute top-2 left-2 rounded-full bg-emerald-600/90 text-white text-2xs px-2.5 py-0.5 font-medium shadow-xs">
                Photo Captured
              </span>
            </div>

            <div className="flex items-center justify-between gap-2 pt-1">
              <Button variant="secondary" size="sm" onClick={retake} className="gap-1.5">
                <RefreshCw size={13} />
                Retake Photo
              </Button>
              <Button variant="primary" size="sm" onClick={confirmPhoto} className="gap-1.5">
                <Camera size={14} />
                Use This Photo
              </Button>
            </div>
          </div>
        ) : (
          // Live camera viewfinder
          <div className="space-y-3">
            <div className="relative aspect-4/3 w-full overflow-hidden rounded-xl bg-black border border-border flex items-center justify-center">
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className={`h-full w-full object-cover ${facingMode === 'user' ? 'scale-x-[-1]' : ''}`}
              />

              {/* Viewfinder Target Guidelines */}
              <div className="pointer-events-none absolute inset-6 rounded-lg border-2 border-dashed border-white/40 flex items-center justify-center">
                <div className="h-6 w-6 border-t-2 border-l-2 border-white/80 absolute top-0 left-0" />
                <div className="h-6 w-6 border-t-2 border-r-2 border-white/80 absolute top-0 right-0" />
                <div className="h-6 w-6 border-b-2 border-l-2 border-white/80 absolute bottom-0 left-0" />
                <div className="h-6 w-6 border-b-2 border-r-2 border-white/80 absolute bottom-0 right-0" />
              </div>

              {loadingCamera && (
                <div className="absolute inset-0 bg-black/70 flex flex-col items-center justify-center text-white text-xs gap-2">
                  <RefreshCw size={20} className="animate-spin text-primary" />
                  <span>Connecting to camera...</span>
                </div>
              )}

              {/* Camera Switch Button (if multiple cameras available) */}
              {hasMultipleCameras && !loadingCamera && (
                <button
                  type="button"
                  onClick={toggleCamera}
                  className="absolute top-3 right-3 rounded-full bg-black/60 backdrop-blur-sm p-2 text-white hover:bg-black/80 transition-colors shadow-md"
                  title="Switch camera"
                >
                  <FlipHorizontal size={16} />
                </button>
              )}
            </div>

            {/* Shutter capture controls */}
            <div className="flex flex-col items-center justify-center gap-2 pt-2">
              <button
                type="button"
                onClick={takePhoto}
                disabled={loadingCamera}
                className="group relative flex h-16 w-16 items-center justify-center rounded-full border-4 border-primary bg-white text-primary shadow-lg transition-transform active:scale-95 disabled:opacity-50 hover:bg-primary hover:text-white"
                title="Click to capture photo"
              >
                <Camera size={26} className="transition-transform group-hover:scale-110" />
              </button>
              <span className="text-2xs text-text-subtle font-medium">
                Tap button to take photo
              </span>
            </div>
          </div>
        )}
      </div>

      <div className="mt-4 flex items-center justify-end border-t border-border/50 pt-3">
        <Button variant="ghost" size="sm" onClick={() => { stopStream(); onClose(); }}>
          Cancel
        </Button>
      </div>
    </Modal>
  );
}
