import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, CheckCheck } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { Link } from 'react-router-dom';

import { Button, ItemCard, ItemList } from '@/components/ui';
import { fetchMyNotices, fetchUnreadCount, markAllRead, markNoticeRead } from '@/lib/api';
import { timeAgo } from '@/lib/time';

/**
 * The bell and its dropdown.
 *
 * Only in-app notices appear here. Nothing in this application can know whether
 * an email was opened, so counting one as read would put a number on the screen
 * that is not true.
 */
export default function NotificationBell() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);

  const count = useQuery({
    queryKey: ['unread-count'],
    queryFn: fetchUnreadCount,
    // Cheap enough to poll, and a notice that arrives while someone is looking
    // at another page should not wait for a navigation to show up.
    refetchInterval: 60_000,
  });

  const notices = useQuery({
    queryKey: ['my-notices'],
    queryFn: fetchMyNotices,
    enabled: open,
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['unread-count'] });
    queryClient.invalidateQueries({ queryKey: ['my-notices'] });
  };

  const readOne = useMutation({ mutationFn: markNoticeRead, onSuccess: refresh });
  // A single notice going grey is its own confirmation; clearing the whole list
  // is a bigger change, so it says so.
  const readAll = useMutation({
    mutationFn: markAllRead,
    meta: { errorFallback: 'Could not mark the notifications read.' },
    onSuccess: () => {
      toast.success('All notifications marked read');
      refresh();
    },
  });

  // Click outside and Escape both close it.
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (panel.current && !panel.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const unread = count.data?.unread ?? 0;
  const rows = notices.data ?? [];

  return (
    <div className="relative" ref={panel}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'}
        aria-expanded={open}
        className="relative rounded-md p-1.5 text-text-muted transition-colors hover:bg-surface-sunken hover:text-text"
      >
        <Bell size={16} />
        {unread > 0 && (
          // Brand red earns its place: this is an indicator, not a destructive
          // action. See the note on colour in the README.
          <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-brand px-1 text-[10px] font-semibold leading-none text-white">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-40 mt-2 w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg border border-border bg-overlay shadow-lg">
          <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
            <p className="text-xs font-semibold">Notifications</p>
            {unread > 0 && (
              <Button
                variant="ghost"
                size="sm"
                loading={readAll.isPending}
                onClick={() => readAll.mutate()}
              >
                <CheckCheck size={13} />
                Mark all read
              </Button>
            )}
          </div>

          <div className="max-h-96 overflow-y-auto">
            {notices.isPending ? (
              <p className="px-4 py-6 text-center text-xs text-text-subtle">Loading…</p>
            ) : rows.length === 0 ? (
              <p className="px-4 py-8 text-center text-xs text-text-subtle">
                Nothing yet. Decisions on your requests will appear here.
              </p>
            ) : (
              // Cards, so one notice never runs into the next. Tighter than on a
              // page: the panel is only 20rem wide.
              <ItemList className="space-y-2 p-2 sm:p-2">
                {rows.map((notice) => (
                  <ItemCard
                    key={notice.id}
                    accent={notice.read_at === null ? 'brand' : 'neutral'}
                    className="px-3 py-2.5 sm:px-3"
                  >
                    <div className="flex items-start gap-2">
                      {notice.read_at === null && (
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />
                      )}
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-medium">{notice.title}</p>
                        <p className="mt-0.5 text-2xs leading-relaxed text-text-muted">
                          {notice.body}
                        </p>
                        <p className="mt-1 text-2xs text-text-subtle">{timeAgo(notice.created_at)}</p>
                      </div>
                      {notice.read_at === null && (
                        <button
                          type="button"
                          onClick={() => readOne.mutate(notice.id)}
                          className="shrink-0 text-2xs text-text-subtle hover:text-text"
                          title="Mark read"
                        >
                          Read
                        </button>
                      )}
                    </div>
                  </ItemCard>
                ))}
              </ItemList>
            )}
          </div>

          <div className="border-t border-border px-4 py-2">
            <Link
              to="/notifications"
              onClick={() => setOpen(false)}
              className="text-2xs text-brand-strong hover:underline"
            >
              All notifications and settings
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
