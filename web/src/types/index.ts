export type Role = 'SUPER_ADMIN' | 'SYSTEM_ADMIN' | 'ADMIN' | 'MANAGER' | 'GROUND_STAFF';

/** Highest first. Nobody grants or manages a role above their own. */
export const ROLE_RANK: Record<Role, number> = {
  GROUND_STAFF: 0,
  MANAGER: 1,
  ADMIN: 2,
  SYSTEM_ADMIN: 3,
  SUPER_ADMIN: 4,
};

/** Every role that runs the travel desk: approvals, bookings, costs, Team. */
export const ADMIN_ROLES: Role[] = ['ADMIN', 'SYSTEM_ADMIN', 'SUPER_ADMIN'];
/** The roles that look after accounts and settings. */
export const ACCOUNT_ROLES: Role[] = ['SYSTEM_ADMIN', 'SUPER_ADMIN'];

export function isAdminRole(role: Role | undefined | null): boolean {
  return role != null && ADMIN_ROLES.includes(role);
}
export type Designation = 'EXECUTIVE' | 'TEAM_LEAD' | 'MANAGER';
/** OTHER and UNDISCLOSED only appear on people saved before gender had to be
 *  chosen; new input is Male or Female (SELECTABLE_GENDERS). */
export type Gender = 'MALE' | 'FEMALE' | 'OTHER' | 'UNDISCLOSED';
export type ThemePreference = 'light' | 'dark' | 'system';
/** Only ACTIVE can sign in. */
export type UserStatus = 'ACTIVE' | 'DEACTIVATED' | 'LEFT' | 'DELETED';

export interface UserProfile {
  id: number;
  email: string;
  full_name: string;
  employee_code: string | null;
  role: Role;
  designation: Designation | null;
  gender: Gender;
  phone: string | null;
  base_state: string | null;
  /** The city or constituency they are based in. */
  base_location: string | null;
  department_id: number | null;
  department_name: string | null;
  /** Who they report to - ground staff in a manager's team. */
  manager_id?: number | null;
  manager_name?: string | null;
  theme_preference: ThemePreference;
  status: UserStatus;
  is_active: boolean;
  last_login_at: string | null;
  /** When they last changed or reset their password. */
  password_changed_at?: string | null;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: UserProfile;
}

export interface UserRow {
  id: number;
  email: string;
  full_name: string;
  employee_code: string | null;
  role: Role;
  designation: Designation | null;
  gender: Gender;
  phone: string | null;
  base_state: string | null;
  /** The city or constituency they are based in. */
  base_location: string | null;
  department_id: number | null;
  department_name: string | null;
  /** Who they report to - ground staff in a manager's team. */
  manager_id: number | null;
  manager_name: string | null;
  status: UserStatus;
  status_changed_at: string | null;
  /** Mirrors status === 'ACTIVE'. */
  is_active: boolean;
  /** The day they left. Starts the 90-day clock on their identity documents.
   *  Distinct from deactivation: a suspension is not a departure. */
  exited_on: string | null;
  last_login_at: string | null;
  created_at: string;
  /** False while an invite is still outstanding. */
  has_password: boolean;
  is_locked: boolean;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface InviteLink {
  detail: string;
  invite_url: string | null;
  expires_at: string | null;
  /** Whether the link reached the mail server. */
  email_sent?: boolean | null;
  /** Why it did not, in words an admin can act on. */
  email_detail?: string | null;
  /** Which kind of link it is: a first invitation or a password reset. */
  purpose?: 'INVITE' | 'PASSWORD_RESET' | null;
}

export interface TokenPreview {
  full_name: string;
  email: string;
  purpose: 'INVITE' | 'PASSWORD_RESET';
}

export interface AuditRow {
  id: number;
  actor_email: string | null;
  actor_name: string | null;
  actor_role: string | null;
  action: string;
  entity_type: string;
  entity_id: number | null;
  summary: string;
  changes: Record<string, { from: unknown; to: unknown }> | null;
  reason: string | null;
  ip_address: string | null;
  created_at: string;
}

export interface ChainVerification {
  ok: boolean;
  checked: number;
  broken_at_id: number | null;
  detail: string;
}

export const ROLE_LABELS: Record<Role, string> = {
  SUPER_ADMIN: 'Super admin',
  // No longer granted: folded into Admin. Kept so an old account still reads.
  SYSTEM_ADMIN: 'System admin (old)',
  ADMIN: 'Admin',
  MANAGER: 'Manager',
  GROUND_STAFF: 'Ground staff',
};

/** What each role can do, in a line - shown under the role pickers. */
export const ROLE_DESCRIPTIONS: Record<Role, string> = {
  GROUND_STAFF: 'Raises and tracks their own travel. Can report to a manager.',
  MANAGER:
    "Leads a team: sees their trips (never costs), asks an admin to add, edit or remove members, and runs campaigns.",
  ADMIN:
    'Runs the travel desk: approvals, bookings, costs, people, departments and the activity log. Keeps vendors and creates and edits vendor invoices.',
  SYSTEM_ADMIN: 'No longer used - the same as Admin. Choose Admin or Super admin.',
  SUPER_ADMIN:
    'The top level: everything an admin sees, plus approving vendor invoices, purging old identity documents and managing other super admins. Reads, but does not create or edit, invoices and vendors.',
};

/** A manager's ask to change their team, held until an admin decides. */
export type TeamChangeKind = 'ADD' | 'EDIT' | 'REMOVE';
export type TeamChangeStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'CANCELLED';

export interface TeamChange {
  id: number;
  kind: TeamChangeKind;
  status: TeamChangeStatus;
  requested_by_id: number;
  requested_by_name: string | null;
  target_user_id: number | null;
  target_name: string | null;
  /** ADD: the new person's details. EDIT: { field: { from, to } }.
   *  REMOVE: { status, exited_on, reason }. */
  payload: Record<string, unknown>;
  note: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
  decision_comment: string | null;
  created_at: string;
}

export interface TeamChangeList {
  items: TeamChange[];
  pending: number;
}

export interface TeamDecision {
  change: TeamChange;
  invite: InviteLink | null;
}

export const DESIGNATION_LABELS: Record<Designation, string> = {
  EXECUTIVE: 'Executive',
  TEAM_LEAD: 'Team lead',
  MANAGER: 'Manager',
};

export const GENDER_LABELS: Record<Gender, string> = {
  MALE: 'Male',
  FEMALE: 'Female',
  OTHER: 'Other (old value)',
  UNDISCLOSED: 'Not set',
};

/** What a form or import can record. */
export const SELECTABLE_GENDERS = ['MALE', 'FEMALE'] as const satisfies readonly Gender[];

export const USER_STATUS_LABELS: Record<UserStatus, string> = {
  ACTIVE: 'Active',
  DEACTIVATED: 'Deactivated',
  LEFT: 'Left',
  DELETED: 'Deleted',
};

/** One line each, true to what the server does. */
export const USER_STATUS_HELP: Record<UserStatus, string> = {
  ACTIVE: 'Can sign in and be added to trips.',
  DEACTIVATED: 'Cannot sign in. For leave or a suspension; reactivate any time.',
  LEFT: 'Cannot sign in. Their ID documents are deleted 90 days after the exit date.',
  DELETED: 'Cannot sign in and is hidden from the team list. Travel history is kept; can be restored.',
};

export interface Department {
  id: number;
  name: string;
  /** People in it, not counting deleted accounts. */
  member_count: number;
}

/** Trips someone is on that are still live and not over yet - what to look at
 *  before switching their account off. */
export interface OpenTrips {
  pending: number;
  approved: number;
  booked: number;
  total: number;
}

/** What anyone may change about themselves without their password. */
export interface ProfileUpdate {
  full_name?: string;
  phone?: string | null;
}

/** A password change signs out every other device, this one included; the
 *  fresh token keeps this one signed in. */
export interface PasswordChanged {
  detail: string;
  access_token: string;
  token_type: string;
  expires_at: string;
}

// --- Phase 2 ---------------------------------------------------------------

export type ProjectStatus = 'ACTIVE' | 'PAUSED' | 'COMPLETED' | 'ARCHIVED';

export type IdProofType =
  | 'AADHAAR'
  | 'PAN'
  | 'PASSPORT'
  | 'DRIVING_LICENCE'
  | 'VOTER_ID'
  | 'OTHER';

export interface Project {
  id: number;
  name: string;
  code: string;
  description: string | null;
  client_name: string | null;
  state: string | null;
  /** City or assembly constituency. */
  city: string | null;
  /** Free text from before state/city; shown until a state is picked. */
  location: string | null;
  status: ProjectStatus;
  start_date: string | null;
  end_date: string | null;
  created_at: string;
  /** Whether this campaign still appears in the request dropdowns. */
  accepts_requests: boolean;
  /** Requests raised against it, of any status. Only one with none can be
   *  deleted. */
  request_count: number;
  /** The built-in "Other / not yet listed" campaign the request form needs.
   *  It cannot be archived, deleted, recoded or paused. */
  is_fallback: boolean;
}

export interface IdProof {
  id: number;
  user_id: number;
  proof_type: IdProofType;
  label: string | null;
  /** e.g. "XXXX XXXX 2109". The real number never arrives in a list. */
  masked_number: string | null;
  issued_on: string | null;
  expires_on: string | null;
  is_expired: boolean;
  has_file: boolean;
  file_name: string | null;
  file_size: number | null;
  is_purged: boolean;
  purged_at: string | null;
  created_at: string;
}

export interface RetentionStatus {
  retention_days: number;
  cutoff: string;
  due_now: number;
}

export interface ImportRow {
  line: number;
  full_name: string | null;
  email: string | null;
  role: Role;
  designation: Designation | null;
  /** Null when the row is missing one, which is an error. */
  gender: Gender | null;
  phone: string | null;
  employee_code: string | null;
  /** A department name; a new one is created on import. */
  department: string | null;
  base_state: string | null;
  base_location: string | null;
  errors: string[];
  warnings: string[];
}

export interface ImportPreview {
  rows: ImportRow[];
  total: number;
  importable: number;
  skipped: number;
  file_errors: string[];
}

export interface ImportResult {
  created: number;
  skipped: number;
  invite_urls: Record<string, string>;
  errors: string[];
  /** True when the invites are being emailed in the background. */
  emailing?: boolean;
  email_detail?: string | null;
}

export const PROJECT_STATUS_LABELS: Record<ProjectStatus, string> = {
  ACTIVE: 'Active',
  PAUSED: 'Paused',
  COMPLETED: 'Completed',
  ARCHIVED: 'Archived',
};

/** What each status does, in the words shown under the status picker. */
export const PROJECT_STATUS_HELP: Record<ProjectStatus, string> = {
  ACTIVE: 'Open: staff can pick it when they raise or edit a travel request.',
  PAUSED:
    'On hold: hidden from the request form, so no new requests or edits. Trips already raised can still be approved and booked.',
  COMPLETED:
    'Finished: no new requests, like Paused, but marks the work as done. Its trips and costs stay in every report.',
  ARCHIVED:
    'Put away: no new requests, hidden from staff and greyed out here. Nothing is deleted; Restore reopens it.',
};

export const ID_PROOF_LABELS: Record<IdProofType, string> = {
  AADHAAR: 'Aadhaar',
  PAN: 'PAN',
  PASSPORT: 'Passport',
  DRIVING_LICENCE: 'Driving licence',
  VOTER_ID: 'Voter ID',
  OTHER: 'Other',
};

// --- Phase 3 ---------------------------------------------------------------

export type RequestType = 'LONG_DISTANCE' | 'LOCAL_CAB' | 'HOTEL';
export type TravelMode = 'FLIGHT' | 'TRAIN' | 'BUS' | 'CAB';

export type RequestStatus =
  | 'DRAFT'
  | 'SUBMITTED'
  | 'PARTIALLY_APPROVED'
  | 'APPROVED'
  | 'BOOKED'
  | 'REJECTED'
  | 'CANCELLED'
  | 'EXPIRED';

export type TravellerStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'BOOKED' | 'CANCELLED';

export type RoomSharingChoice =
  | 'NOT_OFFERED'
  | 'SHARE_EXISTING'
  | 'SEPARATE_ROOM'
  | 'SEPARATE_HOTEL';

export type ConflictKind = 'OVERLAPPING_TRAVEL' | 'OVERLAPPING_STAY' | 'DUPLICATE_REQUEST';

/** The car a cab asks for, and the one an admin records as sent. Named by
 *  size on the server; the labels carry the model staff know. */
export type CabType = 'NO_PREFERENCE' | 'SEDAN' | 'SUV';

export const CAB_TYPE_LABELS: Record<CabType, string> = {
  NO_PREFERENCE: 'No preference',
  SEDAN: 'Dzire (4 seats)',
  SUV: 'Ertiga (7 seats)',
};

/** The choice cards on the request form: the model, then its seats. */
export const CAB_TYPE_CHOICES: { value: CabType; title: string; detail: string }[] = [
  { value: 'NO_PREFERENCE', title: 'No preference', detail: 'Any car the vendor has' },
  { value: 'SEDAN', title: 'Dzire', detail: '4 seats' },
  { value: 'SUV', title: 'Ertiga', detail: '7 seats' },
];

/** The cars an admin can record as sent - never "no preference". */
export const BOOKED_CAB_TYPES: CabType[] = ['SEDAN', 'SUV'];

export type CabTrip = 'LOCAL' | 'OUTSTATION';

/** Mirrors LOCAL_CAB_MAX_KM on the server, which holds the rule and checks
 *  every save; this only words the form. */
export const LOCAL_CAB_MAX_KM = 80;

export const CAB_TRIP_LABELS: Record<CabTrip, string> = {
  LOCAL: 'Local',
  OUTSTATION: 'Outstation',
};

export type CabExtensionStatus = 'PENDING' | 'APPROVED' | 'REJECTED';

/** A manager's advice on a team member's trip. The admin decides. */
export type ManagerRecommendation = 'RECOMMENDED' | 'NOT_RECOMMENDED';

export const RECOMMENDATION_LABELS: Record<ManagerRecommendation, string> = {
  RECOMMENDED: 'Recommended',
  NOT_RECOMMENDED: 'Not recommended',
};

/** The two-level approval list filter: still waiting on a manager, or
 *  already given their view. For a manager, their own team only. */
export type ReviewFilter = 'waiting' | 'reviewed';

export interface RequestTraveller {
  id: number;
  user_id: number;
  full_name: string;
  email: string;
  designation: Designation | null;
  status: TravellerStatus;
  is_requester: boolean;
  room_sharing: RoomSharingChoice;
  share_with_user_id: number | null;
  share_with_name: string | null;
  /** A share is only real once an admin has signed it off — addendum C2. */
  share_confirmed: boolean;

  /** The admin decision, once one has been taken (Phase 4). */
  decided_by_name: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  /** PNR, ticket number or hotel confirmation. */
  booking_reference: string | null;
  /** Airline, number, times, seat or hotel - see BookingDetails. */
  booking_details: BookingDetails | null;
  /** On the admin queue list only: the uploaded ticket to open from this row - the
   *  confirmed one, else the newest still under review. */
  ticket_id?: number | null;
  /** The traveller's confirmed ticket can be downloaded from My requests. */
  ticket_ready?: boolean;
  /** Every file on this person's booking - a booking can carry several. An
   *  admin also sees files still under review; the traveller and whoever
   *  raised the trip see the confirmed ones, which they can download. */
  ticket_files?: TicketFile[];
  /** Admins only, on a hotel stay: colleagues of the same gender staying in the
   *  same city on overlapping nights - who could be put in one room. */
  room_matches?: CoStayMatch[];

  /** Two-level approval. Who this traveller reports to, if that manager is
   *  active - shown to anyone who can see the request. */
  manager_id: number | null;
  manager_name: string | null;
  /** What the manager said. Only an admin, or this traveller's own manager,
   *  is sent it; everyone else - the traveller included - gets null. */
  manager_recommendation: ManagerRecommendation | null;
  manager_comment: string | null;
  manager_reviewed_at: string | null;
  manager_reviewed_by_name: string | null;

  /** This person's share of the cost. Admin-only — null for ground staff
   *  however they reach the request. Amounts are strings; see the note on
   *  Overview. */
  cost_amount: string | null;
  cost_currency: string | null;
  cost_note: string | null;
  cost_entered_by_name: string | null;
  /** Who was paid for this person's trip, and the invoice it is billed on.
   *  Admin-only like cost. A cost on an approved invoice is locked. */
  vendor_id?: number | null;
  vendor_name?: string | null;
  invoice_id?: number | null;
  invoice_number?: string | null;
  invoice_status?: InvoiceStatus | null;
}

/** One file on a traveller's booking. */
export interface TicketFile {
  id: number;
  file_name: string | null;
  /** Sent with the booking, so the traveller can download it. */
  confirmed: boolean;
}

export interface RequestConflict {
  user_id: number;
  user_name: string;
  kind: ConflictKind;
  /** Always WARNING in V1. Conflicts never block a submission — addendum B6. */
  severity: 'WARNING' | 'BLOCKING';
  message: string;
  other_request_id: number | null;
  other_request_type: RequestType | null;
  other_summary: string | null;
}

export interface CoStayMatch {
  user_id: number;
  full_name: string;
  designation: Designation | null;
  request_id: number;
  hotel_city: string;
  check_in: string;
  check_out: string | null;
  overlapping_nights: number;
  status: string;
}

export interface RequestRevision {
  revision_number: number;
  editor_name: string | null;
  created_at: string;
  summary: string;
  changes: Record<string, { from: unknown; to: unknown }> | null;
}

export interface TravelRequest {
  id: number;
  request_type: RequestType;
  status: RequestStatus;
  /** False once any admin has decided on any traveller — addendum A1. */
  is_editable: boolean;
  is_draft: boolean;
  is_cancelled: boolean;
  cancel_reason: string | null;

  project_id: number;
  project_name: string;
  project_code: string;

  requester_id: number;
  requester_name: string;

  mode: TravelMode | null;
  origin: string | null;
  destination: string | null;
  /** The state each place sits in, captured when it was picked. Null on rows
   *  written before places were picked from a list. */
  origin_state: string | null;
  destination_state: string | null;
  hotel_state: string | null;
  /** A cab's city or constituency, beside the street address in origin /
   *  destination. Null on cabs raised before it was asked for. */
  pickup_city: string | null;
  drop_city: string | null;
  start_at: string | null;
  end_at: string | null;

  hotel_city: string | null;
  check_in: string | null;
  check_out: string | null;

  /** A cab's size, local or outstation, and outstation's rough distance.
   *  Null on flights and hotels. */
  cab_type: CabType | null;
  cab_trip: CabTrip | null;
  cab_distance_km: number | null;
  /** The car an admin recorded as sent. Null until they have. */
  booked_cab_type: CabType | null;
  cab_vehicle_number: string | null;
  cab_driver_name: string | null;
  cab_driver_phone: string | null;
  cab_booked_by_name: string | null;
  cab_booked_at: string | null;
  /** The latest ask to keep the cab one more day, and its answer. */
  cab_extension_status: CabExtensionStatus | null;
  cab_extension_reason: string | null;
  cab_extension_requested_by_name: string | null;
  cab_extension_requested_at: string | null;
  cab_extension_decided_by_name: string | null;
  cab_extension_decided_at: string | null;
  cab_extension_comment: string | null;
  /** Extra days approved so far; end_at already includes them. */
  cab_extended_days: number;
  /** An ask to cancel a trip already approved or booked, and its answer. */
  cancellation_status: 'PENDING' | 'APPROVED' | 'REJECTED' | null;
  cancellation_reason: string | null;
  cancellation_requested_by_name: string | null;
  cancellation_requested_at: string | null;
  cancellation_decided_by_name: string | null;
  cancellation_decided_at: string | null;
  cancellation_comment: string | null;
  /** The reader's Cancel becomes an ask (someone on it is approved or booked). */
  cancel_needs_approval: boolean;
  /** The reader may approve or reject the pending ask. */
  can_decide_cancellation: boolean;
  cancelled_by_name: string | null;
  /** The trip this one carries on, when it is an extension - a cab kept
   *  longer, a stay made longer. */
  extends_request_id: number | null;
  /** How that trip was booked (the car and driver, or the hotel), so this one
   *  can be booked the same way. */
  previous_booking: string | null;
  /** The live extension carrying this trip on, if any. */
  extended_by_request_id: number | null;
  /** Whether the person reading may extend this trip now - the server's
   *  rule, so the button only shows when the ask would be accepted. */
  can_extend: boolean;
  /** While they may: the trip's last day. Extending is open until midnight
   *  that day (India time); after it, extra days are a new request. */
  extend_until: string | null;

  /** Why the trip is happening. Mandatory on anything raised from now on;
   *  null on requests that predate the field. */
  travel_reason: string | null;
  /** Set when the requester picked "Other" and typed a campaign name. */
  other_project_name: string | null;
  /** How soon the requester needs a decision. */
  priority: RequestPriority;
  notes: string | null;
  submitted_at: string | null;
  created_at: string;
  updated_at: string;

  travellers: RequestTraveller[];
  /** Amendments since submission. The admin queue shows this as "edited N times". */
  edit_count: number;
  /** True once every traveller has been decided one way or the other. */
  is_decided: boolean;
  conflicts: RequestConflict[];
  costay_matches: CoStayMatch[];
}

export interface AppNotification {
  id: number;
  kind: string;
  title: string;
  body: string;
  request_id: number | null;
  created_at: string;
  read_at: string | null;
}

export const REQUEST_TYPE_LABELS: Record<RequestType, string> = {
  LONG_DISTANCE: 'Flight, train or bus',
  LOCAL_CAB: 'Local cab',
  HOTEL: 'Hotel',
};

export const TRAVEL_MODE_LABELS: Record<TravelMode, string> = {
  FLIGHT: 'Flight',
  TRAIN: 'Train',
  BUS: 'Bus',
  CAB: 'Cab',
};

export type RequestPriority = 'HIGH' | 'MEDIUM' | 'LOW';

export const PRIORITY_LABELS: Record<RequestPriority, string> = {
  HIGH: 'High',
  MEDIUM: 'Medium',
  LOW: 'Low',
};

/** High first: the order admins work through them. */
export const PRIORITY_ORDER: RequestPriority[] = ['HIGH', 'MEDIUM', 'LOW'];

export const REQUEST_STATUS_LABELS: Record<RequestStatus, string> = {
  DRAFT: 'Draft',
  SUBMITTED: 'Submitted',
  PARTIALLY_APPROVED: 'Partly approved',
  APPROVED: 'Approved',
  BOOKED: 'Booked',
  REJECTED: 'Rejected',
  CANCELLED: 'Cancelled',
  EXPIRED: 'Expired',
};

export const TRAVELLER_STATUS_LABELS: Record<TravellerStatus, string> = {
  PENDING: 'Pending',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
  BOOKED: 'Booked',
  CANCELLED: 'Cancelled',
};

export const ROOM_SHARING_LABELS: Record<RoomSharingChoice, string> = {
  NOT_OFFERED: 'Not offered',
  SHARE_EXISTING: 'Share a room',
  SEPARATE_ROOM: 'Separate room',
  SEPARATE_HOTEL: 'Separate hotel',
};

export interface Colleague {
  id: number;
  full_name: string;
  designation: Designation | null;
}

// --- Phase 4 ---------------------------------------------------------------

export interface QueueCounts {
  awaiting: number;
  partially_approved: number;
  approved: number;
  booked: number;
  rejected: number;
  cancelled: number;
  expired: number;
  with_conflicts: number;
  edited: number;
  /** High-priority requests still waiting on a decision. */
  high_priority: number;
  high_priority_awaiting: number;
  high_priority_partial: number;
  /** Still waiting on an admin, with someone whose manager has not
   *  recommended yet. For a manager: their own team only. */
  awaiting_manager: number;
  /** Cabs whose travellers asked to keep them one more day, still waiting on
   *  an admin. */
  cab_extensions: number;
  /** Decided trips whose requester asked to cancel, waiting on an answer. */
  cancellations: number;
  /** Extensions - a cab kept longer, a stay made longer - still waiting on an
   *  admin. The extra day is usually tomorrow. */
  extensions: number;
}

/** Every request in one admin queue tab, read as the admin (so with cost), for
 *  the CSV. Capped on the server; `truncated` says when the cap was hit. */
export interface QueueExport {
  status: RequestStatus;
  total: number;
  truncated: boolean;
  items: TravelRequest[];
}

/** What a traveller needs on the day, beside the booking reference. Times are
 *  India wall-clock ("2026-10-14T06:10:00"), like a trip's start. */
export interface BookingDetails {
  carrier?: string | null;
  service_number?: string | null;
  depart_at?: string | null;
  arrive_at?: string | null;
  seat?: string | null;
  hotel_name?: string | null;
  hotel_address?: string | null;
  notes?: string | null;
}

interface DecisionBody {
  to_status: TravellerStatus;
  reason?: string | null;
  booking_reference?: string | null;
  /** Only when marking booked. */
  booking_details?: BookingDetails | null;
  /** Only when marking booked: the uploaded ticket the booking is from. It is
   *  confirmed with the booking and attached to the traveller's email. */
  ticket_id?: number | null;
  /** Mandatory when approving someone with a live clash — addendum B6. */
  conflict_override_reason?: string | null;
  /** Suppresses the email only. The in-app notice and the ledger entry are
   *  written regardless — the record is not optional, only the email is. */
  notify_employee?: boolean;
}

export interface BatchDecisionItem extends DecisionBody {
  traveller_id: number;
}

// --- Phase 5 ---------------------------------------------------------------

export type TicketStatus =
  | 'UPLOADED'
  | 'EXTRACTING'
  | 'EXTRACTED'
  | 'CONFIRMED'
  | 'FAILED'
  | 'DISCARDED';

export type NotificationChannel = 'EMAIL' | 'IN_APP';
export type NotificationStatus = 'QUEUED' | 'SENT' | 'FAILED' | 'READ' | 'SUPPRESSED';

/** Below this the review screen highlights a field. Mirrors REVIEW_THRESHOLD
 *  in `app/services/extraction.py`. */
export const REVIEW_THRESHOLD = 0.75;

export interface Ticket {
  id: number;
  request_id: number;
  traveller_id: number;
  traveller_name: string;
  status: TicketStatus;

  file_name: string | null;
  file_size: number | null;
  content_type: string | null;
  uploaded_by_name: string | null;
  created_at: string;

  booking_reference: string | null;
  carrier: string | null;
  service_number: string | null;
  passenger_name: string | null;
  origin: string | null;
  destination: string | null;
  depart_at: string | null;
  arrive_at: string | null;
  hotel_name: string | null;
  check_in: string | null;
  check_out: string | null;
  /** The fare printed on the document - a proposal, like every extracted
   *  value. Pre-fills the cost when booking. */
  fare_amount: string | null;
  fare_currency: string | null;

  confidence: Record<string, number> | null;
  /** Fields the model was unsure of — read these before confirming. */
  needs_review: string[];
  model_id: string | null;
  extraction_error: string | null;
  extracted_at: string | null;

  confirmed_by_name: string | null;
  confirmed_at: string | null;
  confirmed_reference: string | null;

  /** Where the ticket disagrees with what was asked for. Advisory. */
  mismatches: string[];
}

/** Several uploaded files read together, as one proposal for the booking
 *  window. Like every extracted value it is checked before it is saved. */
export interface CombinedTickets {
  files: number;
  files_read: number;
  booking_reference: string | null;
  carrier: string | null;
  service_number: string | null;
  depart_at: string | null;
  arrive_at: string | null;
  hotel_name: string | null;
  check_in: string | null;
  check_out: string | null;
  /** The total across the files, each booking reference counted once. */
  fare_total: string | null;
  fare_currency: string | null;
  /** What to look at before saving: two references, a file not read. */
  notes: string[];
}

export interface LedgerRow {
  id: number;
  user_id: number;
  user_name: string | null;
  kind: string;
  title: string;
  body: string;
  channel: NotificationChannel;
  status: NotificationStatus;
  to_address: string | null;
  /** Who the email was copied to, comma separated - a traveller's manager on
   *  a decision. */
  cc_addresses: string | null;
  subject: string | null;
  attempts: number;
  sent_at: string | null;
  last_error: string | null;
  request_id: number | null;
  /** In-app only. Nothing here can know whether an email was opened. */
  read_at: string | null;
  category: NotificationCategory;
  created_at: string;
}

export interface NotificationLedger {
  items: LedgerRow[];
  total: number;
  page: number;
  page_size: number;
  summary: { total: number; emails: number; by_status: Record<string, number> };
}

export const TICKET_STATUS_LABELS: Record<TicketStatus, string> = {
  UPLOADED: 'Uploaded',
  EXTRACTING: 'Reading',
  EXTRACTED: 'Awaiting review',
  CONFIRMED: 'Confirmed',
  FAILED: 'Could not read',
  DISCARDED: 'Discarded',
};

/** The extracted fields, in the order a reviewer reads them. */
export const TICKET_FIELD_LABELS: Record<string, string> = {
  booking_reference: 'Reference / PNR',
  carrier: 'Operator',
  service_number: 'Service',
  passenger_name: 'Passenger',
  origin: 'From',
  destination: 'To',
  depart_at: 'Departs',
  arrive_at: 'Arrives',
  hotel_name: 'Hotel',
  check_in: 'Check in',
  check_out: 'Check out',
};

// --- Phase 6 ---------------------------------------------------------------

export type NotificationCategory =
  | 'DECISIONS'
  | 'BOOKINGS'
  | 'ROOM_SHARING'
  | 'REMINDERS'
  | 'NEW_REQUESTS';

export interface NotificationPreferences {
  /** One entry per switchable category. DECISIONS is absent on purpose — being
   *  told what happened to your own travel is not a subscription. */
  email: Record<string, boolean>;
  unread: number;
}

export interface JobResult {
  job: string;
  notified?: number | null;
  considered?: number | null;
  stale?: number | null;
  attempted?: number | null;
  sent?: number | null;
  still_failing?: number | null;
  error?: string | null;
}

export interface SchedulerStatus {
  enabled: boolean;
  running: boolean;
  interval_minutes: number;
  travel_reminder_days: number;
  stale_after_days: number;
  failed_email: number;
}

export const CATEGORY_LABELS: Record<NotificationCategory, string> = {
  DECISIONS: 'Approvals and rejections',
  BOOKINGS: 'Tickets and confirmations',
  ROOM_SHARING: 'Room sharing requests',
  REMINDERS: 'Reminders and nudges',
  NEW_REQUESTS: 'New requests to approve',
};

export const CATEGORY_HINTS: Record<NotificationCategory, string> = {
  DECISIONS: 'Always on — this is how you find out what happened to your travel.',
  BOOKINGS: 'Your ticket reference once an admin has confirmed it.',
  ROOM_SHARING: 'When a colleague asks to share your room.',
  REMINDERS: 'A nudge shortly before a trip you are booked on.',
  NEW_REQUESTS:
    'An email when a request needs your answer: a new one to decide, a manager’s recommendation, or your team’s request to recommend.',
};

/** The .env file the API reads. Key names only - never values. */
export interface EmailEnvFile {
  path: string;
  exists: boolean;
  encoding: string | null;
  modified_at: string | null;
  keys: string[];
  /** Keys the app does not read, mapped to the setting it probably meant. */
  unknown_keys: Record<string, string | null>;
}

/** The email settings the running API is using. */
export interface EmailSettings {
  enabled: boolean;
  host: string;
  port: number;
  security: 'SSL' | 'STARTTLS' | string;
  username: string | null;
  /** "set (16 characters)" or "not set" - the value itself never leaves the server. */
  password: string;
  password_looks_wrong: boolean;
  from_address: string | null;
  from_name: string;
  allowlist: string[];
  links_point_to: string;
  env_file: EmailEnvFile;
  /** Settings coming from real environment variables, which win over the file. */
  from_environment: string[];
  started_at: string;
  /** The file was saved after the API started, so it is not in effect yet. */
  restart_needed: boolean;
}

export interface EmailStatus {
  problem: string | null;
  settings: EmailSettings;
}

export interface EmailTestResult {
  ok: boolean;
  to: string;
  /** How far it got. */
  stage: 'config' | 'connect' | 'login' | 'send' | 'done' | string;
  error: string | null;
  hint: string | null;
  /** False when EMAIL_ALLOWLIST holds back ordinary notices to this address. */
  allowlisted: boolean;
  settings: EmailSettings;
}

export const NOTIFICATION_STATUS_LABELS: Record<NotificationStatus, string> = {
  QUEUED: 'Queued',
  SENT: 'Sent',
  FAILED: 'Failed',
  READ: 'Read',
  SUPPRESSED: 'Not sent',
};

export const JOB_LABELS: Record<string, string> = {
  remind_travellers: 'Travel reminders',
  remind_admins_of_stale_requests: 'Stale request nudges',
  retry_undelivered: 'Email retries',
};

// --- Phase 7 ---------------------------------------------------------------

/** Amounts cross the wire as strings and are parsed only for display. A JSON
 *  number is a float, and a float is how a report starts disagreeing with an
 *  invoice. */
export interface Overview {
  spent: string;
  committed: string;
  average_per_traveller: string;
  booked_travellers: number;
  pending_travellers: number;
  /** Booked rows with no cost recorded — the honesty check on every other figure. */
  uncosted: number;
  people_travelling: number;
  trips: number;
  currency: string;
}

export interface CampaignSpend {
  project_id: number;
  code: string;
  name: string;
  status: string;
  spent: string;
  committed: string;
  trips: number;
  travellers: number;
  uncosted: number;
}

export interface TypeSpend {
  request_type: RequestType;
  spent: string;
  travellers: number;
}

/** One bucket of booked spend: "2026-10" for a month, or a day (a week's
 *  Monday) as "2026-10-05". */
export interface TrendPoint {
  period: string;
  spent: string;
  travellers: number;
}

export interface PersonSpend {
  user_id: number;
  full_name: string;
  employee_code: string | null;
  spent: string;
  committed: string;
  trips: number;
  uncosted: number;
}

/** Spend by destination state or city. `state`/`city` are null on the
 *  "not recorded" row, which is not a filter. */
export interface PlaceSpend {
  label: string;
  state: string | null;
  city: string | null;
  spent: string;
  travellers: number;
  trips: number;
  uncosted: number;
}

export interface DeploymentRow {
  location: string;
  people: number;
  trips: number;
}

export interface UncostedRow {
  traveller_id: number;
  request_id: number;
  traveller_name: string;
  project_code: string;
  request_type: RequestType;
  booking_reference: string | null;
  trip_date: string | null;
}

export interface AnalyticsBundle {
  /** The span the trend covers: the chosen dates, or the data's own span
   *  where a side was left open. */
  since: string;
  until: string;
  grain: 'day' | 'week' | 'month';
  overview: Overview;
  trend: TrendPoint[];
  by_campaign: CampaignSpend[];
  by_type: TypeSpend[];
  by_person: PersonSpend[];
  by_state: PlaceSpend[];
  by_city: PlaceSpend[];
  deployment: DeploymentRow[];
  /** Distinct people across `deployment`; its rows overlap. */
  deployed_people: number;
  uncosted: UncostedRow[];
}

export interface CostPreviewRow {
  traveller_id: number;
  traveller_name: string;
  amount: string;
}

export interface CostPreview {
  total_amount: string;
  rows: CostPreviewRow[];
  /** Proof the apportionment is exact. Shown so the odd paisa never looks like a bug. */
  sums_to_total: boolean;
}

// --- Travel history (SOW §5) ----------------------------------------------

export interface HistoryCompanion {
  user_id: number;
  full_name: string;
  designation: Designation | null;
  status: TravellerStatus;
}

export interface TravelMovement {
  request_id: number;
  request_type: RequestType;
  mode: TravelMode | null;
  status: TravellerStatus;
  /** One readable line: "Hyderabad → Indore", or the hotel city. */
  where: string;
  origin: string | null;
  destination: string | null;
  hotel_city: string | null;
  started_on: string | null;
  start_at: string | null;
  end_at: string | null;
  check_in: string | null;
  check_out: string | null;
  nights: number | null;
  project_id: number;
  project_name: string | null;
  project_code: string | null;
  booking_reference: string | null;
  /** Everyone else on the same movement — the "cab companions" of §5. */
  companions: HistoryCompanion[];
  room_sharing: RoomSharingChoice;
  share_with_name: string | null;
  share_confirmed: boolean;
  /** Admin-only; null when a user reads their own timeline. */
  cost_amount: string | null;
  cost_currency: string | null;
}

export interface TravelHistory {
  user_id: number;
  full_name: string;
  email: string;
  designation: Designation | null;
  since: string;
  until: string | null;
  entries: TravelMovement[];
  summary: {
    movements: number;
    nights_away: number;
    cities: number;
    travelled_with: number;
    by_type: Record<string, number>;
  };
}

// --- travel logs and the dashboard ------------------------------------------

/** The slice every report takes. State and city are the destination: where
 *  the trip goes (a hotel's own state and city), never where it starts. */
export interface InsightFilters {
  since?: string;
  until?: string;
  user_id?: number;
  project_id?: number;
  /** The traveller's department as it is now; 0 is "no department". */
  department_id?: number;
  request_type?: RequestType;
  state?: string;
  city?: string;
}

export interface TravelLogEntry {
  traveller_id: number;
  request_id: number;
  user_id: number;
  full_name: string;
  employee_code: string | null;
  designation: Designation | null;
  request_type: RequestType;
  mode: TravelMode | null;
  status: TravellerStatus;
  where: string;
  origin: string | null;
  origin_state: string | null;
  destination: string | null;
  destination_state: string | null;
  pickup_city: string | null;
  drop_city: string | null;
  hotel_city: string | null;
  hotel_state: string | null;
  started_on: string | null;
  start_at: string | null;
  end_at: string | null;
  check_in: string | null;
  check_out: string | null;
  nights: number | null;
  project_id: number;
  project_code: string | null;
  project_name: string | null;
  travel_reason: string | null;
  priority: RequestPriority;
  booking_reference: string | null;
  companions: string[];
  cost_amount: string | null;
}

export interface TravelLog {
  since: string | null;
  until: string | null;
  total: number;
  page: number;
  page_size: number;
  pages: number;
  /** More matches than this page holds. On an export, more than the cap. */
  truncated: boolean;
  summary: {
    movements: number;
    people: number;
    requests: number;
    nights: number;
    places: number;
    spent: string | null;
  };
  entries: TravelLogEntry[];
}

export interface CountRow {
  label: string;
  count: number;
}

export interface Insights {
  since: string;
  until: string;
  grain: 'day' | 'week' | 'month';
  currency: string;
  kpis: {
    movements: number;
    requests: number;
    people: number;
    nights: number;
    pending: number;
    approved: number;
    booked: number;
    rejected: number;
    cancelled: number;
    spent: string;
    committed: string;
    uncosted: number;
    average_per_booking: string;
    /** Travelled movements with no destination state on record. */
    unstated: number;
  };
  /** Requests in this slice still waiting on a decision (status Submitted),
   *  plus partly-approved ones with someone still pending. */
  awaiting: { requests: number; people: number; partly_approved: number; with_conflicts: number };
  trend: { period: string; movements: number; people: number; spent: string }[];
  by_status: { status: TravellerStatus; count: number }[];
  by_type: { request_type: RequestType; count: number; spent: string }[];
  by_mode: CountRow[];
  top_states: CountRow[];
  top_places: CountRow[];
  by_campaign: {
    project_id: number;
    code: string;
    name: string;
    count: number;
    people: number;
    spent: string;
  }[];
  top_travellers: {
    user_id: number;
    full_name: string;
    count: number;
    nights: number;
    spent: string;
  }[];
  /** Every department that travelled in this slice, busiest first, with
   *  "No department" (id 0) last. Trips are travelled movements. */
  by_department: {
    department_id: number;
    name: string;
    count: number;
    people: number;
    nights: number;
    pending: number;
    spent: string;
  }[];
}

export interface FilterOptions {
  projects: { id: number; code: string; name: string; status: ProjectStatus }[];
  departments: { id: number; name: string }[];
  people: {
    id: number;
    full_name: string;
    employee_code: string | null;
    is_active: boolean;
    status: UserStatus;
    department_id: number | null;
  }[];
  /** Destination states in use. */
  states: string[];
  /** Destination cities in use, with the state each is in. */
  cities: { state: string | null; city: string }[];
}

// --- vendors and invoices (vendor reconciliation) ---------------------------

export type VendorKind = 'TRAVEL_AGENT' | 'CAB' | 'HOTEL' | 'OTHER';

export const VENDOR_KINDS: VendorKind[] = ['TRAVEL_AGENT', 'CAB', 'HOTEL', 'OTHER'];

export const VENDOR_KIND_LABELS: Record<VendorKind, string> = {
  TRAVEL_AGENT: 'Travel agent',
  CAB: 'Cab operator',
  HOTEL: 'Hotel',
  OTHER: 'Other',
};

/** Someone the organisation pays for travel. Switched off, never deleted. */
export interface Vendor {
  id: number;
  name: string;
  kind: VendorKind;
  contact_name: string | null;
  phone: string | null;
  email: string | null;
  gstin: string | null;
  notes: string | null;
  is_active: boolean;
  created_at: string;
  /** Trips whose cost was paid to them, and invoices raised against them. */
  traveller_count: number;
  invoice_count: number;
}

export interface VendorPayload {
  name: string;
  kind: VendorKind;
  contact_name?: string | null;
  phone?: string | null;
  email?: string | null;
  gstin?: string | null;
  notes?: string | null;
}

export type InvoiceStatus = 'DRAFT' | 'SUBMITTED' | 'APPROVED' | 'REJECTED';

export const INVOICE_STATUSES: InvoiceStatus[] = ['DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED'];

export const INVOICE_STATUS_LABELS: Record<InvoiceStatus, string> = {
  DRAFT: 'Draft',
  SUBMITTED: 'Waiting for approval',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
};

export const INVOICE_STATUS_TONES: Record<InvoiceStatus, 'neutral' | 'warning' | 'success' | 'danger'> = {
  DRAFT: 'neutral',
  SUBMITTED: 'warning',
  APPROVED: 'success',
  REJECTED: 'danger',
};

/** Who prepares invoices and keeps the vendor list. Not the super admin: they
 *  approve invoices, and whoever approves a bill must not have written it. */
export const INVOICE_EDITOR_ROLES: Role[] = ['ADMIN', 'SYSTEM_ADMIN'];

export const isInvoiceEditor = (role: Role | null | undefined): boolean =>
  !!role && INVOICE_EDITOR_ROLES.includes(role);

export interface InvoiceSummary {
  id: number;
  number: string;
  vendor_id: number;
  vendor_name: string;
  vendor_kind: VendorKind;
  period_start: string;
  period_end: string;
  status: InvoiceStatus;
  currency: string;
  /** Always the sum of the lines, worked out by the server. */
  total_amount: string;
  line_count: number;
  vendor_invoice_ref: string | null;
  created_by_name: string | null;
  created_at: string;
  submitted_at: string | null;
  decided_at: string | null;
  /** The day an approved invoice was paid; null while it is still to be paid. */
  paid_on: string | null;
  /** The bank's reference for the payment - UTR, cheque number. */
  payment_reference: string | null;
}

export interface InvoiceLine {
  id: number;
  traveller_id: number;
  request_id: number;
  traveller_name: string;
  employee_code: string | null;
  request_type: RequestType | null;
  travel_date: string | null;
  /** As billed: name, trip and campaign, kept with the line. */
  description: string;
  project_code: string | null;
  booking_reference: string | null;
  amount: string;
  /** Why it cannot be billed as it stands - cancelled, cost removed, paid to
   *  someone else. Submitting and approving wait until it is dealt with. */
  problem: string | null;
}

export interface InvoiceEvent {
  action: string;
  actor_name: string | null;
  at: string;
  summary: string;
  comment: string | null;
}

export interface Invoice extends InvoiceSummary {
  vendor_gstin: string | null;
  vendor_contact_name: string | null;
  vendor_phone: string | null;
  vendor_email: string | null;
  notes: string | null;
  updated_by_name: string | null;
  updated_at: string;
  submitted_by_name: string | null;
  decided_by_name: string | null;
  decision_comment: string | null;
  paid_by_name: string | null;
  paid_at: string | null;
  lines: InvoiceLine[];
  history: InvoiceEvent[];
  /** What the viewer may do now - the same rules the server enforces. */
  can_edit: boolean;
  can_submit: boolean;
  can_delete: boolean;
  can_decide: boolean;
  /** A super admin, on an approved invoice: mark it paid, or correct it. */
  can_record_payment: boolean;
}

export interface InvoiceList {
  items: InvoiceSummary[];
  /** Per status, whatever the status filter: the tabs' counts. */
  counts: Record<InvoiceStatus, number>;
  /** Approved invoices, split by whether they are paid yet. */
  payment_counts: { paid: number; unpaid: number };
  total: number;
}

/** Paid or still to be paid: only an approved invoice has a payment state. */
export type PaymentFilter = 'paid' | 'unpaid';

/** A booked trip an invoice could carry. */
export interface EligibleRow {
  traveller_id: number;
  request_id: number;
  traveller_name: string;
  employee_code: string | null;
  request_type: RequestType;
  travel_date: string | null;
  trip: string;
  project_code: string;
  project_name: string;
  booking_reference: string | null;
  amount: string;
  /** Null when nobody recorded who was paid; saving it records this vendor. */
  vendor_id: number | null;
  on_this_invoice: boolean;
  /** Only on a line already on the invoice: why it can no longer be kept. */
  problem: string | null;
}

export interface InvoicePayload {
  vendor_id: number;
  period_start: string;
  period_end: string;
  traveller_ids: number[];
  vendor_invoice_ref?: string | null;
  notes?: string | null;
}
