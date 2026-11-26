import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Eye, FileCheck2, FileText, Loader2, ExternalLink, Image as ImageIcon } from 'lucide-react';
import { fetchTelemetryFileBlob } from '@/lib/api';
import { openFileTab, showFile } from '@/lib/files';

export function viewTelemetryFile(path: string, fileName = 'attachment') {
  if (!path) return;
  const tab = openFileTab();
  const name = path.split('/').pop() || fileName;
  return showFile(tab, () => fetchTelemetryFileBlob(path), name);
}

interface TelemetryFilePreviewProps {
  path?: string | null;
  name?: string;
  compact?: boolean;
  className?: string;
}

export function TelemetryFilePreview({
  path,
  name = 'Attachment',
  compact = false,
  className = '',
}: TelemetryFilePreviewProps) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  const { data: blob, isPending, isError } = useQuery({
    queryKey: ['telemetry-file', path],
    queryFn: () => fetchTelemetryFileBlob(path!),
    enabled: Boolean(path && path.trim().length > 0),
    staleTime: 10 * 60 * 1000,
    retry: 1,
  });

  useEffect(() => {
    if (!blob) {
      setObjectUrl(null);
      return;
    }
    const url = URL.createObjectURL(blob);
    setObjectUrl(url);
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [blob]);

  if (!path) {
    return <span className="text-2xs text-text-subtle italic">No attachment</span>;
  }

  if (isPending) {
    return (
      <span className="inline-flex items-center gap-1 text-2xs text-text-subtle">
        <Loader2 size={12} className="animate-spin" />
        <span>Loading attachment…</span>
      </span>
    );
  }

  if (isError || !blob) {
    return (
      <span className="inline-flex items-center gap-1 text-2xs text-amber-600 dark:text-amber-400">
        <FileCheck2 size={12} />
        <span>Attachment unavailable</span>
      </span>
    );
  }

  const isImage = blob.type.startsWith('image/') || /\.(jpg|jpeg|png|webp|gif)$/i.test(path);
  const isPdf = blob.type === 'application/pdf' || path.toLowerCase().endsWith('.pdf');

  if (compact) {
    return (
      <button
        type="button"
        onClick={() => viewTelemetryFile(path, name)}
        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded border border-border bg-surface text-2xs font-semibold text-primary hover:bg-surface-raised transition-colors cursor-pointer ${className}`}
        title={`View ${name}`}
      >
        {isImage && objectUrl ? (
          <img src={objectUrl} alt="" className="h-4 w-4 rounded-xs object-cover" />
        ) : isPdf ? (
          <FileText size={12} className="text-rose-500" />
        ) : (
          <ImageIcon size={12} className="text-primary" />
        )}
        <span>View {isImage ? 'Photo' : isPdf ? 'PDF' : 'File'}</span>
        <ExternalLink size={10} className="text-text-subtle opacity-70" />
      </button>
    );
  }

  return (
    <div className={`space-y-1.5 ${className}`}>
      {isImage && objectUrl ? (
        <div className="relative group inline-block">
          <img
            src={objectUrl}
            alt={name}
            className="h-20 w-32 object-cover rounded-md border border-border shadow-xs cursor-pointer hover:opacity-90 transition-opacity bg-surface-sunken"
            onClick={() => viewTelemetryFile(path, name)}
            title="Click to view full photo"
          />
          <div className="mt-1 flex items-center gap-2">
            <button
              type="button"
              onClick={() => viewTelemetryFile(path, name)}
              className="inline-flex items-center gap-1 text-2xs font-semibold text-primary hover:underline cursor-pointer"
            >
              <Eye size={11} />
              <span>View full size</span>
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => viewTelemetryFile(path, name)}
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border bg-surface text-xs font-medium text-primary hover:bg-surface-raised transition-colors shadow-2xs cursor-pointer"
        >
          {isPdf ? <FileText size={15} className="text-rose-500" /> : <FileCheck2 size={15} className="text-primary" />}
          <span>{name || (isPdf ? 'View PDF Invoice' : 'View Document')}</span>
          <ExternalLink size={12} className="text-text-subtle ml-0.5" />
        </button>
      )}
    </div>
  );
}
