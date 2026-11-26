import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bug, CheckCircle2, Clock, ExternalLink, Eye, Image as ImageIcon } from 'lucide-react';
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';

import { Modal } from '@/components/Modal';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Field,
  ItemCard,
  ItemList,
  PageHeader,
  Skeleton,
} from '@/components/ui';
import { errorMessage, fetchBugReports, fetchBugScreenshotBlob, updateBugReport } from '@/lib/api';
import { formatInstant } from '@/lib/time';
import { cn } from '@/lib/utils';
import type { BugReport, BugReportStatus } from '@/types';

const STATUS_TONES: Record<BugReportStatus, 'danger' | 'warning' | 'success'> = {
  OPEN: 'danger',
  IN_PROGRESS: 'warning',
  RESOLVED: 'success',
};

function BugScreenshotThumbnail({ bugId }: { bugId: number }) {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    let objUrl: string | null = null;
    fetchBugScreenshotBlob(bugId)
      .then((blob) => {
        if (!active) return;
        objUrl = URL.createObjectURL(blob);
        setUrl(objUrl);
      })
      .catch(() => undefined)
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      if (objUrl) URL.revokeObjectURL(objUrl);
    };
  }, [bugId]);

  if (loading) {
    return <Skeleton className="h-32 w-56 rounded-lg" />;
  }
  if (!url) {
    return (
      <div className="flex h-24 w-48 items-center justify-center rounded-lg border border-border bg-surface-sunken text-2xs text-text-subtle">
        Preview unavailable
      </div>
    );
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="group relative block w-fit overflow-hidden rounded-lg border border-border bg-black/5 shadow-2xs hover:ring-2 hover:ring-brand"
      title="Open full-size screenshot in new tab"
    >
      <img src={url} alt={`Bug #${bugId} screenshot`} className="max-h-44 w-auto object-contain" />
      <span className="absolute bottom-1.5 right-1.5 inline-flex items-center gap-1 rounded bg-black/75 px-2 py-0.5 text-2xs text-white opacity-90 group-hover:opacity-100">
        <ExternalLink size={11} />
        Full view
      </span>
    </a>
  );
}

export default function BugReportsPage() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<BugReportStatus | 'ALL'>('ALL');
  const [triaging, setTriaging] = useState<BugReport | null>(null);
  const [nextStatus, setNextStatus] = useState<BugReportStatus>('RESOLVED');
  const [note, setNote] = useState('');

  const { data: reports = [], isLoading, isError, error } = useQuery<BugReport[]>({
    queryKey: ['bug-reports'],
    queryFn: () => fetchBugReports(),
    refetchInterval: 15000,
  });

  const update = useMutation({
    mutationFn: () =>
      updateBugReport(triaging!.id, {
        status: nextStatus,
        resolution_note: note.trim() || null,
      }),
    onSuccess: (saved) => {
      toast.success(`Bug #${saved.id} marked ${saved.status.replace('_', ' ')}`);
      qc.invalidateQueries({ queryKey: ['bug-reports'] });
      setTriaging(null);
      setNote('');
    },
    onError: (err) => {
      toast.error(errorMessage(err, 'Could not update bug report.'));
    },
  });

  const openCount = reports.filter((r) => r.status === 'OPEN').length;
  const inProgressCount = reports.filter((r) => r.status === 'IN_PROGRESS').length;
  const resolvedCount = reports.filter((r) => r.status === 'RESOLVED').length;

  const visible = reports.filter((r) => filter === 'ALL' || r.status === filter);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Reported Bugs & Observations"
        description="In-app issue reports and screen captures raised by users via the Raise Bug button."
      />

      <Card>
        <CardHeader
          title={`${visible.length} ${visible.length === 1 ? 'report' : 'reports'}`}
          action={
            <div className="flex flex-wrap rounded-lg border border-border/70 p-0.5 bg-surface-raised text-xs">
              {(
                [
                  { key: 'ALL', label: `All (${reports.length})` },
                  { key: 'OPEN', label: `Open (${openCount})` },
                  { key: 'IN_PROGRESS', label: `In Progress (${inProgressCount})` },
                  { key: 'RESOLVED', label: `Resolved (${resolvedCount})` },
                ] as const
              ).map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  onClick={() => setFilter(tab.key)}
                  className={cn(
                    'rounded-md px-2.5 py-1 font-medium transition-colors',
                    filter === tab.key
                      ? 'bg-surface text-text shadow-2xs'
                      : 'text-text-muted hover:text-text',
                  )}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          }
        />

        {isLoading ? (
          <div className="space-y-3 p-5">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : isError ? (
          <EmptyState
            icon={<Bug size={28} />}
            title="Could not load bug reports"
            description={errorMessage(error)}
          />
        ) : visible.length === 0 ? (
          <EmptyState
            icon={<Bug size={28} />}
            title="No bug reports in this view"
            description="Users can click the floating Raise Bug button anywhere in the app to capture a screenshot and report an observation."
          />
        ) : (
          <ItemList>
            {visible.map((report) => (
              <ItemCard key={report.id} accent={report.status === 'RESOLVED' ? 'success' : 'danger'}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="inline-flex items-center gap-1 rounded bg-red-500/10 px-2 py-0.5 font-mono text-xs font-bold text-red-700 dark:text-red-300">
                        <Bug size={12} />#{report.id}
                      </span>
                      <Badge tone={STATUS_TONES[report.status]}>{report.status.replace('_', ' ')}</Badge>
                      <span className="text-xs font-semibold text-text">{report.reporter_name}</span>
                      {report.user_role && (
                        <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-2xs text-text-muted">
                          {report.user_role}
                        </span>
                      )}
                      <span className="text-2xs text-text-subtle">
                        {formatInstant(report.created_at)}
                      </span>
                    </div>

                    {report.page_url && (
                      <p className="text-2xs text-text-muted">
                        Screen: <code className="rounded bg-surface-sunken px-1.5 py-0.5 text-text">{report.page_url}</code>
                      </p>
                    )}

                    <div className="rounded-lg border border-border/70 bg-surface-sunken p-3 text-sm text-text whitespace-pre-wrap">
                      {report.comment}
                    </div>

                    {report.has_screenshot && (
                      <div className="space-y-1">
                        <span className="text-2xs font-medium text-text-subtle flex items-center gap-1">
                          <ImageIcon size={12} /> Captured Screenshot
                        </span>
                        <BugScreenshotThumbnail bugId={report.id} />
                      </div>
                    )}

                    {report.resolution_note && (
                      <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-900 dark:text-emerald-200">
                        <strong>Resolution Note</strong>
                        {report.resolved_by_name && ` (${report.resolved_by_name})`}: {report.resolution_note}
                      </div>
                    )}
                  </div>

                  <div className="flex shrink-0 flex-wrap gap-1.5">
                    {report.status !== 'IN_PROGRESS' && report.status !== 'RESOLVED' && (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                          setTriaging(report);
                          setNextStatus('IN_PROGRESS');
                          setNote(report.resolution_note ?? '');
                        }}
                      >
                        <Clock size={13} />
                        In Progress
                      </Button>
                    )}
                    {report.status !== 'RESOLVED' ? (
                      <Button
                        size="sm"
                        variant="primary"
                        onClick={() => {
                          setTriaging(report);
                          setNextStatus('RESOLVED');
                          setNote(report.resolution_note ?? '');
                        }}
                      >
                        <CheckCircle2 size={13} />
                        Mark Resolved
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setTriaging(report);
                          setNextStatus('OPEN');
                          setNote(report.resolution_note ?? '');
                        }}
                      >
                        <Eye size={13} />
                        Reopen
                      </Button>
                    )}
                  </div>
                </div>
              </ItemCard>
            ))}
          </ItemList>
        )}
      </Card>

      <Modal
        open={triaging !== null}
        onClose={() => setTriaging(null)}
        title={triaging ? `Update Bug #${triaging.id}` : ''}
        description="Update status and optionally add a note explaining what was fixed."
        footer={
          <>
            <Button variant="secondary" onClick={() => setTriaging(null)}>
              Cancel
            </Button>
            <Button loading={update.isPending} onClick={() => update.mutate()}>
              Save Status
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Resolution / Triage Note" htmlFor="bug-note">
            <textarea
              id="bug-note"
              rows={3}
              maxLength={2000}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. Fixed in v4 — added missing Role import and real-time refetch."
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text shadow-sm"
            />
          </Field>
        </div>
      </Modal>
    </div>
  );
}
