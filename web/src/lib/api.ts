import axios, { AxiosError } from 'axios';

import type {
  CabTrip,
  CabType,
  AppNotification,
  AnalyticsBundle,
  AuditRow,
  BatchDecisionItem,
  BookingDetails,
  ChainVerification,
  Colleague,
  CoStayMatch,
  CostPreview,
  Department,
  EligibleRow,
  EmailStatus,
  EmailTestResult,
  FilterOptions,
  Insights,
  InsightFilters,
  TravelLog,
  TravellerStatus,
  IdProof,
  ImportPreview,
  ImportResult,
  Invoice,
  InvoiceList,
  InvoicePayload,
  InvoiceStatus,
  InviteLink,
  JobResult,
  LedgerRow,
  LoginResponse,
  ManagerRecommendation,
  NotificationPreferences,
  NotificationLedger,
  OpenTrips,
  Paginated,
  PasswordChanged,
  PaymentFilter,
  ProfileUpdate,
  Project,
  QueueCounts,
  QueueExport,
  RequestConflict,
  RequestPriority,
  RequestRevision,
  RequestType,
  RetentionStatus,
  ReviewFilter,
  TravelHistory,
  SchedulerStatus,
  RoomSharingChoice,
  TeamChange,
  TeamChangeList,
  TeamChangeStatus,
  TeamDecision,
  ThemePreference,
  Ticket,
  TokenPreview,
  TravelMode,
  TravelRequest,
  UserProfile,
  UserRow,
  Vendor,
  VendorPayload,
  UserStatus,
} from '@/types';

/**
 * The API version this build was written against. Bump it together with
 * API_VERSION in backend/app/main.py (a backend test checks they match). The
 * shell compares it with what /health reports, to tell an admin when the API
 * process is older than this page.
 */
export const API_VERSION = '0.17.0';

/** Negative when `a` is older than `b`, by dotted number. */
export function compareVersions(a: string, b: string): number {
  const left = a.split('.').map(Number);
  const right = b.split('.').map(Number);
  for (let i = 0; i < Math.max(left.length, right.length); i++) {
    const difference = (left[i] || 0) - (right[i] || 0);
    if (difference !== 0) return difference;
  }
  return 0;
}

/** What a request to a route the server does not have is told instead of
 *  FastAPI's bare "Not Found" or "Method Not Allowed". */
const STALE_API_MESSAGE =
  'The server does not know this action yet - it is running older code than this page. ' +
  'Restart the API (after running its migrations), then try again.';

/**
 * Single axios instance for the whole app. In dev, Vite proxies `/api` to the
 * FastAPI process, so the browser only ever sees one origin.
 */
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api',
  timeout: 30_000,
});

/** Set by the auth store; kept out of it to avoid an import cycle. Called
 *  with the server's reason, e.g. "Your account is deactivated...". */
let onUnauthorized: ((detail?: string) => void) | null = null;

export function setUnauthorizedHandler(handler: (detail?: string) => void) {
  onUnauthorized = handler;
}

let accessToken: string | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

api.interceptors.request.use((config) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    // Downloads ask for a Blob, so their error body arrives as one too. The
    // server still sent JSON; read it so the reason reaches the person instead
    // of "Something went wrong."
    const body = error.response?.data;
    if (body instanceof Blob && body.type.includes('json')) {
      try {
        error.response!.data = JSON.parse(await body.text());
      } catch {
        // Keep the blob; errorMessage falls back.
      }
    }

    // A 401 means the session is gone - expired, revoked, a password changed
    // elsewhere, or the account was deactivated. Only a request that carried
    // the token in use now counts: one sent just before a password change
    // (with the old token) must not sign out the person who changed it.
    const sent = error.config?.headers?.Authorization;
    if (
      error.response?.status === 401 &&
      onUnauthorized &&
      (!accessToken || !sent || sent === `Bearer ${accessToken}`)
    ) {
      const detail = (error.response.data as { detail?: unknown } | undefined)?.detail;
      onUnauthorized(typeof detail === 'string' ? detail : undefined);
    }
    return Promise.reject(error);
  },
);

/** "full_name" -> "Full name", for naming the field a 422 is about. */
function fieldLabel(loc: unknown): string | null {
  if (!Array.isArray(loc) || loc.length === 0) return null;
  const last = loc[loc.length - 1];
  if (typeof last !== 'string' || last === 'body' || last === 'query') return null;
  const words = last.replace(/_id$/, '').replace(/_/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : null;
}

/** FastAPI's own answers when no route matches. Every 404 this API raises on
 *  purpose names what was missing ("Request not found."), so these two mean
 *  the route itself is absent: the API process predates the page. */
const NO_ROUTE: Record<number, string> = { 404: 'Not Found', 405: 'Method Not Allowed' };

/** Pull a readable message out of a FastAPI error body. */
export function errorMessage(error: unknown, fallback = 'Something went wrong.'): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') {
      return detail === NO_ROUTE[error.response?.status ?? 0] ? STALE_API_MESSAGE : detail;
    }
    // 422 bodies are a list of per-field validation errors. Pydantic's wording
    // ("Value error, ...", no field name) is for developers; say which field
    // and what is wrong with it.
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as {
        msg?: unknown;
        type?: unknown;
        loc?: unknown;
        ctx?: Record<string, unknown>;
      };
      if (typeof first?.msg === 'string') {
        const message = first.msg.replace(/^(Value error|Assertion failed), /, '');
        const label = fieldLabel(first.loc);
        if (!label) return message;
        switch (first.type) {
          case 'missing':
            return `${label} is required.`;
          case 'string_too_short':
            return `${label} must be at least ${first.ctx?.min_length} characters.`;
          case 'string_too_long':
            return `${label} must be at most ${first.ctx?.max_length} characters.`;
          case 'value_error':
            return message;
          default:
            return `${label}: ${message}`;
        }
      }
    }
    if (error.code === 'ECONNABORTED') {
      return 'The server took too long to answer. Please try again.';
    }
    if (!error.response) return 'Cannot reach the server. Check your connection and try again.';
  }
  return fallback;
}

// --- health ---------------------------------------------------------------

interface HealthResponse {
  status: string;
  app: string;
  /** Absent on servers from before the version handshake. */
  version?: string;
  environment: string;
  database: string;
  migrations_pending?: boolean;
}

export const fetchHealth = () => api.get<HealthResponse>('/health').then((r) => r.data);

// --- auth -----------------------------------------------------------------

export const login = (email: string, password: string) =>
  api.post<LoginResponse>('/auth/login', { email, password }).then((r) => r.data);

export const logout = () => api.post('/auth/logout').then((r) => r.data);

export const fetchMe = () => api.get<UserProfile>('/auth/me').then((r) => r.data);

export const saveThemePreference = (theme_preference: ThemePreference) =>
  api.patch<UserProfile>('/auth/me/theme', { theme_preference }).then((r) => r.data);

/** Signs out every other device; the answer carries a fresh token for this
 *  one (swap it in with the auth store's replaceToken). */
export const changePassword = (current_password: string, new_password: string) =>
  api
    .post<PasswordChanged>('/auth/change-password', { current_password, new_password })
    .then((r) => r.data);

export const updateMyProfile = (payload: ProfileUpdate) =>
  api.patch<UserProfile>('/auth/me', payload).then((r) => r.data);

export const changeMyEmail = (new_email: string, current_password: string) =>
  api
    .post<UserProfile>('/auth/me/email', { new_email, current_password })
    .then((r) => r.data);

export const previewToken = (token: string) =>
  api.get<TokenPreview>(`/auth/token/${token}`).then((r) => r.data);

export const setPassword = (token: string, password: string) =>
  api.post<{ detail: string }>('/auth/set-password', { token, password }).then((r) => r.data);

export const forgotPassword = (email: string) =>
  api.post<{ detail: string }>('/auth/forgot-password', { email }).then((r) => r.data);

// --- users ----------------------------------------------------------------

interface UserQuery {
  search?: string;
  role?: string;
  is_active?: boolean;
  /** Without it, everyone except deleted accounts. */
  status?: UserStatus;
  department_id?: number;
  /** Only the people who report to this manager. */
  manager_id?: number;
  page?: number;
  page_size?: number;
}

export const fetchUsers = (params: UserQuery) =>
  api.get<Paginated<UserRow>>('/users', { params }).then((r) => r.data);

export interface UserPayload {
  email: string;
  full_name: string;
  role: string;
  designation?: string | null;
  /** MALE or FEMALE; required on create. */
  gender: string;
  phone?: string | null;
  employee_code?: string | null;
  base_state?: string | null;
  /** The city or constituency. */
  base_location?: string | null;
  department_id?: number | null;
  /** Ground staff only: the manager they report to. */
  manager_id?: number | null;
  /** Create only: email the invitation as well as showing the link. */
  send_email?: boolean;
}

/** A partial update. Status is not here: it has its own endpoint. */
export type UserUpdatePayload = Partial<UserPayload>;

export const createUser = (payload: UserPayload) =>
  api.post<InviteLink>('/users', payload).then((r) => r.data);

export const updateUser = (id: number, payload: UserUpdatePayload) =>
  api.patch<UserRow>(`/users/${id}`, payload).then((r) => r.data);

interface StatusChange {
  status: UserStatus;
  /** Left (or Deleted) only; defaults to today on the server. */
  exited_on?: string | null;
  reason?: string | null;
}

export const changeUserStatus = (id: number, change: StatusChange) =>
  api.post<UserRow>(`/users/${id}/status`, change).then((r) => r.data);

export const fetchUserOpenTrips = (id: number) =>
  api.get<OpenTrips>(`/users/${id}/open-trips`).then((r) => r.data);

/** The link always comes back to copy; `sendEmail` also emails it to them. */
export const reinviteUser = (id: number, sendEmail = true) =>
  api
    .post<InviteLink>(`/users/${id}/reinvite`, null, { params: { send_email: sendEmail } })
    .then((r) => r.data);

// --- a manager's team ---------------------------------------------------------

/** The people who report to the signed-in manager. */
export const fetchMyTeam = () => api.get<UserRow[]>('/team/members').then((r) => r.data);

export interface TeamAddPayload {
  email: string;
  full_name: string;
  gender: string;
  designation?: string | null;
  phone?: string | null;
  employee_code?: string | null;
  base_state?: string | null;
  base_location?: string | null;
  note?: string | null;
}

type TeamEditPayload = Partial<Omit<TeamAddPayload, 'email' | 'gender'>>;

interface TeamRemovePayload {
  status: 'LEFT' | 'DEACTIVATED';
  exited_on?: string | null;
  reason: string;
  note?: string | null;
}

export const askToAddMember = (payload: TeamAddPayload) =>
  api.post<TeamChange>('/team/changes/add', payload).then((r) => r.data);

export const askToEditMember = (userId: number, payload: TeamEditPayload) =>
  api.post<TeamChange>(`/team/changes/edit/${userId}`, payload).then((r) => r.data);

export const askToRemoveMember = (userId: number, payload: TeamRemovePayload) =>
  api.post<TeamChange>(`/team/changes/remove/${userId}`, payload).then((r) => r.data);

export const withdrawTeamChange = (id: number) =>
  api.post<TeamChange>(`/team/changes/${id}/cancel`).then((r) => r.data);

/** A manager's own asks, or every ask for an admin. */
export const fetchTeamChanges = (status?: TeamChangeStatus) =>
  api
    .get<TeamChangeList>('/team/changes', { params: status ? { status } : {} })
    .then((r) => r.data);

export const approveTeamChange = (id: number, comment: string, sendEmail = true) =>
  api
    .post<TeamDecision>(`/team/changes/${id}/approve`, {
      comment: comment.trim() || null,
      send_email: sendEmail,
    })
    .then((r) => r.data);

export const rejectTeamChange = (id: number, comment: string) =>
  api.post<TeamDecision>(`/team/changes/${id}/reject`, { comment }).then((r) => r.data);

export const unlockUser = (id: number) =>
  api.post<UserRow>(`/users/${id}/unlock`).then((r) => r.data);

// --- departments ----------------------------------------------------------

export const fetchDepartments = () =>
  api.get<Department[]>('/departments').then((r) => r.data);

/** Returns the existing department when the name is already taken (any case). */
export const createDepartment = (name: string) =>
  api.post<Department>('/departments', { name }).then((r) => r.data);

export const renameDepartment = (id: number, name: string) =>
  api.patch<Department>(`/departments/${id}`, { name }).then((r) => r.data);

/** Refused (409) while anyone is still in it. */
export const deleteDepartment = (id: number) => api.delete(`/departments/${id}`);

// --- audit ----------------------------------------------------------------

interface AuditQuery {
  action?: string;
  entity_type?: string;
  actor_user_id?: number;
  page?: number;
  page_size?: number;
}

export const fetchAudit = (params: AuditQuery) =>
  api.get<Paginated<AuditRow>>('/audit', { params }).then((r) => r.data);

export const verifyAuditChain = () =>
  api.get<ChainVerification>('/audit/verify').then((r) => r.data);

// --- projects -------------------------------------------------------------

interface ProjectQuery {
  search?: string;
  status?: string;
  page?: number;
  page_size?: number;
}

/** No code: every campaign's ID is generated by the server. */
export interface ProjectPayload {
  name: string;
  description?: string | null;
  client_name?: string | null;
  state?: string | null;
  /** City or assembly constituency. */
  city?: string | null;
  status?: string;
  start_date?: string | null;
  end_date?: string | null;
}

export const fetchProjects = (params: ProjectQuery) =>
  api.get<Paginated<Project>>('/projects', { params }).then((r) => r.data);

export const createProject = (payload: ProjectPayload) =>
  api.post<Project>('/projects', payload).then((r) => r.data);

export const updateProject = (id: number, payload: Partial<ProjectPayload>) =>
  api.patch<Project>(`/projects/${id}`, payload).then((r) => r.data);

export const archiveProject = (id: number) =>
  api.post<Project>(`/projects/${id}/archive`).then((r) => r.data);

export const restoreProject = (id: number) =>
  api.post<Project>(`/projects/${id}/restore`).then((r) => r.data);

/** Only a campaign with no requests; the server answers 409 otherwise. */
export const deleteProject = (id: number) =>
  api.delete(`/projects/${id}`).then(() => undefined);

// --- identity documents ---------------------------------------------------

export const fetchIdProofs = (userId: number) =>
  api.get<IdProof[]>(`/users/${userId}/id-proofs`).then((r) => r.data);

export function addIdProof(
  userId: number,
  fields: {
    proof_type: string;
    number: string;
    label?: string;
    issued_on?: string;
    expires_on?: string;
  },
  file?: File | null,
) {
  const form = new FormData();
  Object.entries(fields).forEach(([key, value]) => {
    if (value) form.append(key, value);
  });
  if (file) form.append('file', file);
  return api.post<IdProof>(`/users/${userId}/id-proofs`, form).then((r) => r.data);
}

/** The one call that decrypts. Every use writes a VIEW_SENSITIVE audit row. */
export const revealIdProof = (proofId: number) =>
  api
    .get<{ id: number; proof_type: string; number: string }>(`/id-proofs/${proofId}/reveal`)
    .then((r) => r.data);

/** Fetched as a blob rather than linked, so the bearer token is still attached
 *  and the download is audited like any other read. */
export const fetchIdProofFile = (proofId: number) =>
  api.get(`/id-proofs/${proofId}/file`, { responseType: 'blob' }).then((r) => r.data as Blob);

export const deleteIdProof = (proofId: number) =>
  api.delete(`/id-proofs/${proofId}`).then((r) => r.data);

export const fetchRetentionStatus = () =>
  api.get<RetentionStatus>('/id-proofs/retention').then((r) => r.data);

export const runRetentionPurge = () =>
  api
    .post<{ purged: number; files_removed: number; cutoff: string }>('/id-proofs/retention/purge')
    .then((r) => r.data);

// --- bulk import ----------------------------------------------------------

export const fetchImportTemplate = () =>
  api.get('/users/import/template', { responseType: 'blob' }).then((r) => r.data as Blob);

export function previewImport(file: File) {
  const form = new FormData();
  form.append('file', file);
  return api.post<ImportPreview>('/users/import/preview', form).then((r) => r.data);
}

export function commitImport(file: File) {
  const form = new FormData();
  form.append('file', file);
  return api.post<ImportResult>('/users/import', form).then((r) => r.data);
}

// --- requests -------------------------------------------------------------

interface RequestQuery {
  mine?: boolean;
  status?: string;
  type?: string;
  project_id?: number;
  search?: string;
  priority?: RequestPriority;
  /** 'priority' puts high first, then medium, then low; newest first within each. */
  sort?: 'newest' | 'priority';
  /** Two-level approval: still waiting on a manager, or already reviewed. */
  review?: ReviewFilter;
  /** Cabs waiting on an admin's answer to the older "one more day" ask. */
  extension?: 'pending';
  /** Only extensions: a cab kept longer, a stay made longer. */
  extensions_only?: boolean;
  /** Decided trips whose requester asked to cancel, still waiting. */
  cancellation?: 'pending';
  page?: number;
  page_size?: number;
}

/** The body the API takes for a create, an edit or a dry-run check. It is a full
 *  replacement rather than a patch: a revision has to say what a field was as
 *  well as what it became, which a partial body cannot. */
export interface RequestPayload {
  request_type: RequestType;
  project_id: number;
  traveller_ids: number[];
  mode?: TravelMode | null;
  origin?: string | null;
  destination?: string | null;
  origin_state?: string | null;
  destination_state?: string | null;
  hotel_state?: string | null;
  /** A cab's city or constituency; null for anything else. */
  pickup_city?: string | null;
  drop_city?: string | null;
  start_at?: string | null;
  end_at?: string | null;
  hotel_city?: string | null;
  check_in?: string | null;
  check_out?: string | null;
  /** A cab's size and distance; the server clears them for anything else. */
  cab_type?: CabType | null;
  cab_trip?: CabTrip | null;
  cab_distance_km?: number | null;
  travel_reason?: string;
  /** Only with the fallback "Other" campaign: the name the requester typed. */
  other_project_name?: string | null;
  /** Defaults to MEDIUM on the server when left out. */
  priority?: RequestPriority;
  notes?: string | null;
  is_draft?: boolean;
  /** A new hotel request only: the requester's own room. Left out, the admin
   *  decides; a shared room is an ask until an admin confirms it. */
  room_sharing?: RoomSharingChoice | null;
  share_with_user_id?: number | null;
}

export const fetchRequests = (params: RequestQuery) =>
  api.get<Paginated<TravelRequest>>('/requests', { params }).then((r) => r.data);

export const fetchRequest = (id: number) =>
  api.get<TravelRequest>(`/requests/${id}`).then((r) => r.data);

export const createRequest = (payload: RequestPayload) =>
  api.post<TravelRequest>('/requests', payload).then((r) => r.data);

export const editRequest = (id: number, payload: RequestPayload) =>
  api.put<TravelRequest>(`/requests/${id}`, payload).then((r) => r.data);

export const submitRequest = (id: number) =>
  api.post<TravelRequest>(`/requests/${id}/submit`).then((r) => r.data);

export const cancelRequest = (id: number, reason: string) =>
  api.post<TravelRequest>(`/requests/${id}/cancel`, { reason }).then((r) => r.data);

export const fetchRevisions = (id: number) =>
  api.get<RequestRevision[]>(`/requests/${id}/revisions`).then((r) => r.data);

/** Dry-run the conflict and co-stay checks while the form is still being typed.
 *  Saves nothing; the same checks run again on the real write. */
export const checkRequest = (payload: RequestPayload & { request_id?: number }) =>
  api
    .post<{ conflicts: RequestConflict[]; costay_matches: CoStayMatch[] }>('/requests/check', payload)
    .then((r) => r.data);

/** Colleagues of the caller's gender staying in a city, from the moment the city
 *  is picked: with dates, the stays that overlap; without, every stay to come. */
export const fetchRoomMatches = (params: {
  city: string;
  check_in?: string;
  check_out?: string;
  request_id?: number;
}) => api.get<CoStayMatch[]>('/requests/room-matches', { params }).then((r) => r.data);

/** An admin puts a hotel traveller in one room with a colleague, or - with
 *  null - in a room of their own. Both sides of a pairing are updated. */
export const allotRoom = (requestId: number, travellerId: number, shareWithUserId: number | null) =>
  api
    .post<TravelRequest>(`/requests/${requestId}/travellers/${travellerId}/room`, {
      share_with_user_id: shareWithUserId,
    })
    .then((r) => r.data);

export const setRoomSharing = (
  id: number,
  body: { traveller_id: number; choice: RoomSharingChoice; share_with_user_id?: number | null },
) => api.post<TravelRequest>(`/requests/${id}/room-sharing`, body).then((r) => r.data);

/** The signed-in person's own in-app notices. My requests reads the room-share
 *  asks out of these. */
export const fetchNotifications = () =>
  api.get<AppNotification[]>('/notifications/mine').then((r) => r.data);

/** A thin colleague list for the co-traveller picker. Ground staff cannot read
 *  /users, and tagging someone does not need their whole employee record. */
export const fetchColleagues = () =>
  api.get<Colleague[]>('/requests/colleagues').then((r) => r.data);

// --- admin decisions ------------------------------------------------------

/** Without filters, the whole queue; with them, only what matches - for tab
 *  labels that agree with the filtered list under them. */
export const fetchQueueCounts = (
  filters: {
    type?: RequestType;
    search?: string;
    priority?: RequestPriority;
    review?: ReviewFilter;
    extensions_only?: boolean;
  } = {},
) =>
  api.get<QueueCounts>('/requests/queue/counts', { params: filters }).then((r) => r.data);

/** Every request in one queue tab, not just a page, for the CSV export. Given
 *  longer than the default timeout: a big tab is read row by row as the admin. */
export const exportQueue = (params: {
  status: string;
  type?: RequestType;
  search?: string;
  priority?: RequestPriority;
  review?: ReviewFilter;
  extensions_only?: boolean;
}) =>
  api
    .get<QueueExport>('/requests/queue/export', { params, timeout: 120_000 })
    .then((r) => r.data);

/** Several travellers on one request in a single transaction — one failure rolls
 *  the whole set back, so the queue never shows a half-applied decision. */
export const decideBatch = (requestId: number, decisions: BatchDecisionItem[]) =>
  api.post<TravelRequest>(`/requests/${requestId}/decide`, { decisions }).then((r) => r.data);

/** A manager's team requests: still waiting for their recommendation, or
 *  already reviewed. One shared query key, so the sidebar badge and the Team
 *  approvals page agree. */
export const fetchTeamReviews = (review: ReviewFilter) =>
  fetchRequests({ mine: false, review, sort: 'priority', page_size: 100 });

interface RecommendationBody {
  recommendation: ManagerRecommendation;
  comment: string;
  /** Traveller rows on the request. Left out: every one of the manager's
   *  team on it who is still pending. */
  traveller_ids?: number[];
}

/** The first of the two levels: the manager's view, with a comment. Advice -
 *  the admin makes the final decision. */
export const recommendRequest = (requestId: number, body: RecommendationBody) =>
  api.post<TravelRequest>(`/requests/${requestId}/recommendation`, body).then((r) => r.data);

// --- cabs: the car sent, and one more day ---------------------------------

export interface CabBookingBody {
  booked_cab_type: CabType;
  vehicle_number: string;
  driver_name: string;
  driver_phone: string;
  /** Off when the same dialog is about to mark the traveller booked: the
   *  booking notice then carries the car, so one message goes, not two. */
  notify?: boolean;
  /** The cab operator paid, for everyone riding. Left out, each keeps theirs. */
  vendor_id?: number;
}

/** Record or change the car sent for a cab. Admins only; logged, and everyone
 *  riding is told unless `notify` is false. */
export const recordCabBooking = (requestId: number, body: CabBookingBody) =>
  api.put<TravelRequest>(`/requests/${requestId}/cab-booking`, body).then((r) => r.data);

/** What extending a trip takes: a cab's pickup on the first extra day and
 *  until when, or a stay's new check-out. */
export interface ExtensionBody {
  reason: string;
  /** Traveller rows on the trip; left out, everyone approved or booked. */
  traveller_ids?: number[];
  start_at?: string | null;
  end_at?: string | null;
  check_out?: string | null;
  priority?: RequestPriority;
}

/** Carry a decided cab or stay on for more days. Returns the extension: a new
 *  request, linked to this one, waiting for an admin. */
export const extendTrip = (requestId: number, body: ExtensionBody) =>
  api.post<TravelRequest>(`/requests/${requestId}/extend`, body).then((r) => r.data);

/** Book one or more approved travellers in one step - see BookingModal. */
export interface BookingBody {
  traveller_ids: number[];
  booking_reference?: string | null;
  booking_details?: BookingDetails | null;
  /** The note to the traveller. */
  note: string;
  /** A cab only: the car sent. */
  cab?: Omit<CabBookingBody, 'notify' | 'vendor_id'> | null;
  /** Files already uploaded for these travellers; all go with the email. */
  ticket_ids?: number[];
  /** The total for everyone booked, split evenly. A string: money is never a float. */
  cost_amount?: string | null;
  /** Sent: recorded for everyone booked (null clears it). Left out: kept. */
  vendor_id?: number | null;
  notify?: boolean;
}

export const bookTravellers = (requestId: number, body: BookingBody) =>
  api.post<TravelRequest>(`/requests/${requestId}/book`, body).then((r) => r.data);

/** An admin's answer to the older "one more day" ask. A rejection needs a comment. */
/** Approve an ask to cancel (the trip is cancelled) or reject it with a comment. */
export const decideCancellation = (
  requestId: number,
  body: { approve: boolean; comment?: string | null },
) =>
  api
    .post<TravelRequest>(`/requests/${requestId}/cancellation/decide`, body)
    .then((r) => r.data);

export const decideCabExtension = (
  requestId: number,
  body: { approve: boolean; comment?: string | null },
) =>
  api
    .post<TravelRequest>(`/requests/${requestId}/cab-extension/decide`, body)
    .then((r) => r.data);

// --- tickets and extraction -----------------------------------------------

export const fetchTickets = (requestId: number) =>
  api.get<Ticket[]>(`/requests/${requestId}/tickets`).then((r) => r.data);

/** Upload runs extraction inline — a ticket takes a few seconds and the admin
 *  who uploaded it is waiting to review it. */
export function uploadTicket(requestId: number, travellerId: number, file: File) {
  const form = new FormData();
  form.append('file', file);
  return api
    .post<Ticket>(`/requests/${requestId}/tickets`, form, {
      params: { traveller_id: travellerId },
      timeout: 120_000,
    })
    .then((r) => r.data);
}

export const reextractTicket = (ticketId: number) =>
  api.post<Ticket>(`/tickets/${ticketId}/extract`, null, { timeout: 120_000 }).then((r) => r.data);

/** The only path to BOOKED. Nothing reaches a traveller before this. */
export const discardTicket = (ticketId: number) =>
  api.post<Ticket>(`/tickets/${ticketId}/discard`).then((r) => r.data);

/** Fetched as a blob rather than linked, so the bearer token is still attached —
 *  the document carries a PNR and a passenger name. */
export const fetchTicketFile = (ticketId: number) =>
  api.get(`/tickets/${ticketId}/file`, { responseType: 'blob' }).then((r) => r.data as Blob);

/** One of the files on a traveller's booking, for them (or the person who
 *  asked for the trip) to keep. */
export const fetchMyTicketFile = (requestId: number, travellerId: number, ticketId: number) =>
  api
    .get(`/requests/${requestId}/travellers/${travellerId}/tickets/${ticketId}`, {
      responseType: 'blob',
    })
    .then((r) => r.data as Blob);

// --- the notification ledger ----------------------------------------------

export const fetchLedger = (params: {
  status?: string;
  channel?: string;
  search?: string;
  page_size?: number;
}) =>
  api.get<NotificationLedger>('/notifications/ledger', { params }).then((r) => r.data);

export const retryFailedEmail = () =>
  api
    .post<{ attempted: number; sent: number; still_failing: number }>('/notifications/retry')
    .then((r) => r.data);

/** What the running API is using for email, and what is missing. No
 *  connection to the mail server, so cheap to load. */
export const fetchEmailStatus = () =>
  api.get<EmailStatus>('/notifications/email/status').then((r) => r.data);

/** Send one real message and report how far it got. Slow when the mail server
 *  is unreachable: the server waits out its own connection timeout first. */
export const sendTestEmail = (to?: string) =>
  api
    .post<EmailTestResult>('/notifications/email/test', { to: to || null }, { timeout: 60_000 })
    .then((r) => r.data);

// --- notifications: inbox, preferences, scheduler -------------------------

export const fetchMyNotices = () =>
  api.get<LedgerRow[]>('/notifications/mine').then((r) => r.data);

export const fetchUnreadCount = () =>
  api.get<{ unread: number }>('/notifications/unread-count').then((r) => r.data);

export const markNoticeRead = (id: number) =>
  api.post<{ marked: number }>(`/notifications/${id}/read`).then((r) => r.data);

export const markAllRead = () =>
  api.post<{ marked: number }>('/notifications/read').then((r) => r.data);

export const fetchPreferences = () =>
  api.get<NotificationPreferences>('/notifications/preferences').then((r) => r.data);

export const setPreference = (category: string, enabled: boolean) =>
  api
    .patch<NotificationPreferences>('/notifications/preferences', { category, enabled })
    .then((r) => r.data);

export const fetchSchedulerStatus = () =>
  api.get<SchedulerStatus>('/notifications/scheduler').then((r) => r.data);

/** Safe to press twice: every notice the jobs write is deduplicated by event. */
export const runReminderJobs = () =>
  api.post<JobResult[]>('/notifications/run-jobs', null, { timeout: 120_000 }).then((r) => r.data);

// --- cost and analytics ---------------------------------------------------

export const fetchAnalytics = (params: InsightFilters = {}) =>
  api.get<AnalyticsBundle>('/analytics', { params: repeatParams({ ...params }) }).then((r) => r.data);

/** `vendorId`: who was paid, for everyone in `amounts`. Left out, each keeps
 *  the vendor they had. */
export const setCosts = (
  requestId: number,
  amounts: { traveller_id: number; amount: string | null; note?: string | null }[],
  vendorId?: number,
) =>
  api
    .post<TravelRequest>(`/requests/${requestId}/costs`, {
      amounts,
      ...(vendorId !== undefined ? { vendor_id: vendorId } : {}),
    })
    .then((r) => r.data);

/** What an even split comes to, before saving. Shown so the odd paisa on the
 *  first row does not look like a bug the first time someone divides by three. */
export const previewSplit = (
  requestId: number,
  body: { total_amount: string; traveller_ids: number[] },
) => api.post<CostPreview>(`/requests/${requestId}/costs/preview`, body).then((r) => r.data);

export const splitCost = (
  requestId: number,
  body: { total_amount: string; traveller_ids: number[]; note?: string | null; vendor_id?: number },
) => api.post<TravelRequest>(`/requests/${requestId}/costs/split`, body).then((r) => r.data);

// --- vendors and invoices ---------------------------------------------------

export const fetchVendors = (active?: boolean) =>
  api
    .get<Vendor[]>('/vendors', { params: active === undefined ? {} : { active } })
    .then((r) => r.data);

export const createVendor = (payload: VendorPayload) =>
  api.post<Vendor>('/vendors', payload).then((r) => r.data);

export const updateVendor = (id: number, payload: Partial<VendorPayload>) =>
  api.patch<Vendor>(`/vendors/${id}`, payload).then((r) => r.data);

/** Switched off, a vendor is no longer offered for new costs; its invoices
 *  can still be finished. */
export const setVendorActive = (id: number, active: boolean) =>
  api.post<Vendor>(`/vendors/${id}/${active ? 'activate' : 'deactivate'}`).then((r) => r.data);

export const fetchInvoices = (
  params: { status?: InvoiceStatus; vendor_id?: number; payment?: PaymentFilter; limit?: number } = {},
) =>
  api.get<InvoiceList>('/invoices', { params }).then((r) => r.data);

export const fetchInvoice = (id: number) =>
  api.get<Invoice>(`/invoices/${id}`).then((r) => r.data);

/** Booked trips with a cost, paid to the vendor, in the period, on no other
 *  invoice. With `invoice_id`, that invoice's own lines are included and marked. */
export const fetchEligible = (params: {
  vendor_id: number;
  start: string;
  end: string;
  invoice_id?: number;
  include_unassigned?: boolean;
}) => api.get<EligibleRow[]>('/invoices/eligible', { params }).then((r) => r.data);

/** Why the period's trips are not listed for this vendor, counted by reason. */
export interface WhyNotListed {
  trips_in_period: number;
  not_booked_yet: number;
  booked_without_cost: number;
  other_vendor: number;
  no_vendor_recorded: number;
  already_invoiced: number;
}

export const fetchWhyNotListed = (params: { vendor_id: number; start: string; end: string }) =>
  api.get<WhyNotListed>('/invoices/eligible/why', { params }).then((r) => r.data);

export const createInvoice = (payload: InvoicePayload) =>
  api.post<Invoice>('/invoices', payload).then((r) => r.data);

export const updateInvoice = (id: number, payload: Partial<InvoicePayload>) =>
  api.patch<Invoice>(`/invoices/${id}`, payload).then((r) => r.data);

export const submitInvoice = (id: number) =>
  api.post<Invoice>(`/invoices/${id}/submit`).then((r) => r.data);

/** `expected_total` is the total on screen: if a cost moved it since, the
 *  server refuses and the page shows the new figures instead. */
export const approveInvoice = (
  id: number,
  body: {
    comment?: string | null;
    expected_total?: string;
    /** Paid already, in the same step; left out, approved and still to be paid. */
    paid?: boolean;
    paid_on?: string | null;
    payment_reference?: string | null;
  },
) =>
  api.post<Invoice>(`/invoices/${id}/approve`, body).then((r) => r.data);

export const rejectInvoice = (id: number, comment: string) =>
  api.post<Invoice>(`/invoices/${id}/reject`, { comment }).then((r) => r.data);

/** Mark an approved invoice paid - or, correcting a mistake, not paid after
 *  all, which needs a comment. Super admins only. */
export const recordInvoicePayment = (
  id: number,
  body: { paid: boolean; paid_on?: string | null; payment_reference?: string | null; comment?: string | null },
) => api.post<Invoice>(`/invoices/${id}/payment`, body).then((r) => r.data);

export const deleteInvoice = (id: number) => api.delete(`/invoices/${id}`).then(() => undefined);

/** Fetched as a blob so the bearer token is attached; the download is logged. */
export const downloadInvoiceCsv = (id: number) =>
  api.get(`/invoices/${id}/export.csv`, { responseType: 'blob' }).then((r) => r.data as Blob);

/** The print view tells the server it was opened, so the PDF copy is logged
 *  beside the CSV downloads. */
export const markInvoicePrinted = (id: number) =>
  api.post(`/invoices/${id}/printed`).then(() => undefined);

// --- audit viewer, hardening ----------------------------------------------

interface LedgerGrants {
  checked: boolean;
  append_only?: boolean;
  update_refused?: boolean;
  delete_refused?: boolean;
  detail: string;
  grants?: string[];
}

interface AuditSummary {
  total: number;
  by_action: Record<string, number>;
  by_entity: Record<string, number>;
  oldest: string | null;
  newest: string | null;
}

/** Probes an actual UPDATE inside a rolled-back transaction rather than parsing
 *  grant text — see services/ledger_guard.py for why. */
export const fetchLedgerGrants = () =>
  api.get<LedgerGrants>('/audit/grants').then((r) => r.data);

export const fetchAuditSummary = () =>
  api.get<AuditSummary>('/audit/summary').then((r) => r.data);

/** Fetched as a blob so the bearer token is attached — and because exporting
 *  the log writes its own VIEW_SENSITIVE row. */
export const exportAudit = (params: { action?: string; entity_type?: string; limit?: number }) =>
  api.get('/audit/export', { params, responseType: 'blob' }).then((r) => r.data as Blob);

// --- travel history -------------------------------------------------------

/** Anyone may read their own; only an admin may read someone else's. */
export const fetchTravelHistory = (userId: number, params?: { since?: string; until?: string }) =>
  api.get<TravelHistory>(`/users/${userId}/travel-history`, { params }).then((r) => r.data);

// --- locations ------------------------------------------------------------

/** Cities grouped by state, which is the shape the cascading picker wants. */
export const fetchLocations = () =>
  api.get<Record<string, string[]>>('/locations').then((r) => r.data);

// --- travel logs and the dashboard ------------------------------------------

/** Axios would send an array as `status[]=`; FastAPI wants the key repeated. */
function repeatParams(params: Record<string, unknown>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) value.forEach((v) => search.append(key, String(v)));
    else search.append(key, String(value));
  }
  return search;
}

/** The most rows one request may ask for - what an export asks for. */
export const MAX_LOG_ROWS = 5000;

export const fetchTravelLogs = (
  params: InsightFilters & {
    status?: TravellerStatus[];
    search?: string;
    page?: number;
    page_size?: number;
  },
) =>
  api
    .get<TravelLog>('/travel-logs', { params: repeatParams({ ...params }) })
    .then((r) => r.data);

export const fetchInsights = (params: InsightFilters) =>
  api.get<Insights>('/analytics/insights', { params: repeatParams({ ...params }) }).then((r) => r.data);

export const fetchFilterOptions = () =>
  api.get<FilterOptions>('/analytics/filter-options').then((r) => r.data);
