# Scope of Work (SOW): Field Logistics, Travel Management & Reimbursement System
## Platform: Sriyatra
**Company:** DesignBoxed Innovations Pvt. Ltd.  
**Document Ref:** `docs/sow/SCOPE_OF_WORK_V4.md`  
**Version:** 4.0 (Master Unified Scope of Work)  
**Date:** November 2026  
**Status:** Approved Master Specification  
**Prepared by:** Menthyala Sridhar  

---

## 1. System Overview & Authentication

**Sriyatra** is an integrated enterprise web and mobile platform designed and engineered for **DesignBoxed Innovations Pvt. Ltd.** to manage end-to-end field logistics, intercity travel, local vehicle movements, hotel accommodations, and employee expense reimbursements. The platform supports approximately 100+ field operatives deployed across state campaigns, supervised by field managers, fulfilled by contracted travel and accommodation vendors, audited and settled by the finance team, and governed by operations administrators and executive leadership.

### 1.1 Role-Based Access Control (RBAC) & User Hierarchy
The system enforces six discrete, ranked hierarchical roles, each equipped with dedicated login credentials, scoped views, operational queues, and permission boundaries:

```
┌─────────────────────────────────────────────────────────────────┐
│                          SUPER ADMIN                            │
│     System Governance, Vendor Invoices, Master Policy Setup     │
└────────────────────────────────┬────────────────────────────────┘
                                 │
         ┌───────────────────────┴───────────────────────┐
         ▼                                               ▼
┌──────────────────┐                           ┌──────────────────┐
│      ADMIN       │                           │   FINANCE TEAM   │
│Operations Desk & │                           │ Reimbursements & │
│ Vendor Dispatch  │                           │ Payout Settlement│
└────────┬─────────┘                           └────────┬─────────┘
         │                                              │
         ▼                                              │
┌──────────────────┐                                    │
│     MANAGER      │◄───────────────────────────────────┤
│Team & Campaigns, │                                    │
│  L1 Approvals    │                                    │
└────────┬─────────┘                                    │
         │                                              │
         ▼                                              ▼
┌──────────────────┐                           ┌──────────────────┐
│   GROUND STAFF   │                           │     VENDORS      │
│  (Field Users)   │                           │  Hotels, Cabs &  │
│  Reimbursements  │                           │ Ticketing Agents │
└──────────────────┘                           └──────────────────┘
```

1. **Ground Staff (Field Users):**
   - Self-service portal for mobile and desktop.
   - Raise requests for long-distance travel (Flight/Train/Bus), local cabs, and hotel stays.
   - Tag colleagues for group travel or shared cabs/rooms.
   - Save drafts; edit requests freely while pending; submit formal asks to cancel or extend.
   - Review vendor-proposed hotel options and choose preferred stay.
   - Select room-sharing with colleagues of the exact same gender.
   - Perform manual hotel check-in and check-out with automated timestamps and folio bill uploads.
   - Upload start-of-day and end-of-day cab odometer photos with auto-captured timestamps.
   - Submit itemized expense reimbursement claims for daily food (₹400/day for all cities), local transit/auto, tolls, parking, and miscellaneous field expenses with supporting bills.
   - View itineraries, download e-tickets/vouchers, and monitor reimbursement payment status.

2. **Managers:**
   - Dedicated Manager Team Portal.
   - **Campaign Management:** Full authority to create, edit, pause, and delete campaigns.
   - **Team Management:** Full authority to add new team members, edit member details, and delete/remove members from their team roster.
   - **Mandatory Level-1 Approver:** All travel, flight, cab, and hotel requests raised by team members must be approved by the manager with comments before going to Admin.
   - **Hotel Occupancy Evaluation:** Evaluates and determines whether team members are eligible for Single Occupancy or Double Occupancy (Default: Single Occupancy).
   - **Reimbursement First Review:** Initial verification and endorsement of team members' expense claims before passing to Finance.
   - Approve or reject team members' cancellation requests; inspect team travel rosters and itineraries (confidential vendor costs excluded).

3. **Vendors:**
   - Dedicated portal for contracted commercial partners (Hotels, Cab fleet operators, Travel ticketing agents).
   - Receive approved booking orders dispatched by Admin.
   - **Hotel Sourcing:** Submit 2 to 5 available hotel options with street address, landmark, Google Maps link, room category, and nightly rate for employee selection.
   - **Flights / Trains / Buses:** Issue PNR, carrier, seat/berth allocations, and upload confirmed e-tickets.
   - **Cabs:** Assign car model (Sedan/SUV), vehicle registration number, driver name, and driver contact number.
   - Submit commercial invoices against completed trips for payment reconciliation.

4. **Finance Team:**
   - Dedicated portal for Accounts & Finance Officers.
   - Dedicated audit queue for employee expense reimbursement claims.
   - Verify submitted tax invoices and receipts against company travel policy rate caps (Food: flat ₹400/day for all cities; unbilled auto max ₹150/day).
   - Actions: Approve in Full, Partial Approve with itemized deduction notes, Reject with reason, or Request Clarification.
   - Process disbursements and record payout mode (NEFT/RTGS/UPI), payment date, and transaction UTR reference number.
   - Cross-audit cab meter telemetry (actual KM traveled) against vendor cab invoices.
   - Reconcile vendor invoices and export financial ledgers to CSV / accounting systems.

5. **Admin:**
   - Central Operations & Travel Desk.
   - **Mandatory Level-2 Approver:** Final operational sanction on manager-approved requests.
   - Confirm or override hotel occupancy designations.
   - Dispatch approved booking requests to contracted Vendors.
   - Direct booking fallback: Manual booking entry or automated ticket extraction via Gemini AI.
   - Master data management: Departments, system settings, city tier policies, and vendor profiles.
   - Real-time operations dashboard, live field deployment map, and cost analytics.

6. **Super Admin:**
   - Executive governance and system security.
   - User role administration (provisioning Admin, Finance, and Manager accounts).
   - Final approval of consolidated vendor invoices before commercial release.
   - Master configuration of policy parameters (food allowance, city tiers, rate ceilings).
   - Cryptographic tamper-evident audit ledger verification.
   - Compliance monitoring, data retention schedules, and sensitive PII controls.

### 1.2 Sign-in, Accounts & Security
- **No Self-Registration:** All accounts are created by an administrator. The system generates a single-use invitation link (valid for 72 hours) dispatched via email or copied for secure sharing over WhatsApp/SMS. The user sets their own password upon first access.
- **Password & Session Security:** Passwords require a minimum of 10 characters. "Forgot password" issues a self-service reset link valid for 2 hours. Five consecutive failed sign-in attempts lock the account for 15 minutes (admins possess manual unlock capability). User sessions remain active for 8 hours.
- **Tab Isolation:** Browser tabs maintain independent session contexts, enabling administrators to test employee-facing workflows in parallel tabs without logging out.
- **Account Lifecycles:** Account statuses include `ACTIVE`, `DEACTIVATED`, `LEFT` (recording exit date), and `DELETED` (soft-deleted with historical integrity preserved). Only `ACTIVE` accounts may authenticate.

### 1.3 User Experience & Look and Feel
- **Theme Preferences:** Three-way native theme support: **Light**, **Dark**, and **System** default. Preferences are persisted in the database per user and synchronized across all desktop and mobile devices.
- **Mobile-First Responsive Layout:** Full desktop layout rendered by default on tablets and large screens; mobile browsers automatically adapt with an optimized touch view and a persistent toggle to switch between mobile and full views.
- **Localization:** DesignBoxed corporate branding throughout. Timestamps are formatted in Indian Standard Time (IST) using 12-hour AM/PM notation. Currency displays in Indian Rupees (INR, ₹).

---

## 2. Campaign & Project Management

Every field logistics movement and financial expenditure is anchored to an operational campaign:

- **Full Campaign Lifecycle Control:** Managers and Admins possess full CRUD capabilities: Create, Read, Update (edit), Pause, Complete, and Delete campaigns. A campaign with historical requests can be archived (hidden from new booking dropdowns while preserving full reporting integrity) or restored.
- **Automated Campaign Identifiers:** The system automatically generates unique campaign codes in the format `CMP-YYYY-NNNN` (e.g., `CMP-2026-0012`). IDs are system-assigned and immutable.
- **Mandatory Request Tagging:** Ground staff must select an active campaign from a dropdown when submitting any travel, cab, hotel, or reimbursement request. An emergency option — *"Other (not yet listed)"* — allows typing a campaign title so urgent field work is never blocked.
- **Campaign Financials & Reporting:** Real-time visibility into total personnel deployed, total journeys booked, hotel nights utilized, and aggregate travel, cab, hotel, and reimbursement expenditures per campaign.
- **Department Master:** System administrators manage functional departments (e.g., Operations, Media, Strategy, Logistics, Field Research). Users are tagged to departments, enabling cross-departmental cost filtering.

---

## 3. Team Management & Organizational Hierarchy

- **Single-Tier Field Hierarchy:** Each ground employee is assigned a primary reporting manager (*"Reports to"* attribute). If a manager is demoted, deactivated, or exits, their team members are released into an unassigned pool for administrative reassignment.
- **Manager Team Portal ("My Team"):** Managers have autonomous control over their direct field rosters:
  - Add new team members to their roster.
  - Edit existing team member details (name, contact, base location, designation).
  - Delete or remove team members from their team.
  - View member profiles, upcoming trips, active field deployments, and complete travel histories (confidential vendor purchase rates are hidden).
- **Field Designations:** Employees carry operational designations — **Executive**, **Team Lead (TL)**, and **Manager** — representing operational seniority.

---

## 4. Corporate Travel Policy Rules & Financial Governance

The platform embeds and automatically validates the official **DesignBoxed Innovations Pvt. Ltd.** travel regulations:

### 4.1 Mode & Class of Travel
- **Travel Mode Entitlement:**
  - Journey duration 8 hours or less: Train (3rd AC or below) or Bus (AC / Volvo).
  - Journey duration > 8 hours or distance > 500 km: Flight (Economy Class).
  - Mode/Class upgrades: Permitted only at the employee's personal expense.
- **Booking Parity:** Flight, train, and bus bookings follow the identical mandatory approval workflow (Ground Staff &rarr; Manager Approval &rarr; Admin Approval &rarr; Vendor Dispatch).

### 4.2 City Categorization & Hotel Lodging Limits (Exclusive of Taxes)
| City Category | Cities Included | Single Occupancy Max | Double Occupancy Max | Triple Occupancy Max |
|---|---|---|---|---|
| **Category A** | Mumbai, Delhi, Kolkata, Chennai, Bengaluru, Hyderabad | ₹1,500 / night | ₹2,000 / night | ₹2,500 / night |
| **Category B** | Amravati, Itanagar, Dispur, Patna, Raipur, Panaji, Gandhinagar, Chandigarh, Shimla, Ranchi, Thiruvananthapuram, Bhopal, Imphal, Shillong, Aizawl, Guwahati, Kohima, Bhubaneswar, Jaipur, Gangtok, Agartala, Lucknow, Dehradun | ₹1,000 / night | ₹1,500 / night | ₹2,000 / night |
| **Category C** | All other remaining cities and rural districts | ₹750 / night | ₹1,200 / night | ₹1,500 / night |

*Tax Invoice Requirement: Hotel invoices must be issued in the corporate name of DesignBoxed Innovations Pvt. Ltd. and state the employee's name.*

### 4.3 Expense Reimbursement Allowances & Ceilings
- **Core Company Coverage:** The company directly books and pays for intercity transportation (flight/train/bus), daily cabs, and hotel accommodations via vendors.
- **Daily Food Allowance (Employee incurred, claimed via reimbursement):**
  - **Flat Rate Across All Cities:** **₹400 per day** for all cities (supported by actual itemized food bills).
- **Local Public Conveyance (Auto / Taxi):**
  - Auto/rickshaw travel: Up to **₹150 per day** without bills; claims exceeding ₹150 require receipts.
  - Point-to-point taxis reimbursed at actuals with receipts.
  - Highway tolls and parking charges are reimbursed at actuals with receipts.
  - Tips to cab drivers and traffic violation fines are strictly non-reimbursable.
- **Submission Window:** Reimbursement claims should ideally be filed within **3 days** of trip completion.

---

## 5. Request Management (Field Employees)

### 5.1 Request Types & Specifications
Field personnel can initiate requests across three primary logistics categories:

1. **Long-Distance Travel (Flight, Train, Bus):**
   - Mode selection (Flight, Train, Bus).
   - Origin and Destination (State and City picked from a validated master list of Indian locations).
   - Departure date-time and optional Return date-time.
   - Travel reason, urgency priority (**High**, **Medium**, **Low**), notes, and group co-travelers.

2. **Cabs (Daily Hire & Outstation):**
   - **Operational Model:** In field logistics, cabs are deployed on a **whole-day basis** where the vehicle remains dedicated to the employee throughout their working hours.
   - Typically, the **pickup location and drop-off location are identical** (starting from the hotel in the morning, carrying out field visits across the district, and returning to the same hotel in the evening).
   - Vehicle type: **No preference** (default), **Sedan (Dzire - 4 seats)**, or **SUV (Ertiga - 7 seats)**.
   - Trip classification: **Local** (within 80 km) or **Outstation** (80 to 5,000 km) with estimated distance.
   - Pickup date-time and expected return date-time.

3. **Hotels & Accommodations:**
   - Destination state and city.
   - Check-in and check-out dates.
   - Default occupancy: **Single Occupancy**. (Manager or Admin evaluates eligibility for Double Occupancy).
   - Preferred area/locality and special requirements.

### 5.2 Group Requests & Co-Travelers
- A single employee can submit a request on behalf of a group by tagging registered colleagues (e.g., booking one cab for four team members or a block of train berths).
- Group requests maintain **per-traveler decision units**: an approver may approve three members and reject one without cancelling the entire request.
- Costs and split shares are tracked per traveler.

### 5.3 Edit Window & Audit Revision History
- A submitted request is **freely editable by the requester while all travelers on it remain in `PENDING` status**.
- The moment a manager or admin acts (approves, rejects, or books) on any traveler, the request automatically **locks**.
- Every edit writes an immutable **revision row**: revision number, editor identity, timestamp, and a granular before/after diff.
- Reviewers see an *"Edited N×"* badge with an expandable audit diff so changes are never approved unread.

### 5.4 Automated Conflict & Double-Booking Detection
- When a request is composed, the system validates proposed dates against existing itineraries for the requester and all tagged co-travelers.
- **Conflict Rules:**
  - Hotel vs. Hotel: Evaluated by night `[check_in, check_out)`. Any shared night triggers a warning.
  - Journey vs. Journey: Evaluated by closed datetime window `[departure, arrival]`. Overlapping legs trigger a warning.
  - Cab vs. Journey: Cab movements sharing a calendar day with a flight/train are recognized as airport/station transit and are **exempt from conflict warnings**.
  - Hotel vs. Journey: Sleeping in a city after traveling to it is standard operations and is **never a conflict**.
  - Identical Requests: Flagged specifically as `DUPLICATE_REQUEST`.
- **Enforcement:** Conflict warnings do not block the requester from submitting. However, an approver who approves over an active conflict is **hard-blocked** until they enter an audited justification, recorded in the ledger as `OVERRIDE_CONFLICT`.

### 5.5 Cancellations & Day Extensions
- **Pre-Decision Cancellation:** Before any manager/admin decision, the requester can cancel immediately with a mandatory reason.
- **Post-Approval Cancellation:** Once approved or booked, the cancellation becomes an **Ask to Cancel**. The manager and admins are alerted; either can approve the cancellation (triggering vendor booking cancellation) or reject the ask with a written comment.
- **Cab Day Extension:** A booked cab can be extended by an additional day. The traveler submits an extension request with a reason; an admin approves (moving the booking end time by 24 hours) or rejects with a comment.

---

## 6. Universal Mandatory Two-Tier Approval Pipeline

Every booking request — Flight, Train, Bus, Cab, and Hotel — must complete a strict two-step approval before being dispatched to vendors:

```
[Ground Staff Submits Request]
                │
                ▼
      [Manager Pending Queue]
                │
     ┌──────────┴──────────┐
     ▼                     ▼
[Manager Rejects]    [Manager Approves] ─── (Hotel: Evaluates Single/Double Occupancy)
(With Reason)              │
                           ▼
                 [Admin Pending Queue]
                           │
                ┌──────────┴──────────┐
                ▼                     ▼
         [Admin Rejects]       [Admin Approves]
         (With Reason)                │
                                      ▼
                        [Status: READY_FOR_VENDOR]
                                      │
                                      ▼
                     [Dispatched to Assigned Vendor]
```

1. **Step 1: Submission:** Request is submitted, tagged to a campaign with destination, dates, travel purpose, and priority.
2. **Step 2: Manager Approval (Strictly Mandatory):**
   - Enters the direct manager's queue.
   - The manager validates operational necessity, dates, and budget impact.
   - For hotels, the manager determines whether the employee is eligible for **Single Occupancy** or **Double Occupancy**.
   - Manager actions: **Approve** (with comments) or **Reject** (with reason).
   - *System Guard:* Admin approval is physically disabled in the UI and API until the manager records an `APPROVED` status.
3. **Step 3: Admin Approval (Mandatory):**
   - Enters the central Operations Admin queue.
   - Admin validates policy compliance, checks city lodging limits, confirms/overrides occupancy, and grants final approval with a logged reason.
4. **Step 4: Vendor Dispatch:**
   - Status updates to `READY_FOR_VENDOR`.
   - The request is routed to the designated contracted Vendor portal to initiate fulfillment.

---

## 7. Hotel Sourcing, Occupancy & Co-Stay Matching

```
[Admin Approves Hotel Request]
                │
                ▼
  [Dispatched to Hotel Vendor]
                │
                ▼
[Vendor Proposes 2 to 5 Hotels]
(Name, Full Street Address, Landmark, Map Link, Room Category, Nightly Rate, Amenities)
                │
                ▼
[Employee Chooses Preferred Hotel]
                │
                ▼
  [Room Occupancy Check]
         │
    ┌────┴─────────────────────────────┐
    ▼                                  ▼
[Single Occupancy]             [Double Occupancy]
    │                                  │
    │                    [Query Same Hotel & Matching Dates]
    │                                  │
    │                    ┌─────────────┴─────────────┐
    │                    ▼                           ▼
    │           [Same-Gender Matches Found]   [No Matches Found]
    │                    │                           │
    │           [Employee selects Roommate    [System books individual
    │             or lets Admin Allot]         room under double cap]
    │                    │                           │
    └────────────────────┼───────────────────────────┘
                         ▼
             [Vendor Issues Booking Voucher]
                         │
                         ▼
             [Status: BOOKED / CONFIRMED]
```

1. **Occupancy Evaluation:**
   - Default is **Single Occupancy**.
   - Evaluated by the Manager (or confirmed by Admin) based on employee seniority, project requirements, or joint deployment.
   - System enforces city rate caps (Cat A: Single ₹1,500 / Double ₹2,000; Cat B: Single ₹1,000 / Double ₹1,500; Cat C: Single ₹750 / Double ₹1,200).
2. **Vendor Hotel Proposals:**
   - The assigned vendor receives the approved city, dates, and occupancy grade.
   - The vendor submits **2 to 5 available hotel options** from their contracted network.
   - Each option includes: Hotel Name, Full Street Address, Landmark / Locality, Google Maps URL, Room Category (Standard / Deluxe), Included Amenities (Wi-Fi, Breakfast, AC), and Quoted Nightly Rate.
3. **Employee Selection:**
   - The employee receives an in-app and email alert, reviews the proposed options, and selects the hotel best suited for their field assignments.
4. **Exact Same-Gender Room Sharing Engine:**
   - If Double Occupancy is active, the system checks for existing active bookings or approved requests in that **exact same hotel** for the **overlapping nights**.
   - **Strict Safety Constraint:** Only colleagues of the **exact same gender** (`MALE` with `MALE`, `FEMALE` with `FEMALE`) are displayed. Cross-gender room sharing is strictly prevented.
   - The employee can select: (a) Share room with an identified colleague, (b) Request an individual room (if eligible under policy), or (c) Let Admin allot the roommate.
   - Once confirmed, the vendor is instructed to issue a double-sharing booking voucher.

---

## 8. In-Transit & On-Site Verification Telemetry

### 8.1 Daily Cab Hire Telemetry & In-Transit Odometer Tracking
To ensure billing integrity on whole-day cab hires where vehicles remain with employees for daytime field duties:

```
[Vendor Assigns Driver, Car Model & Vehicle Registration Number]
                               │
                               ▼
                 [Morning: Meet Cab at Hotel]
                               │
                               ▼
                      [Start Trip Action]
               1. Take Photo of Odometer (Meter)
               2. Auto-capture GPS & IST Timestamp
               3. Enter Starting Odometer Reading (KM)
                               │
                               ▼
                     [Field Duty Throughout Day]
                               │
                               ▼
                 [Evening: Return to Hotel]
                               │
                               ▼
                       [End Trip Action]
               1. Take Photo of Final Odometer (Meter)
               2. Auto-capture GPS & IST Timestamp
               3. Enter Ending Odometer Reading (KM)
                               │
                               ▼
                   [Automated Calculation]
               • Total Distance = End KM - Start KM
               • Total Duration = End Time - Start Time
               • Local (<80 km) vs Outstation Verification
                               │
                               ▼
              [Telemetry Stored for Vendor Billing Audit]
```

- **Odometer Capture:** At the morning start, the employee snaps an odometer photo, enters the numeric KM, and the system records the non-editable server timestamp (IST) and GPS coordinates.
- **Evening Conclude:** At the end of the day upon returning to the hotel, the employee snaps the ending odometer photo and enters final KM.
- **Automated Verification:** The system computes $\text{Total Distance} = \text{End KM} - \text{Start KM}$ and journey duration. Photos and computed distances are displayed side-by-side on the Admin and Finance reconciliation screens to audit against vendor invoices.

### 8.2 Manual Hotel Check-In / Check-Out Verification
To verify physical presence, eliminate phantom bookings, and support corporate tax compliance:
- **Manual Check-In:** Upon arrival at the hotel, the employee clicks **Check-In**. The system records the arrival timestamp (IST) and device geolocation. The employee enters their assigned room number.
- **Manual Check-Out:** On departure, the employee clicks **Check-Out**, auto-capturing departure time and GPS coordinates. The employee uploads a photo/PDF of the **Final Hotel Folio / Bill** (confirming company entity name and employee name).
- **Post-Stay Reimbursement Prompt:** Completing check-out automatically prompts the employee: *"Would you like to file a Reimbursement claim for food or local travel during this stay?"*

---

## 9. Post-Travel Expense Reimbursement Module

The company directly books and pays for intercity travel and hotels via vendors. Daily food, parking, tolls, and local public transit are incurred directly by the employee and reimbursed under policy caps:

```
[Employee Compiles Claim & Uploads Bills]
                     │
                     ▼
           [Manager Review & Endorsement]
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
   [Manager Rejects]    [Manager Endorses]
                                │
                                ▼
                   [Finance Audit Queue]
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
  [Finance Rejects]    [Partial Approval]    [Full Approval]
   (With Reason)       (Deductions Noted)     (Policy Validated)
                                │                     │
                                └──────────┬──────────┘
                                           ▼
                               [Disbursement & Settlement]
                          (Record Payment Mode, Date & UTR #)
                                           │
                                           ▼
                                   [Status: SETTLED]
```

### 9.1 Eligible Categories & Rules
1. **Daily Food Allowance (`FOOD`):**
   - **All Cities:** Maximum **₹400 per day** flat rate across all locations.
   - Mandatory itemized food bill / restaurant receipt for each day claimed.
   - Non-reimbursable: Alcohol, personal grooming, minibar luxury items.
2. **Local Conveyance (`LOCAL_TRANSIT`):**
   - Metered auto/rickshaw: Up to **₹150 per day** without bills; claims > ₹150 require receipts.
   - Point-to-point taxis reimbursed at actuals with receipts.
   - Highway tolls and parking charges reimbursed at actuals with receipts.
3. **Miscellaneous Field Expenses (`MISCELLANEOUS`):**
   - Incidental field supplies, stationery, or printing with receipts and written justification.

### 9.2 Reimbursement Form Structure
- **Header:** Claim Reference (`EXP-YYYY-NNNN`), employee name/code, designation, department, linked campaign, linked travel booking ID (optional), travel dates, destination city.
- **Itemized Lines:** Date, category, vendor/merchant name, bill number, bill date, claimed amount, receipt image/PDF upload, employee justification.
- **Two-Tier Audit:** Manager verifies official duty context and endorses claim &rarr; Finance audits receipts against daily policy ceilings (flat ₹400/day for food), enters approved/deducted amounts and deduction remarks.
- **Disbursement:** Finance records payment date, payment mode (NEFT/RTGS/UPI), and **Bank UTR Number**, marking the claim `SETTLED`.
- **CSV Import/Export:** Full spreadsheet export and bulk import support for audit and accounting reconciliation.

---

## 10. Fulfillment & Ticketing (Admins & Vendors)

### 10.1 Operations Action Queue
- Central dashboard categorized by status tabs: **Awaiting Decision**, **Partially Approved**, **Approved**, **Ready for Vendor**, **Booked**, **Rejected**, **Cancelled**, and **Expired** (requests where travel dates passed without decision).
- Segmented by request type: **All**, **Flight · Train · Bus**, **Cab**, and **Hotel**.
- Searchable by employee name, employee code, email, campaign, request ID, or city.
- Dedicated queues for **Asks to Cancel** and **Cab Extensions**.
- Full CSV export of any queue.

### 10.2 Vendor Portal Fulfillment
- Contracted vendors log in to their dedicated portal to access orders dispatched to them.
- **Hotel Vendors:** Enter available hotel choices with addresses, room types, nightly rates, and amenities. Upon employee selection, upload final booking voucher.
- **Cab Vendors:** Input vehicle model, registration number, driver name, and driver contact number.
- **Travel Ticketing Agents:** Enter PNR, carrier, departure/arrival times, seat numbers, and upload official e-tickets.

### 10.3 Direct Admin Booking Fallback & Gemini AI Extraction
- For bookings handled directly by the internal travel desk rather than external vendors:
- Admin opens the traveler's booking card and uploads the raw booking PDF, screenshot, or voucher.
- **Google Gemini (Vertex AI)** automatically parses and extracts: PNR, carrier, flight/train number, departure/arrival date-times, seat/berth, hotel name, address, and passenger names.
- **Human-in-the-Loop Safety:** Extraction proposals are pre-filled for administrative review. An admin checks and accepts or corrects the details. The system *never* auto-books or auto-notifies on raw AI extraction alone.
- Mismatches against the request (wrong dates, wrong city, wrong passenger name) are prominently flagged.

### 10.4 Traveler Document Delivery
- Upon booking confirmation, the traveler is notified via in-app alert and email with the **e-ticket or hotel voucher attached** (manager in CC).
- Travelers can reopen and download their tickets from **My Requests** at any time.

---

## 11. Vendors & Invoicing (Reconciliation)

- **Vendor Master Directory:** Registered travel agencies, cab operators, hotel partners, with company name, contact person, phone, email, and billing address.
- **Reconciliation Invoices:**
  - Operations admin selects a vendor and date range.
  - System aggregates all completed, verified trips during the period not yet invoiced.
  - Automatically generates an invoice draft with ID `INV-YYYY-NNNN`.
  - Admin attaches the vendor's commercial bill number and notes. Total is calculated strictly from verified trips and cannot be manually overwritten.
- **Super Admin Approval:** The invoice is submitted to the **Super Admin**, who approves it (locking the invoice amounts and freezing the underlying trips from ever being billed again) or rejects it with comments.
- **Invoice Export:** Downloadable as CSV or printable PDF for finance reconciliation.

---

## 12. Employee Management & Profiles

- **Team Directory:** Complete employee directory with CSV import and export. Fields: Full Name, Work Email, Mobile Number, Role, Designation, Department, Reports-To Manager, Base State and City, Gender (`MALE` / `FEMALE`), Employee Code, and Account Status.
- **Mobile Number Validation:** Standard 10-digit Indian mobile format (starting with 6, 7, 8, or 9), unique across all employee accounts.
- **Identity Documents & PII Protection:**
  - Aadhaar, PAN, Passport, and Driver's Licenses are stored with numbers **encrypted at rest (Fernet AES-256)**.
  - Scans are stored outside the public web root.
  - Revealing an unmasked ID number or downloading a scan generates an audited `VIEW_SENSITIVE` log entry.
  - **Retention Policy:** Identity documents of exited employees are automatically purged **90 days after their recorded exit date**.
- **Personal Travel History:** Visual chronological timeline of every trip, mode, hotel stay, and cab journey. Employees see their own; managers see their team's; admins see company-wide with financial costs.

---

## 13. Dashboard, Analytics & Reporting

- **Live Operations Dashboard:** Real-time headcount of field staff deployed across states and campaigns, pending decision bottlenecks, active trips, and critical system alerts.
- **Departmental Analytics:** Trips, personnel, hotel nights, pending requests, and booked spend broken down by department.
- **Cost Analytics:** Financial expenditures filterable by campaign, department, location, and date range, broken down across Travel, Cabs, Hotels, and Employee Reimbursements.
- **Telemetry Reconciliation Reports:** Comparison reports cross-referencing cab meter actual kilometers against vendor billed kilometers to identify excess billing or unapproved detours.
- **Comprehensive Travel Logs:** Complete historical log of all logistical movements, searchable, filterable, and exportable to CSV.

---

## 14. Notifications, Reminders & Security Audit

### 14.1 Communications & Notifications
- **In-App Notification Center:** Real-time notification bell with unread indicators for all users.
- **Email Notifications:** Dispatched via secure SMTP integration.
  - Travelers notified of every decision and booking (with ticket attached; manager in CC).
  - Roommates notified when paired in a co-stay.
  - Managers notified of team requests, cab extensions, and reimbursement claims.
  - Admins notified of manager recommendations, asks to cancel, and vendor option submissions.
  - Finance notified of endorsed reimbursement claims.
  - Super Admin notified of vendor invoices awaiting approval.
- **Automated Reminders:** Departure reminders sent to travelers 2 days before travel; pending decision alerts sent to managers and admins for requests pending over 3 days.
- **Delivery Ledger:** Comprehensive audit log recording recipient, timestamp, channel, subject, delivery status (`SENT`, `FAILED`, `SUPPRESSED`), and server responses.

### 14.2 System Security & Cryptographic Audit Trail
- **Cryptographic Append-Only Activity Log:** Every operational event — request creation, edits, manager L1 approvals, admin L2 approvals, vendor hotel submissions, employee hotel picks, manual check-in/outs, cab odometer captures, finance reimbursement audits, disbursements, and account changes — is recorded in an immutable ledger.
- **Hash Chaining:** Each ledger row contains the cryptographic SHA-256 hash of the preceding row. The integrity of the audit chain can be verified from the admin console.
- **Database Grants:** In production, the database application user is restricted to `INSERT` and `SELECT` only on the `audit_logs` table, preventing retroactive editing or deletion.
- **Network & Host Hardening:** Strict per-IP rate limiting on authentication endpoints, secure HTTP headers, and environment-based secret management.

---

## 15. Platform Architecture & Delivery Specifications

- **Frontend:** Modern responsive single-page application built with React, TypeScript, Tailwind CSS, and Vite.
- **Backend:** High-performance RESTful API engineered with Python FastAPI, Pydantic data schemas, and SQLAlchemy ORM.
- **Database:** MySQL 8 Enterprise Database with automated Alembic schema migrations.
- **Document & Image Storage:** Secure localized storage outside the web root, with cloud storage abstraction ready for Google Cloud Storage (GCS).
- **Deployment:** Multi-container Docker architecture (`docker-compose.yml`), containerized for production on Google Cloud Run or Linux VM hosting.
- **Quality Assurance:** Over 1,000 automated unit, API, and integration test suites, alongside end-to-end database build verification.
