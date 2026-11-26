import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Bug, Camera, Loader2, RefreshCw, Send, Trash2, Upload } from 'lucide-react';
import { domToBlob } from 'modern-screenshot';
import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';

import { Modal } from '@/components/Modal';
import { Button, Field } from '@/components/ui';
import { createBugReport, errorMessage } from '@/lib/api';
import { useAuth } from '@/store/auth';

export function BugReportWidget() {
  const me = useAuth((s) => s.user);
  const queryClient = useQueryClient();

  const [open, setOpen] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [screenshotBlob, setScreenshotBlob] = useState<Blob | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [comment, setComment] = useState('');
  const [capturedUrl, setCapturedUrl] = useState('');
  const [capturedTitle, setCapturedTitle] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const setNewPreview = (blob: Blob | null) => {
    setPreviewUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return blob ? URL.createObjectURL(blob) : null;
    });
    setScreenshotBlob(blob);
  };

  const captureScreen = async () => {
    setCapturing(true);
    try {
      const target = document.getElementById('root') ?? document.body;
      const blob = await domToBlob(target, {
        type: 'image/png',
        scale: Math.min(window.devicePixelRatio || 1, 1.5),
        filter: (node) => !(node instanceof HTMLElement && node.dataset?.bugIgnore === 'true'),
      });
      if (blob && blob.size > 0) {
        setNewPreview(blob);
      }
    } catch {
      toast('Could not auto-capture screenshot — you can still attach one or submit your observation.');
    } finally {
      setCapturing(false);
    }
  };

  const handleOpen = async () => {
    setCapturedUrl(window.location.pathname + window.location.search + window.location.hash);
    setCapturedTitle(document.title);
    setComment('');
    await captureScreen();
    setOpen(true);
  };

  const handleClose = () => {
    setOpen(false);
    setComment('');
    setNewPreview(null);
  };

  const submit = useMutation({
    mutationFn: () =>
      createBugReport({
        comment: comment.trim(),
        page_url: capturedUrl || window.location.pathname,
        page_title: capturedTitle || document.title,
        browser_info: `${navigator.userAgent} (${window.innerWidth}x${window.innerHeight})`,
        screenshot: screenshotBlob,
      }),
    onSuccess: (saved) => {
      toast.success(`Bug #${saved.id} submitted with screenshot for fixing!`);
      queryClient.invalidateQueries({ queryKey: ['bug-reports'] });
      handleClose();
    },
    onError: (err) => {
      toast.error(errorMessage(err, 'Could not submit bug report.'));
    },
  });

  if (!me) return null;

  return (
    <>
      <button
        type="button"
        data-bug-ignore="true"
        disabled={capturing}
        onClick={handleOpen}
        title="Capture this screen and report a bug or observation"
        className="fixed bottom-20 right-4 z-40 inline-flex items-center gap-2 rounded-full bg-red-600 px-3.5 py-2.5 text-xs font-semibold text-white shadow-lg ring-2 ring-white/80 transition-all hover:bg-red-700 active:scale-95 disabled:opacity-70 dark:ring-neutral-900 lg:bottom-6 lg:right-6"
      >
        {capturing ? <Loader2 size={15} className="animate-spin" /> : <Bug size={15} />}
        <span>{capturing ? 'Capturing...' : 'Raise Bug'}</span>
      </button>

      <Modal
        open={open}
        onClose={handleClose}
        title="Raise a Bug / Share Observation"
        description="Your current screenshot and page details are captured automatically so we can reproduce and fix the issue quickly."
        className="sm:max-w-xl"
        footer={
          <>
            <Button variant="secondary" onClick={handleClose}>
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={submit.isPending}
              disabled={comment.trim().length < 3}
              onClick={() => submit.mutate()}
            >
              <Send size={14} />
              Submit Bug Report
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="rounded-lg border border-border bg-surface-sunken px-3 py-2 text-2xs text-text-muted flex flex-wrap items-center justify-between gap-2">
            <span>
              Page: <strong className="font-mono text-text">{capturedUrl || '/'}</strong>
            </span>
            <span>
              Signed in as: <strong className="text-text">{me.full_name} ({me.role})</strong>
            </span>
          </div>

          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-semibold text-text flex items-center gap-1.5">
                <Camera size={13} className="text-red-600" />
                Captured Screenshot
              </span>
              <div className="flex items-center gap-1.5">
                <Button
                  size="sm"
                  variant="secondary"
                  className="h-7 px-2 text-2xs"
                  disabled={capturing}
                  onClick={async () => {
                    setOpen(false);
                    setTimeout(async () => {
                      await captureScreen();
                      setOpen(true);
                    }, 150);
                  }}
                >
                  <RefreshCw size={12} />
                  Re-capture
                </Button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) setNewPreview(file);
                    e.target.value = '';
                  }}
                />
                <Button
                  size="sm"
                  variant="secondary"
                  className="h-7 px-2 text-2xs"
                  onClick={() => fileInputRef.current?.click()}
                >
                  <Upload size={12} />
                  Replace Image
                </Button>
                {previewUrl && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 px-2 text-2xs text-danger"
                    onClick={() => setNewPreview(null)}
                    title="Remove screenshot"
                  >
                    <Trash2 size={12} />
                  </Button>
                )}
              </div>
            </div>

            {previewUrl ? (
              <div className="overflow-hidden rounded-lg border border-border bg-black/5 max-h-60 flex items-center justify-center">
                <img
                  src={previewUrl}
                  alt="Captured page screenshot"
                  className="max-h-60 w-auto object-contain"
                />
              </div>
            ) : (
              <div className="rounded-lg border border-dashed border-border p-4 text-center text-xs text-text-subtle">
                No screenshot attached. Click &quot;Re-capture&quot; or &quot;Replace Image&quot; to attach one.
              </div>
            )}
          </div>

          <Field
            label="What bug or issue did you observe?"
            htmlFor="bug-comment"
            required
            hint="Describe what you clicked, what you expected, and what went wrong (mandatory)."
          >
            <textarea
              id="bug-comment"
              rows={4}
              maxLength={3000}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="e.g. When I uncheck the email checkbox on cab approval, the employee still receives a notification..."
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text shadow-sm placeholder:text-text-subtle hover:border-border-strong focus:border-border-strong"
            />
          </Field>
        </div>
      </Modal>
    </>
  );
}
