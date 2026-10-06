import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Eye, FileText, RefreshCw, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';

import { Modal } from '@/components/Modal';
import { Badge, Button, EmptyState, Skeleton } from '@/components/ui';
import {
  discardTicket,
  fetchMyTicketsZip,
  fetchTicketFile,
  fetchTickets,
  reextractTicket,
} from '@/lib/api';
import { openFileTab, showFile } from '@/lib/files';
import { itinerary } from '@/lib/requests';
import { formatInstant } from '@/lib/time';
import type { Ticket, TravelRequest } from '@/types';

/**
 * Every file on a request, for an admin to look at again later: what it is,
 * who it is for, whether it went with the booking - and the file itself.
 *
 * A file booked for several people at once is held once per person (so each
 * can download it), but it is one file: it is listed once, with everyone it
 * is for.
 */

interface FileGroup {
  key: string;
  tickets: Ticket[];
  sent: boolean;
}

const sizeLabel = (bytes: number | null) =>
  bytes == null ? '' : bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

function groupFiles(tickets: Ticket[]): FileGroup[] {
  const groups = new Map<string, FileGroup>();
  for (const ticket of tickets) {
    if (ticket.status === 'DISCARDED') continue;
    const key = `${ticket.file_name ?? ''}|${ticket.file_size ?? ''}|${ticket.status === 'CONFIRMED'}`;
    const group = groups.get(key) ?? { key, tickets: [], sent: ticket.status === 'CONFIRMED' };
    group.tickets.push(ticket);
    groups.set(key, group);
  }
  // Sent files first, then the newest.
  return [...groups.values()].sort(
    (a, b) => Number(b.sent) - Number(a.sent) || b.tickets[0].id - a.tickets[0].id,
  );
}

/** How many separate files a request holds, as its Tickets button counts them. */
export function fileCount(request: TravelRequest): number {
  const seen = new Set<string>();
  for (const traveller of request.travellers) {
    for (const file of traveller.ticket_files ?? []) seen.add(`${file.file_name}|${file.confirmed}`);
  }
  return seen.size;
}

export function TicketFilesModal({
  request,
  onClose,
  onChanged,
}: {
  request: TravelRequest;
  onClose: () => void;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const tickets = useQuery({
    queryKey: ['tickets', request.id],
    queryFn: () => fetchTickets(request.id),
  });
  const groups = groupFiles(tickets.data ?? []);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['tickets', request.id] });
    onChanged();
  };

  const view = useMutation({
    mutationFn: (vars: { ticket: Ticket; tab: Window | null }) =>
      showFile(vars.tab, () => fetchTicketFile(vars.ticket.id), vars.ticket.file_name ?? 'ticket'),
    meta: { errorFallback: 'Could not open the file.' },
  });
  const zip = useMutation({
    mutationFn: (travellerId: number) =>
      showFile(null, () => fetchMyTicketsZip(request.id, travellerId), `request-${request.id}-tickets.zip`),
    meta: { errorFallback: 'Could not download the files.' },
  });
  const reread = useMutation({
    mutationFn: (ticket: Ticket) => reextractTicket(ticket.id),
    meta: { errorFallback: 'Could not read it again.' },
    onSuccess: (ticket) => {
      toast[ticket.status === 'FAILED' ? 'error' : 'success'](
        ticket.status === 'FAILED' ? 'Still could not read it' : 'Read - its details are on the booking window',
      );
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (ticket: Ticket) => discardTicket(ticket.id),
    meta: { errorFallback: 'Could not remove the file.' },
    onSuccess: () => {
      toast.success('File removed');
      refresh();
    },
  });

  // Travellers with booked files, for a zip each.
  const zippable = request.travellers.filter((t) => (t.ticket_files ?? []).some((f) => f.confirmed));

  return (
    <Modal
      open
      onClose={onClose}
      title={`Tickets · #${request.id}`}
      description={itinerary(request)}
      className="sm:max-w-2xl"
      footer={
        <>
          {zippable.map((t) => (
            <Button
              key={t.id}
              variant="secondary"
              loading={zip.isPending && zip.variables === t.id}
              onClick={() => zip.mutate(t.id)}
              title={`Every booked file for ${t.full_name}, in one zip`}
            >
              <Download size={14} />
              {zippable.length === 1 ? 'Download all' : t.full_name.split(' ')[0]}
            </Button>
          ))}
          <Button onClick={onClose}>Done</Button>
        </>
      }
    >
      {tickets.isPending ? (
        <div className="space-y-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : groups.length === 0 ? (
        <EmptyState
          icon={<FileText size={26} />}
          title="No files on this request"
          description="Files are added in the booking window, from Mark booked."
        />
      ) : (
        <ul className="space-y-2">
          {groups.map((group) => {
            const first = group.tickets[0];
            const failed = first.status === 'FAILED';
            return (
              <li key={group.key} className="rounded-lg border border-border px-3.5 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <FileText size={15} className="shrink-0 text-text-subtle" />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">
                    {first.file_name ?? 'File'}
                  </span>
                  <Badge tone={group.sent ? 'success' : failed ? 'warning' : 'info'}>
                    {group.sent ? 'Sent with the booking' : failed ? 'Could not read' : 'Not sent yet'}
                  </Badge>
                  <Button
                    size="sm"
                    variant="secondary"
                    loading={view.isPending && view.variables?.ticket.id === first.id}
                    onClick={() => view.mutate({ ticket: first, tab: openFileTab() })}
                  >
                    <Eye size={13} />
                    View
                  </Button>
                </div>
                <p className="mt-1 text-2xs text-text-subtle">
                  For {group.tickets.map((t) => t.traveller_name).join(', ')}
                  {first.file_size != null && ` · ${sizeLabel(first.file_size)}`}
                  {first.uploaded_by_name && ` · uploaded by ${first.uploaded_by_name}`}
                  {` · ${formatInstant(first.created_at)}`}
                  {group.sent && first.confirmed_reference && ` · booked as ${first.confirmed_reference}`}
                </p>
                {!group.sent && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {failed && (
                      <Button
                        size="sm"
                        variant="ghost"
                        loading={reread.isPending && reread.variables?.id === first.id}
                        onClick={() => reread.mutate(first)}
                      >
                        <RefreshCw size={13} />
                        Read again
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={remove.isPending && remove.variables?.id === first.id}
                      onClick={() => remove.mutate(first)}
                      title="The file is deleted; it was never sent to anyone"
                    >
                      <Trash2 size={13} />
                      Remove
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Modal>
  );
}
