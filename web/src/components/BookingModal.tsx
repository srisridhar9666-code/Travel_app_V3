import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CalendarPlus,
  Eye,
  FileText,
  IndianRupee,
  Loader2,
  Mail,
  Ticket as TicketIcon,
  Upload,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';

import {
  BookingFields,
  EMPTY_BOOKING,
  bookingBody,
  bookingDraftValid,
  type BookingDraft,
} from '@/components/BookingDetails';
import {
  CabBookingFields,
  cabDraftComplete,
  cabDraftEmpty,
  cabDraftFrom,
  type CabDraft,
} from '@/components/CabDetails';
import { formatMoney } from '@/components/charts';
import { Modal } from '@/components/Modal';
import { Badge, Button, Field, Input } from '@/components/ui';
import { VendorSelect, sharedVendor } from '@/components/VendorSelect';
import {
  bookTravellers,
  errorMessage,
  fetchCombinedTickets,
  fetchRequest,
  fetchTicketFile,
  fetchTickets,
  uploadTicket,
  type BookingBody,
} from '@/lib/api';
import { openFileTab, showFile } from '@/lib/files';
import { cabAsked, campaignLabel, itinerary } from '@/lib/requests';
import { cn } from '@/lib/utils';
import type { CabType, CombinedTickets, RequestTraveller, Ticket, TravelRequest } from '@/types';

/**
 * Booking, in one window: who, the car or the ticket, every file, what it cost
 * and who was paid, the note - and one email.
 *
 * Several approved travellers on one request can be booked together: a shared
 * cab, rooms on one hotel booking, a group PNR. They all get the same
 * reference, details and files, in one email with their managers copied, and
 * a cost is the total for all of them, split evenly. A cab books everyone
 * approved on it by default; anything else books the person clicked.
 *
 * An extension (a cab kept longer, a stay made longer) can be booked "the same
 * as before" in one tap: the car and driver, or the hotel, from the trip it
 * carries on. The admin changes whatever differs.
 */

const MONEY = /^\d{1,10}(\.\d{1,2})?$/;

/** What an even split comes to, to the paisa, the way the server does it: the
 *  first traveller absorbs the odd paisa. Display only - the server splits. */
function evenSplit(total: string, shares: number): number[] | null {
  const tidy = total.trim();
  if (!MONEY.test(tidy) || shares < 1) return null;
  const [rupees, paise = ''] = tidy.split('.');
  const all = Number(rupees) * 100 + Number(`${paise}00`.slice(0, 2));
  const base = Math.floor(all / shares);
  const extra = all - base * shares;
  return Array.from({ length: shares }, (_, i) => (base + (i < extra ? 1 : 0)) / 100);
}

const names = (people: string[]) =>
  people.length <= 1
    ? people.join('')
    : `${people.slice(0, -1).join(', ')} and ${people[people.length - 1]}`;

const tidyPlate = (plate: string) => plate.trim().replace(/\s+/g, ' ').toUpperCase();

/** Saved booking details back into the form's strings. */
function draftOfDetails(details: RequestTraveller['booking_details']): BookingDraft {
  const out: BookingDraft = { ...EMPTY_BOOKING };
  for (const [key, value] of Object.entries(details ?? {})) {
    if (!value || !(key in out)) continue;
    out[key as keyof BookingDraft] =
      key === 'depart_at' || key === 'arrive_at' ? String(value).slice(0, 16) : String(value);
  }
  return out;
}

/** Run `work` over `items`, at most `limit` at a time; every outcome kept. */
async function settleEach<T, R>(
  items: T[],
  limit: number,
  work: (item: T) => Promise<R>,
): Promise<PromiseSettledResult<R>[]> {
  const out: PromiseSettledResult<R>[] = new Array(items.length);
  let next = 0;
  const lane = async () => {
    while (next < items.length) {
      const index = next++;
      try {
        out[index] = { status: 'fulfilled', value: await work(items[index]) };
      } catch (reason) {
        out[index] = { status: 'rejected', reason };
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane));
  return out;
}

/** "14 Oct 2026" for a plain date, never through a time zone. */
const plainDate = (iso: string) => {
  const [year, month, day] = iso.slice(0, 10).split('-');
  const name = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][
    Number(month) - 1
  ];
  return name ? `${day} ${name} ${year}` : iso;
};

/** What the files say together, as booking fields - only the ones they give. */
function fieldsOf(read: CombinedTickets, hotel: boolean): Partial<BookingDraft> {
  const out: Partial<BookingDraft> = {};
  if (!hotel && read.carrier) out.carrier = read.carrier;
  if (read.service_number) out.service_number = read.service_number;
  if (read.depart_at) out.depart_at = read.depart_at.slice(0, 16);
  if (read.arrive_at) out.arrive_at = read.arrive_at.slice(0, 16);
  if (read.hotel_name) out.hotel_name = read.hotel_name;
  const stay = [read.check_in, read.check_out].filter(Boolean).map((d) => plainDate(d!));
  if (stay.length) out.notes = `Stay ${stay.join(' to ')}`;
  return out;
}

/** Files that can go with this booking: uploaded for someone in it, and not
 *  sent with an earlier one or thrown away. */
const sendable = (ticket: Ticket, who: number[]) =>
  who.includes(ticket.traveller_id) && (ticket.status === 'EXTRACTED' || ticket.status === 'FAILED');

/** Mount with a `key` per traveller, so each opens on its own defaults. */
export function BookingModal({
  request,
  traveller,
  onClose,
  onBooked,
}: {
  request: TravelRequest;
  /** Whose "Mark booked" was pressed. */
  traveller: RequestTraveller;
  onClose: () => void;
  onBooked: () => void;
}) {
  const queryClient = useQueryClient();
  const isCab = request.request_type === 'LOCAL_CAB';
  const isHotel = request.request_type === 'HOTEL';
  const approved = request.travellers.filter((t) => t.status === 'APPROVED');

  const [who, setWho] = useState<number[]>(() =>
    isCab ? approved.map((t) => t.id) : [traveller.id],
  );
  const [cabDraft, setCabDraft] = useState<CabDraft | null>(() =>
    isCab ? cabDraftFrom(request) : null,
  );
  const [reference, setReference] = useState('');
  const [booking, setBooking] = useState<BookingDraft>(() =>
    traveller.booking_details ? draftOfDetails(traveller.booking_details) : EMPTY_BOOKING,
  );
  const [filled, setFilled] = useState<'ticket' | 'before' | null>(null);
  // The files to send: null until the uploaded ones are known, then the ids.
  const [fileIds, setFileIds] = useState<number[] | null>(null);
  const [cost, setCost] = useState('');
  const [costFromFiles, setCostFromFiles] = useState(false);
  const [vendorPick, setVendorPick] = useState<number | '' | null>(null);
  const [note, setNote] = useState(isCab ? 'Cab booked' : isHotel ? 'Room booked' : 'Ticket booked');
  const [notify, setNotify] = useState(true);
  const [uploading, setUploading] = useState<{ done: number; of: number } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  // Fields the admin has typed into (or filled "the same as before"). What
  // the files say never overwrites them.
  const edited = useRef(new Set<string>());
  const mark = (...keys: string[]) => keys.forEach((key) => edited.current.add(key));

  // The queue list leaves cost and vendor out, so the request is read as the
  // admin; the trip it extends too, for "the same as before".
  const detail = useQuery({ queryKey: ['request', request.id], queryFn: () => fetchRequest(request.id) });
  const previous = useQuery({
    queryKey: ['request', request.extends_request_id],
    queryFn: () => fetchRequest(request.extends_request_id!),
    enabled: request.extends_request_id != null,
  });
  const tickets = useQuery({
    queryKey: ['tickets', request.id],
    queryFn: () => fetchTickets(request.id),
  });

  // The first time the uploaded files are known: send every one for the
  // people being booked.
  if (fileIds === null && tickets.data) {
    setFileIds(tickets.data.filter((t) => sendable(t, who)).map((t) => t.id));
  }

  // Every file is read when it is uploaded; this is what they say together,
  // and the fields fill from all of them, not just the first.
  const sending = [...(fileIds ?? [])].sort((a, b) => a - b);
  const combined = useQuery({
    queryKey: ['tickets-combined', request.id, sending.join(',')],
    queryFn: () => fetchCombinedTickets(request.id, sending),
    enabled: sending.length > 0,
  });
  useEffect(() => {
    const read = combined.data;
    if (!read || read.files_read === 0) return;
    const free = (key: string) => !edited.current.has(key);
    if (read.booking_reference && free('reference')) setReference(read.booking_reference);
    if (read.fare_total && free('cost')) {
      setCost(read.fare_total);
      setCostFromFiles(true);
    }
    if (!isCab) {
      setBooking((now) => {
        const next = { ...now };
        for (const [key, value] of Object.entries(fieldsOf(read, isHotel))) {
          if (value && free(key)) next[key as keyof BookingDraft] = value;
        }
        return next;
      });
    }
    setFilled('ticket');
  }, [combined.data, isCab, isHotel]);

  const people = (detail.data?.travellers ?? request.travellers).filter((t) => who.includes(t.id));
  const vendorWas = detail.data ? sharedVendor(people) : '';
  const vendor = vendorPick ?? vendorWas;
  const files = (tickets.data ?? []).filter((t) => (fileIds ?? []).includes(t.id));

  const upload = useMutation({
    mutationFn: async (picked: File[]) => {
      // Each file is read as it lands - a few seconds apiece - so up to three
      // go at once, and one that fails does not lose the others.
      const owner = who.includes(traveller.id) ? traveller.id : who[0];
      let done = 0;
      setUploading({ done, of: picked.length });
      const outcomes = await settleEach(picked, 3, async (file) => {
        try {
          return await uploadTicket(request.id, owner, file);
        } finally {
          done += 1;
          setUploading({ done, of: picked.length });
        }
      });
      return outcomes.map((outcome, index) => ({ file: picked[index], outcome }));
    },
    meta: { errorFallback: 'Could not upload the files.' },
    onSuccess: (results) => {
      const added = results.flatMap(({ outcome }) =>
        outcome.status === 'fulfilled' ? [outcome.value] : [],
      );
      queryClient.setQueryData<Ticket[]>(['tickets', request.id], (now) => [...added, ...(now ?? [])]);
      setFileIds((now) => [...(now ?? []), ...added.map((t) => t.id)]);
      for (const { file, outcome } of results) {
        if (outcome.status === 'rejected') {
          toast.error(`${file.name}: ${errorMessage(outcome.reason, 'could not be uploaded')}`);
        }
      }
      const read = added.filter((t) => t.status === 'EXTRACTED').length;
      if (added.length === 0) return;
      if (read === added.length) {
        toast.success(
          added.length === 1 ? 'Read - check the details below' : `All ${added.length} read - check the details below`,
        );
      } else {
        toast(
          read === 0
            ? `${added.length === 1 ? 'Added, but it' : `${added.length} added, but they`} could not be read - type the details below. ${added.length === 1 ? 'It is' : 'They are'} still sent.`
            : `${read} of ${added.length} read - check the details below and the files that were not.`,
        );
      }
    },
    onSettled: () => setUploading(null),
  });

  const view = useMutation({
    mutationFn: (vars: { ticket: Ticket; tab: Window | null }) =>
      showFile(vars.tab, () => fetchTicketFile(vars.ticket.id), vars.ticket.file_name ?? 'ticket'),
    meta: { errorFallback: 'Could not open the file.' },
  });

  const save = useMutation({
    mutationFn: (body: BookingBody) => bookTravellers(request.id, body),
    meta: { errorFallback: 'Could not book.' },
    onSuccess: () => {
      toast.success(
        notify
          ? `Booked - ${names(people.map((p) => p.full_name.split(' ')[0]))} ${people.length === 1 ? 'is' : 'are'} emailed`
          : 'Booked',
      );
      queryClient.invalidateQueries({ queryKey: ['request', request.id] });
      queryClient.invalidateQueries({ queryKey: ['tickets', request.id] });
      queryClient.invalidateQueries({ queryKey: ['invoices'] });
      onBooked();
    },
  });

  // --- "the same as before", for an extension ------------------------------
  const before = previous.data;
  const beforeBooked = before?.travellers.find((t) => t.status === 'BOOKED') ?? null;
  const beforeCost = before?.travellers
    .map((t) => t.cost_amount)
    .filter((amount): amount is string => amount != null);
  const useBefore = () => {
    if (!before) return;
    if (isCab && before.cab_vehicle_number) {
      setCabDraft(cabDraftFrom(before));
    } else if (beforeBooked) {
      // A hotel usually extends the same booking: its name, address and, very
      // often, its confirmation number.
      setBooking(draftOfDetails(beforeBooked.booking_details));
      mark(...Object.keys(EMPTY_BOOKING));
      if (beforeBooked.booking_reference) {
        setReference(beforeBooked.booking_reference);
        mark('reference');
      }
    }
    const paidBefore = sharedVendor(before.travellers.filter((t) => t.status === 'BOOKED'));
    if (paidBefore !== '') setVendorPick(paidBefore);
    setFilled('before');
  };

  // --- what is ready -------------------------------------------------------
  const carReady = cabDraft !== null && cabDraftComplete(cabDraft);
  const carHalfTyped = cabDraft !== null && !cabDraftEmpty(cabDraft) && !carReady;
  const referenceReady = reference.trim().length >= 2 || carReady;
  const costValid = cost.trim() === '' || MONEY.test(cost.trim());
  const split = cost.trim() && people.length > 1 ? evenSplit(cost, people.length) : null;
  const ready =
    people.length > 0 &&
    referenceReady &&
    !carHalfTyped &&
    bookingDraftValid(booking) &&
    costValid &&
    note.trim().length >= 3 &&
    !upload.isPending;

  const managers = [
    ...new Map(
      people
        .filter((p) => p.manager_id && p.manager_name && !people.some((q) => q.user_id === p.manager_id))
        .map((p) => [p.manager_id, p.manager_name!]),
    ).values(),
  ];

  const submit = () => {
    const body: BookingBody = {
      traveller_ids: who,
      booking_reference: reference.trim() || (carReady ? tidyPlate(cabDraft!.vehicle_number) : null),
      note: note.trim(),
      ticket_ids: files.map((f) => f.id),
      notify,
    };
    if (!isCab) body.booking_details = bookingBody(booking);
    if (carReady) {
      body.cab = {
        booked_cab_type: cabDraft!.booked_cab_type as CabType,
        vehicle_number: cabDraft!.vehicle_number,
        driver_name: cabDraft!.driver_name,
        driver_phone: cabDraft!.driver_phone,
      };
    }
    if (cost.trim()) body.cost_amount = cost.trim();
    if (vendor !== vendorWas) body.vendor_id = vendor === '' ? null : vendor;
    save.mutate(body);
  };

  const title =
    people.length > 1 ? `Book ${people.length} travellers` : `Book ${people[0]?.full_name ?? traveller.full_name}`;

  return (
    <Modal
      open
      onClose={onClose}
      title={title}
      description={`${itinerary(request)} · ${campaignLabel(request)}`}
      className="sm:max-w-2xl"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button loading={save.isPending} disabled={!ready} onClick={submit}>
            <TicketIcon size={14} />
            {people.length > 1 ? `Mark ${people.length} booked` : 'Mark booked'}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        {/* An extension: book it as the trip before it was, in one tap. */}
        {request.extends_request_id != null && (
          <section className="rounded-lg border border-brand/40 bg-brand-soft px-3.5 py-3">
            <div className="flex flex-wrap items-start gap-3">
              <CalendarPlus size={16} className="mt-0.5 shrink-0 text-brand-strong" />
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold text-brand-strong">
                  Extends request {request.extends_request_id}
                </p>
                <p className="mt-0.5 text-xs text-text-muted">
                  {previous.isPending
                    ? 'Reading how it was booked…'
                    : request.previous_booking
                      ? `Booked before as: ${request.previous_booking}`
                      : 'The trip it extends is not booked yet.'}
                </p>
                {beforeCost && beforeCost.length > 0 && (
                  <p className="mt-0.5 text-2xs text-text-subtle">
                    Cost before: {beforeCost.map((c) => formatMoney(c, true)).join(' + ')}
                  </p>
                )}
              </div>
              {before && (isCab ? before.cab_vehicle_number : beforeBooked) && (
                <Button size="sm" variant="secondary" onClick={useBefore}>
                  {isCab ? 'Use the same cab' : isHotel ? 'Use the same hotel' : 'Use the same booking'}
                </Button>
              )}
            </div>
            {filled === 'before' && (
              <p className="mt-2 text-2xs text-text-muted">
                Filled from request {request.extends_request_id}. Change anything that is different -
                {isCab ? ' another car or driver is fine.' : ' another hotel is fine.'}
              </p>
            )}
          </section>
        )}

        {approved.length > 1 && (
          <fieldset>
            <legend className="mb-1.5 text-xs font-medium">
              Who this booking is for
              <span className="ml-1 font-normal text-text-subtle">
                - everyone ticked gets the same details and files, in one email
              </span>
            </legend>
            <div className="flex flex-wrap gap-2">
              {approved.map((t) => {
                const on = who.includes(t.id);
                return (
                  <label
                    key={t.id}
                    className={cn(
                      'flex cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition-colors',
                      on ? 'border-primary bg-surface-sunken' : 'border-border text-text-muted',
                    )}
                  >
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 accent-[rgb(var(--primary))]"
                      checked={on}
                      onChange={(e) =>
                        setWho((now) =>
                          e.target.checked ? [...now, t.id] : now.filter((id) => id !== t.id),
                        )
                      }
                    />
                    {t.full_name}
                  </label>
                );
              })}
            </div>
          </fieldset>
        )}

        {isCab && cabDraft && (
          <section className="space-y-2">
            <h3 className="text-xs font-semibold">The car sent</h3>
            <CabBookingFields
              draft={cabDraft}
              onChange={setCabDraft}
              idPrefix="bk-cab"
              asked={cabAsked(request)}
            />
            <p className={cn('text-2xs', carHalfTyped ? 'text-danger' : 'text-text-subtle')}>
              {carHalfTyped
                ? 'Fill in all four, or clear them and add the car later from Cab details.'
                : 'The email carries the plate and the driver’s phone - what a traveller looks for at the kerb.'}
            </p>
          </section>
        )}

        <section className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-xs font-semibold">
              {isCab ? 'Files' : 'Tickets'}
              <span className="ml-1 font-normal text-text-subtle">
                - all sent with the email; PDF or photo
              </span>
            </h3>
            <input
              ref={fileInput}
              type="file"
              multiple
              accept="image/jpeg,image/png,image/webp,image/heic,application/pdf"
              className="hidden"
              onChange={(e) => {
                const picked = Array.from(e.target.files ?? []);
                if (picked.length) upload.mutate(picked);
                e.target.value = '';
              }}
            />
            <Button
              size="sm"
              variant="secondary"
              disabled={upload.isPending || who.length === 0}
              onClick={() => fileInput.current?.click()}
            >
              <Upload size={13} />
              {files.length ? 'Add more' : isCab ? 'Upload files' : 'Upload tickets'}
            </Button>
          </div>

          {uploading && (
            <p className="flex items-center gap-2 rounded-md bg-surface-sunken px-3 py-2 text-xs text-text-muted">
              <Loader2 size={13} className="animate-spin" />
              {uploading.of > 1
                ? `Reading ${uploading.of} files - ${uploading.done} done…`
                : 'Reading the file - up to 20 seconds…'}
            </p>
          )}

          {files.length > 0 && combined.data && !uploading && (
            <div className="rounded-md bg-surface-sunken px-3 py-2 text-2xs text-text-muted">
              <p className="font-medium text-text">
                {combined.data.files_read === combined.data.files
                  ? `Read ${combined.data.files === 1 ? 'the file' : `all ${combined.data.files} files`} - the details below are filled from ${combined.data.files === 1 ? 'it' : 'all of them'}.`
                  : `Read ${combined.data.files_read} of ${combined.data.files} files.`}
              </p>
              {combined.data.notes.map((line) => (
                <p key={line} className="mt-0.5">
                  {line}
                </p>
              ))}
            </div>
          )}

          {files.length === 0 && !uploading ? (
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              disabled={who.length === 0}
              className="flex w-full flex-col items-center gap-1 rounded-md border border-dashed border-border-strong bg-surface-sunken/50 px-3 py-4 text-center text-xs text-text-muted hover:border-primary hover:text-text"
            >
              <Upload size={16} />
              {isCab
                ? 'Optional: the vendor’s slip or invoice.'
                : 'Upload one or more - the details below fill in from the first one read.'}
            </button>
          ) : (
            <ul className="space-y-1.5">
              {files.map((ticket) => (
                <li
                  key={ticket.id}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-border px-3 py-2"
                >
                  <FileText size={14} className="shrink-0 text-text-subtle" />
                  <span className="min-w-0 flex-1 truncate text-xs font-medium">
                    {ticket.file_name ?? 'File'}
                  </span>
                  <Badge tone={ticket.status === 'FAILED' ? 'warning' : 'success'}>
                    {ticket.status === 'FAILED' ? 'Could not read' : 'Read'}
                  </Badge>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 px-2"
                    onClick={() => view.mutate({ ticket, tab: openFileTab() })}
                    title="Open the file"
                  >
                    <Eye size={13} />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 px-2"
                    onClick={() => setFileIds((now) => (now ?? []).filter((id) => id !== ticket.id))}
                    title="Do not send this one (it stays under Tickets)"
                    aria-label={`Do not send ${ticket.file_name ?? 'this file'}`}
                  >
                    <X size={13} />
                  </Button>
                  {ticket.mismatches.length > 0 && (
                    <p className="flex basis-full items-start gap-1.5 text-2xs text-warning">
                      <AlertTriangle size={12} className="mt-px shrink-0" />
                      {ticket.mismatches.join('; ')}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <Field
          label={isCab ? 'Booking reference' : isHotel ? 'Confirmation number' : 'PNR or ticket number'}
          htmlFor="bk-reference"
          required={!carReady}
          hint={
            isCab
              ? 'The vendor’s booking ID. Left blank, the vehicle number is used.'
              : filled === 'ticket'
                ? 'Filled from the files - check it.'
                : undefined
          }
        >
          <Input
            id="bk-reference"
            value={reference}
            onChange={(e) => {
              mark('reference');
              setReference(e.target.value);
            }}
            placeholder={isCab ? 'VND-20431' : isHotel ? 'LT-88812' : 'QK8T2M'}
          />
        </Field>

        {!isCab && (
          <section className="space-y-2">
            <h3 className="text-xs font-semibold">
              {isHotel ? 'The stay' : 'The journey'}
              <span className="ml-1 font-normal text-text-subtle">
                {filled === 'ticket'
                  ? '- filled from the files; check it'
                  : '- what the traveller needs on the day'}
              </span>
            </h3>
            <BookingFields
              draft={booking}
              hotel={isHotel}
              onChange={(next) => {
                mark(
                  ...(Object.keys(next) as (keyof BookingDraft)[]).filter((key) => next[key] !== booking[key]),
                );
                setBooking(next);
              }}
            />
          </section>
        )}

        <section className="grid gap-3 sm:grid-cols-2">
          <Field
            label={people.length > 1 ? 'Total cost (₹)' : 'Cost (₹)'}
            htmlFor="bk-cost"
            error={costValid ? undefined : 'Rupees, with up to two decimals.'}
            hint={
              split
                ? `${costFromFiles ? 'From the files. ' : ''}Split evenly: ${split.map((share) => formatMoney(share, true)).join(' + ')}.`
                : costFromFiles
                  ? 'Added up from the files - check it against the bill.'
                  : 'Optional now - it can be corrected later.'
            }
          >
            <div className="relative">
              <IndianRupee
                size={13}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-subtle"
              />
              <Input
                id="bk-cost"
                value={cost}
                onChange={(e) => {
                  mark('cost');
                  setCostFromFiles(false);
                  setCost(e.target.value);
                }}
                inputMode="decimal"
                placeholder="0.00"
                className="pl-7 tabular-nums"
              />
            </div>
          </Field>
          <Field
            label="Paid to"
            htmlFor="bk-vendor"
            hint="The travel agent, cab operator or hotel - for the vendor’s invoice."
          >
            <VendorSelect
              id="bk-vendor"
              value={vendor}
              onChange={setVendorPick}
              disabled={detail.isPending}
              emptyLabel="Not recorded"
            />
          </Field>
        </section>

        <Field
          label="Note to the traveller"
          htmlFor="bk-note"
          required
          hint="In the email, and kept in the activity log."
        >
          <Input
            id="bk-note"
            value={note}
            maxLength={500}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Report at Terminal 1 two hours early"
          />
        </Field>

        <label className="flex cursor-pointer items-start gap-2.5 rounded-md bg-surface-sunken px-3 py-2.5">
          <input
            type="checkbox"
            checked={notify}
            onChange={(e) => setNotify(e.target.checked)}
            className="mt-0.5 h-3.5 w-3.5 accent-[rgb(var(--primary))]"
          />
          <span className="text-xs">
            <span className="flex items-center gap-1.5 font-medium">
              <Mail size={13} />
              Email {people.length > 1 ? 'them' : (people[0]?.full_name ?? traveller.full_name)}
            </span>
            <span className="mt-0.5 block text-text-muted">
              {notify
                ? `One email to ${names(people.map((p) => p.full_name)) || 'nobody yet'}` +
                  ` with the booking details${files.length ? ` and ${files.length === 1 ? 'the file' : `all ${files.length} files`} attached` : ''}.` +
                  (managers.length ? ` ${names(managers)} ${managers.length === 1 ? 'is' : 'are'} on Cc.` : '')
                : 'No email. They still see it in the app, and the activity log keeps it.'}
            </span>
          </span>
        </label>
      </div>
    </Modal>
  );
}
