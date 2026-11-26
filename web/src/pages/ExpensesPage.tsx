import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Camera,
  ChevronDown,
  Download,
  IndianRupee,
  Layers,
  MapPin,
  Plus,
  Receipt,
  Search,
  ShieldCheck,
  Upload,
  UserCheck,
} from 'lucide-react';
import toast from 'react-hot-toast';

import { CameraCaptureModal } from '@/components/CameraCaptureModal';
import { TelemetryFilePreview } from '@/components/TelemetryFilePreview';
import { Modal } from '@/components/Modal';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  DateRangeFilter,
  EmptyState,
  Field,
  Input,
  ItemCard,
  ItemList,
  Select,
  tabButtonClass,
} from '@/components/ui';
import {
  auditExpenseClaim,
  createExpenseClaim,
  endorseExpenseClaim,
  exportExpenseClaims,
  fetchExpenseClaims,
  fetchProjects,
  settleExpenseClaim,
  uploadTelemetryFile,
} from '@/lib/api';
import { dayTime } from '@/lib/requests';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import type {
  ExpenseClaim,
  ExpenseStatus,
  ItemDeductionPayload,
  PaymentMode,
  Project,
} from '@/types';

const formatInr = (val: number | string) =>
  `₹${Number(val || 0).toLocaleString('en-IN')}`;

const STATUS_TONES: Record<ExpenseStatus, 'neutral' | 'info' | 'success' | 'danger' | 'warning'> = {
  DRAFT: 'neutral',
  SUBMITTED: 'info',
  MANAGER_ENDORSED: 'info',
  FINANCE_APPROVED: 'success',
  PARTIALLY_APPROVED: 'warning',
  REJECTED: 'danger',
  SETTLED: 'success',
};

export default function ExpensesPage() {
  const qc = useQueryClient();
  const me = useAuth((s) => s.user);

  const isManager = me?.role === 'MANAGER';
  const [scopeTab, setScopeTab] = useState<'TEAM' | 'MINE'>('TEAM');
  const [activeTab, setActiveTab] = useState<'ALL' | 'PENDING' | 'AUDITED' | 'SETTLED'>('ALL');
  const [searchTerm, setSearchTerm] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [endorseModalClaim, setEndorseModalClaim] = useState<ExpenseClaim | null>(null);
  const [auditModalClaim, setAuditModalClaim] = useState<ExpenseClaim | null>(null);
  const [settleModalClaim, setSettleModalClaim] = useState<ExpenseClaim | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const { data: claims = [], isLoading } = useQuery<ExpenseClaim[]>({
    queryKey: ['expense-claims'],
    queryFn: () => fetchExpenseClaims(),
  });

  const { data: projectsData } = useQuery({
    queryKey: ['projects'],
    queryFn: () => fetchProjects({}),
  });
  const projects = projectsData?.items || [];

  const isFinance =
    me?.role === 'FINANCE' || me?.role === 'SUPER_ADMIN' || me?.role === 'SYSTEM_ADMIN';

  const myClaims = claims.filter((c) => c.user_id === me?.id);
  const teamClaims = claims.filter((c) => c.user_id !== me?.id);
  const scopedClaims = isManager ? (scopeTab === 'MINE' ? myClaims : teamClaims) : claims;

  // Filter claims
  const filteredClaims = scopedClaims.filter((c) => {
    if (activeTab === 'PENDING') {
      if (!['SUBMITTED', 'MANAGER_ENDORSED'].includes(c.status)) return false;
    } else if (activeTab === 'AUDITED') {
      if (!['FINANCE_APPROVED', 'PARTIALLY_APPROVED'].includes(c.status)) return false;
    } else if (activeTab === 'SETTLED') {
      if (c.status !== 'SETTLED') return false;
    }

    if (dateFrom || dateTo) {
      const claimStart = c.travel_start_date.slice(0, 10);
      const claimEnd = (c.travel_end_date || c.travel_start_date).slice(0, 10);
      if (dateFrom && claimEnd < dateFrom) return false;
      if (dateTo && claimStart > dateTo) return false;
    }

    if (searchTerm.trim()) {
      const q = searchTerm.toLowerCase();
      const matchCode = c.claim_code.toLowerCase().includes(q);
      const matchUser = c.user_name?.toLowerCase().includes(q);
      const matchCity = c.destination_city.toLowerCase().includes(q);
      const matchProject = c.project_name?.toLowerCase().includes(q);
      if (!matchCode && !matchUser && !matchCity && !matchProject) return false;
    }
    return true;
  });

  const handleExportCsv = async () => {
    try {
      const blob = await exportExpenseClaims();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `sriyatra_expense_claims_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      window.URL.revokeObjectURL(url);
      toast.success('CSV exported successfully');
    } catch {
      toast.error('Failed to export CSV');
    }
  };

  // Metrics (scoped to active tab for Managers)
  const totalClaimedAll = scopedClaims.reduce((acc, c) => acc + (c.total_claimed_amount || 0), 0);
  const totalSettledAll = scopedClaims
    .filter((c) => c.status === 'SETTLED')
    .reduce((acc, c) => acc + (c.total_approved_amount || 0), 0);
  const pendingAuditCount = scopedClaims.filter((c) => c.status === 'MANAGER_ENDORSED').length;
  const pendingEndorseCount = scopedClaims.filter((c) => c.status === 'SUBMITTED').length;
  const teamPendingEndorseCount = teamClaims.filter((c) => c.status === 'SUBMITTED').length;

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-text">
            Post-Travel Expense Reimbursements
          </h2>
          <p className="text-xs text-text-muted mt-0.5">
            Claim and audit out-of-pocket expenses for food, local conveyance, and field incidentals.
          </p>
        </div>

        <div className="flex items-center gap-2">
          {isFinance && (
            <Button
              variant="secondary"
              size="sm"
              onClick={handleExportCsv}
              className="gap-1.5 text-xs"
            >
              <Download size={13} /> Export CSV
            </Button>
          )}

          <Button
            variant="primary"
            size="sm"
            onClick={() => setCreateModalOpen(true)}
            className="gap-1.5 text-xs"
          >
            <Plus size={13} /> Submit New Claim
          </Button>
        </div>
      </div>

      {/* Manager Scope Switcher: Team Claims vs Own Claims */}
      {isManager && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border/80 bg-surface-raised p-1.5 w-fit">
          <button
            type="button"
            onClick={() => {
              setScopeTab('TEAM');
              setExpandedId(null);
            }}
            className={cn(
              tabButtonClass(scopeTab === 'TEAM'),
              'gap-2 rounded-lg px-3.5 py-2',
            )}
          >
            <span>Team Reimbursements</span>
            <Badge tone={teamPendingEndorseCount > 0 ? 'warning' : 'neutral'}>
              {teamClaims.length}
              {teamPendingEndorseCount > 0 ? ` (${teamPendingEndorseCount} pending)` : ''}
            </Badge>
          </button>
          <button
            type="button"
            onClick={() => {
              setScopeTab('MINE');
              setExpandedId(null);
            }}
            className={cn(
              tabButtonClass(scopeTab === 'MINE'),
              'gap-2 rounded-lg px-3.5 py-2',
            )}
          >
            <span>My Reimbursements</span>
            <Badge tone="info">{myClaims.length}</Badge>
          </button>
        </div>
      )}

      {/* KPI Cards */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl border border-border/80 bg-surface/70 p-4 shadow-2xs backdrop-blur-xs">
          <span className="text-2xs font-medium text-text-subtle uppercase tracking-wider">
            {isManager ? (scopeTab === 'MINE' ? 'My Claims Raised' : 'Team Claims Raised') : 'Total Claims Raised'}
          </span>
          <div className="mt-2 flex items-baseline justify-between">
            <span className="text-2xl font-bold text-text">{scopedClaims.length}</span>
            <span className="text-xs font-semibold text-text-muted">{formatInr(totalClaimedAll)}</span>
          </div>
        </div>

        <div className="rounded-xl border border-border/80 bg-surface/70 p-4 shadow-2xs backdrop-blur-xs">
          <span className="text-2xs font-medium text-text-subtle uppercase tracking-wider">
            Pending Manager Review
          </span>
          <div className="mt-2 flex items-baseline justify-between">
            <span className="text-2xl font-bold text-info">{pendingEndorseCount}</span>
            <span className="text-2xs text-text-subtle">Awaiting L1 Endorsement</span>
          </div>
        </div>

        <div className="rounded-xl border border-border/80 bg-surface/70 p-4 shadow-2xs backdrop-blur-xs">
          <span className="text-2xs font-medium text-text-subtle uppercase tracking-wider">
            Pending Finance Audit
          </span>
          <div className="mt-2 flex items-baseline justify-between">
            <span className="text-2xl font-bold text-amber-600 dark:text-amber-400">
              {pendingAuditCount}
            </span>
            <span className="text-2xs text-text-subtle">Awaiting L2 Deduction Audit</span>
          </div>
        </div>

        <div className="rounded-xl border border-border/80 bg-surface/70 p-4 shadow-2xs backdrop-blur-xs">
          <span className="text-2xs font-medium text-text-subtle uppercase tracking-wider">
            Disbursed &amp; Settled
          </span>
          <div className="mt-2 flex items-baseline justify-between">
            <span className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">
              {formatInr(totalSettledAll)}
            </span>
            <span className="text-2xs text-emerald-700 dark:text-emerald-300">Bank UTR Verified</span>
          </div>
        </div>
      </div>

      {/* Main Content Card */}
      <Card>
        <CardHeader
          title={
            isManager
              ? scopeTab === 'MINE'
                ? 'My Reimbursement Claims'
                : 'Team Reimbursement Claims'
              : 'Reimbursement Claims'
          }
          description={
            isManager && scopeTab === 'TEAM'
              ? 'Review and endorse post-travel expense claims submitted by your team members.'
              : 'Track items, receipts, manager endorsement, and finance payout settlements.'
          }
          action={
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search size={13} className="absolute left-2.5 top-2.5 text-text-subtle" />
                <Input
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  placeholder="Search code, name, city..."
                  className="h-8 pl-8 text-xs w-48 sm:w-64"
                />
              </div>

              <div className="flex rounded-lg border border-border/70 p-0.5 bg-surface-raised text-xs">
                {(['ALL', 'PENDING', 'AUDITED', 'SETTLED'] as const).map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    onClick={() => setActiveTab(tab)}
                    className={tabButtonClass(activeTab === tab)}
                  >
                    {tab === 'ALL' && 'All'}
                    {tab === 'PENDING' && 'Pending'}
                    {tab === 'AUDITED' && 'Audited'}
                    {tab === 'SETTLED' && 'Settled'}
                  </button>
                ))}
              </div>
              <DateRangeFilter
                from={dateFrom}
                to={dateTo}
                onFromChange={setDateFrom}
                onToChange={setDateTo}
                onClear={() => {
                  setDateFrom('');
                  setDateTo('');
                }}
              />
            </div>
          }
        />

        {isLoading ? (
          <div className="p-8 text-center text-xs text-text-muted">Loading expense claims...</div>
        ) : filteredClaims.length === 0 ? (
          <EmptyState
            title="No expense claims found"
            description="Submit out-of-pocket expenses incurred on field deployments to receive reimbursement."
          />
        ) : (
          <ItemList>
            {filteredClaims.map((claim) => {
              const isExpanded = expandedId === claim.id;
              const canEndorse =
                claim.status === 'SUBMITTED' && (me?.id === claim.manager_id || isFinance);
              const canAudit =
                ['MANAGER_ENDORSED', 'SUBMITTED'].includes(claim.status) && isFinance;
              const canSettle =
                ['FINANCE_APPROVED', 'PARTIALLY_APPROVED'].includes(claim.status) && isFinance;

              return (
                <ItemCard key={claim.id} className="transition-all">
                  <div
                    className="flex cursor-pointer flex-wrap items-center justify-between gap-3 p-4"
                    onClick={() => setExpandedId(isExpanded ? null : claim.id)}
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-surface-raised border border-border/60 text-primary">
                        <Receipt size={18} />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-sm text-text">{claim.claim_code}</span>
                          <Badge tone={STATUS_TONES[claim.status]}>{claim.status.replace('_', ' ')}</Badge>
                          {claim.manager_endorsed ? (
                            <Badge tone="success">
                              <UserCheck size={11} className="mr-1 inline" />
                              Mgr Endorsed
                            </Badge>
                          ) : claim.manager_reviewed_at ? (
                            <Badge tone="danger">
                              <UserCheck size={11} className="mr-1 inline" />
                              Mgr Rejected
                            </Badge>
                          ) : null}
                          {claim.is_food_cap_exceeded && (
                            <span className="inline-flex items-center gap-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-2xs font-medium text-amber-800 dark:text-amber-300">
                              <AlertTriangle size={10} /> Food +{formatInr(claim.food_excess_amount || 0)}
                            </span>
                          )}
                          {claim.is_unbilled_transit_exceeded && (
                            <span className="inline-flex items-center gap-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-2xs font-medium text-amber-800 dark:text-amber-300">
                              <AlertTriangle size={10} /> Transit +{formatInr(claim.unbilled_transit_excess_amount || 0)}
                            </span>
                          )}
                        </div>

                        <div className="mt-1 flex flex-wrap items-center gap-2 text-2xs text-text-muted">
                          <span className="font-medium text-text">{claim.user_name || 'Ground Staff'}</span>
                          <span>·</span>
                          <span>{claim.project_name || 'Campaign'}</span>
                          <span>·</span>
                          <span className="flex items-center gap-0.5">
                            <MapPin size={10} /> {claim.destination_city}
                          </span>
                          <span>·</span>
                          <span>
                            {claim.travel_start_date} to {claim.travel_end_date}
                          </span>
                          {claim.expense_type && (
                            <>
                              <span>·</span>
                              <Badge tone="neutral">
                                {claim.expense_type === 'OTHERS'
                                  ? claim.expense_type_other || 'Others'
                                  : claim.expense_type}
                              </Badge>
                            </>
                          )}
                          {claim.employee_payment_mode && (
                            <Badge tone="info">{claim.employee_payment_mode}</Badge>
                          )}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-4">
                      <div className="text-right">
                        <span className="block text-sm font-bold text-text">
                          {formatInr(claim.total_claimed_amount)}
                        </span>
                        {claim.total_approved_amount > 0 && claim.status !== 'SUBMITTED' && (
                          <span className="block text-2xs font-semibold text-emerald-600 dark:text-emerald-400">
                            Approved: {formatInr(claim.total_approved_amount)}
                          </span>
                        )}
                        {claim.total_deducted_amount > 0 && (
                          <span className="block text-2xs font-medium text-danger">
                            Deducted: {formatInr(claim.total_deducted_amount)}
                          </span>
                        )}
                      </div>

                      <ChevronDown
                        size={16}
                        className={`text-text-subtle transition-transform ${
                          isExpanded ? 'rotate-180' : ''
                        }`}
                      />
                    </div>
                  </div>

                  {/* Expanded Itemized Lines & Action Bar */}
                  {isExpanded && (
                    <div className="border-t border-border/60 bg-surface-sunken p-4 space-y-4">
                      {/* Action buttons */}
                      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/40 pb-3">
                        <span className="text-xs font-semibold text-text flex items-center gap-1.5">
                          <Layers size={13} className="text-primary" />
                          Itemized Claim Breakdown ({claim.items?.length || 0} items)
                        </span>

                        <div className="flex items-center gap-2">
                          {canEndorse && (
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={(e) => {
                                e.stopPropagation();
                                setEndorseModalClaim(claim);
                              }}
                              className="text-xs gap-1"
                            >
                              <UserCheck size={13} /> Manager Endorse
                            </Button>
                          )}

                          {canAudit && (
                            <Button
                              size="sm"
                              variant="primary"
                              onClick={(e) => {
                                e.stopPropagation();
                                setAuditModalClaim(claim);
                              }}
                              className="text-xs gap-1"
                            >
                              <ShieldCheck size={13} /> Finance Audit
                            </Button>
                          )}

                          {canSettle && (
                            <Button
                              size="sm"
                              variant="primary"
                              onClick={(e) => {
                                e.stopPropagation();
                                setSettleModalClaim(claim);
                              }}
                              className="text-xs gap-1 bg-emerald-600 hover:bg-emerald-700 text-white"
                            >
                              <IndianRupee size={13} /> Settle Payout (UTR)
                            </Button>
                          )}
                        </div>
                      </div>

                      {/* Claim Details & Employee Payout Information */}
                      <div className="grid gap-3 sm:grid-cols-2 rounded-lg border border-border/60 bg-surface p-3 text-xs">
                        <div className="space-y-1">
                          <span className="text-2xs font-bold uppercase tracking-wider text-text-subtle">
                            Claim Information
                          </span>
                          <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-2xs pt-1">
                            <span className="text-text-muted">Payment Mode:</span>
                            <span className="font-semibold text-text">{claim.employee_payment_mode || '—'}</span>
                            <span className="text-text-muted">Expense Type:</span>
                            <span className="font-semibold text-text">
                              {claim.expense_type === 'OTHERS'
                                ? claim.expense_type_other || 'Others'
                                : claim.expense_type || '—'}
                            </span>
                            <span className="text-text-muted">Remarks:</span>
                            <span className="font-normal text-text">{claim.remarks || '—'}</span>
                            {claim.attachment_url && (
                              <div className="col-span-2 pt-1 border-t border-border/50">
                                <span className="text-text-muted block text-2xs mb-1">Attached Bill / Receipt:</span>
                                <TelemetryFilePreview path={claim.attachment_url} name="Receipt" />
                              </div>
                            )}
                          </div>
                        </div>

                        <div className="space-y-1 sm:border-l sm:border-border/50 sm:pl-3">
                          <span className="text-2xs font-bold uppercase tracking-wider text-text-subtle">
                            Employee Payout Account (Receive Money)
                          </span>
                          <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-2xs pt-1">
                            <span className="text-text-muted">UPI ID:</span>
                            <span className="font-mono font-semibold text-primary">{claim.upi_id || '—'}</span>
                            <span className="text-text-muted">Bank Name:</span>
                            <span className="font-semibold text-text">{claim.bank_name || '—'}</span>
                            <span className="text-text-muted">Bank A/c No.:</span>
                            <span className="font-mono font-semibold text-text">{claim.bank_account_number || '—'}</span>
                            <span className="text-text-muted">A/c Holder:</span>
                            <span className="font-semibold text-text">{claim.account_holder_name || '—'}</span>
                            <span className="text-text-muted">IFSC Code:</span>
                            <span className="font-mono text-text">{claim.ifsc_code || '—'}</span>
                          </div>
                        </div>
                      </div>

                      {/* Items table */}
                      <div className="overflow-x-auto rounded-lg border border-border/60 bg-surface">
                        <table className="w-full text-left text-xs">
                          <thead className="border-b border-border/60 bg-surface-raised text-2xs uppercase tracking-wider text-text-subtle">
                            <tr>
                              <th className="px-3 py-2">Date</th>
                              <th className="px-3 py-2">Category</th>
                              <th className="px-3 py-2">Merchant / Vendor</th>
                              <th className="px-3 py-2">Bill No.</th>
                              <th className="px-3 py-2 text-right">Claimed</th>
                              <th className="px-3 py-2 text-right">Approved</th>
                              <th className="px-3 py-2">Deduction Reason</th>
                              <th className="px-3 py-2 text-center">Receipt</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-border/40">
                            {claim.items.map((it) => (
                              <tr key={it.id} className="hover:bg-surface-raised/40">
                                <td className="px-3 py-2 font-medium">{it.expense_date}</td>
                                <td className="px-3 py-2 text-text-muted">
                                  {it.category === 'FOOD' && 'Food (₹400 cap)'}
                                  {it.category === 'LOCAL_TRANSIT' && 'Local Transit'}
                                  {it.category === 'MISCELLANEOUS' && 'Miscellaneous'}
                                </td>
                                <td className="px-3 py-2 font-medium text-text">
                                  {it.vendor_merchant_name}
                                </td>
                                <td className="px-3 py-2 text-text-subtle">{it.bill_number || '—'}</td>
                                <td className="px-3 py-2 text-right font-bold text-text">
                                  {formatInr(it.claimed_amount)}
                                </td>
                                <td className="px-3 py-2 text-right font-semibold text-emerald-600 dark:text-emerald-400">
                                  {formatInr(it.approved_amount)}
                                </td>
                                <td className="px-3 py-2 text-2xs text-danger">
                                  {it.deduction_reason || '—'}
                                </td>
                                <td className="px-3 py-2 text-center">
                                  {it.receipt_url ? (
                                    <TelemetryFilePreview path={it.receipt_url} compact name="Receipt" />
                                  ) : (
                                    <span className="text-2xs text-text-subtle italic">No receipt</span>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>

                      {/* Audit & Settlement Footnote */}
                      <div className="grid gap-2 sm:grid-cols-2 text-2xs text-text-muted pt-2 border-t border-border/40">
                        {claim.manager_reviewed_at && (
                          <div className="flex items-center gap-1.5">
                            <UserCheck size={12} className="text-emerald-600" />
                            <span>
                              Manager Endorsed by <strong>{claim.manager_name}</strong> on{' '}
                              {dayTime(claim.manager_reviewed_at)}
                              {claim.manager_notes && ` ("${claim.manager_notes}")`}
                            </span>
                          </div>
                        )}

                        {claim.finance_audited_at && (
                          <div className="flex items-center gap-1.5">
                            <ShieldCheck size={12} className="text-primary" />
                            <span>
                              Audited by <strong>{claim.finance_auditor_name}</strong> on{' '}
                              {dayTime(claim.finance_audited_at)}
                              {claim.finance_deduction_notes && ` ("${claim.finance_deduction_notes}")`}
                            </span>
                          </div>
                        )}

                        {claim.settled_at && (
                          <div className="flex items-center gap-1.5 sm:col-span-2 text-emerald-800 dark:text-emerald-300 font-medium">
                            <IndianRupee size={12} />
                            <span>
                              Settled via <strong>{claim.payment_mode}</strong> with Bank UTR:{' '}
                              <strong>{claim.bank_utr_number}</strong> on {claim.payment_date} by{' '}
                              {claim.settled_by_name}
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </ItemCard>
              );
            })}
          </ItemList>
        )}
      </Card>

      {/* Submit New Claim Modal */}
      {createModalOpen && (
        <CreateExpenseClaimModal
          projects={projects}
          existingClaims={claims}
          isOpen={createModalOpen}
          onClose={() => setCreateModalOpen(false)}
          onSuccess={() => {
            setCreateModalOpen(false);
            qc.invalidateQueries({ queryKey: ['expense-claims'] });
          }}
        />
      )}

      {/* Manager Endorse Modal */}
      {endorseModalClaim && (
        <ManagerEndorseModal
          claim={endorseModalClaim}
          isOpen={Boolean(endorseModalClaim)}
          onClose={() => setEndorseModalClaim(null)}
          onSuccess={() => {
            setEndorseModalClaim(null);
            qc.invalidateQueries({ queryKey: ['expense-claims'] });
          }}
        />
      )}

      {/* Finance Audit Modal */}
      {auditModalClaim && (
        <FinanceAuditModal
          claim={auditModalClaim}
          isOpen={Boolean(auditModalClaim)}
          onClose={() => setAuditModalClaim(null)}
          onSuccess={() => {
            setAuditModalClaim(null);
            qc.invalidateQueries({ queryKey: ['expense-claims'] });
          }}
        />
      )}

      {/* Finance Settle Modal */}
      {settleModalClaim && (
        <SettleClaimModal
          claim={settleModalClaim}
          isOpen={Boolean(settleModalClaim)}
          onClose={() => setSettleModalClaim(null)}
          onSuccess={() => {
            setSettleModalClaim(null);
            qc.invalidateQueries({ queryKey: ['expense-claims'] });
          }}
        />
      )}
    </div>
  );
}

function CreateExpenseClaimModal({
  projects,
  existingClaims,
  isOpen,
  onClose,
  onSuccess,
}: {
  projects: Project[];
  existingClaims: ExpenseClaim[];
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const me = useAuth((s) => s.user);
  const todayStr = new Date().toISOString().slice(0, 10);
  const [projectId, setProjectId] = useState<number | ''>(projects[0]?.id || '');
  const [city, setCity] = useState('');
  const [startDate, setStartDate] = useState(todayStr);
  const [endDate, setEndDate] = useState(todayStr);

  const [paymentMode, setPaymentMode] = useState<'ONLINE' | 'CASH'>('ONLINE');
  const [expenseType, setExpenseType] = useState<'FOOD' | 'TRAVEL' | 'OTHERS'>('FOOD');
  const [expenseTypeOther, setExpenseTypeOther] = useState('');
  const [totalAmount, setTotalAmount] = useState<string>('500');
  const [remarks, setRemarks] = useState('');

  // Employee payout details (receiving money)
  const [upiId, setUpiId] = useState(me?.upi_id || '');
  const [bankName, setBankName] = useState(me?.bank_name || '');
  const [bankAccountNumber, setBankAccountNumber] = useState(me?.bank_account_number || '');
  const [accountHolderName, setAccountHolderName] = useState(
    me?.account_holder_name || me?.full_name || '',
  );
  const [ifscCode, setIfscCode] = useState(me?.ifsc_code || '');

  // Attachment (images, PDFs)
  const [attachmentUrl, setAttachmentUrl] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [cameraModalOpen, setCameraModalOpen] = useState(false);

  // Check for date overlaps with employee's previous claims (multi-day claim guard)
  const overlappingClaim = existingClaims.find(
    (c) =>
      c.user_id === me?.id &&
      c.status !== 'REJECTED' &&
      startDate &&
      endDate &&
      c.travel_start_date <= endDate &&
      c.travel_end_date >= startDate,
  );

  const handleFileUpload = async (file: File) => {
    try {
      setIsUploading(true);
      const res = await uploadTelemetryFile(file);
      setAttachmentUrl(res.file_path);
      toast.success('Document uploaded successfully');
    } catch {
      toast.error('Failed to upload document');
    } finally {
      setIsUploading(false);
    }
  };

  const numAmount = parseFloat(totalAmount) || 0;
  const isFormValid =
    Boolean(projectId) &&
    city.trim().length > 0 &&
    Boolean(startDate) &&
    Boolean(endDate) &&
    endDate >= startDate &&
    !overlappingClaim &&
    numAmount > 0 &&
    (expenseType !== 'OTHERS' || expenseTypeOther.trim().length > 0);

  const createMutation = useMutation({
    mutationFn: () =>
      createExpenseClaim({
        project_id: Number(projectId),
        destination_city: city.trim(),
        travel_start_date: startDate,
        travel_end_date: endDate,
        employee_payment_mode: paymentMode,
        expense_type: expenseType,
        expense_type_other: expenseType === 'OTHERS' ? expenseTypeOther.trim() : null,
        remarks: remarks.trim() || null,
        upi_id: upiId.trim() || null,
        bank_name: bankName.trim() || null,
        bank_account_number: bankAccountNumber.trim() || null,
        account_holder_name: accountHolderName.trim() || null,
        ifsc_code: ifscCode.trim() || null,
        attachment_url: attachmentUrl || null,
        total_amount: numAmount,
      }),
    onSuccess: (res) => {
      toast.success(`Expense claim ${res.claim_code} submitted successfully!`);
      onSuccess();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to submit claim');
    },
  });

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title="Submit Post-Travel Expense Reimbursement Claim"
      description="Claim out-of-pocket expenses for food, travel, or other multi-day tour expenses."
      className="max-w-2xl"
    >
      <div className="space-y-4 max-h-[70vh] overflow-y-auto px-1">
        {/* Date Overlap Alert */}
        {overlappingClaim && (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-800 dark:text-amber-300 flex items-start gap-2">
            <AlertTriangle size={16} className="shrink-0 mt-0.5 text-amber-600" />
            <div>
              <span className="font-semibold">Selected Dates Overlap Detected:</span>
              <p className="mt-0.5">
                The selected dates ({startDate} to {endDate}) overlap with your previous claim{' '}
                <strong>{overlappingClaim.claim_code}</strong> ({overlappingClaim.travel_start_date} to{' '}
                {overlappingClaim.travel_end_date}). Selected dates should not overlap with previous claims.
              </p>
            </div>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Campaign Project" required htmlFor="claim-proj">
            <Select
              id="claim-proj"
              value={String(projectId)}
              onChange={(e) => setProjectId(Number(e.target.value))}
            >
              <option value="">Select campaign project...</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.code})
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Destination City" required htmlFor="claim-city">
            <Input
              id="claim-city"
              value={city}
              onChange={(e) => setCity(e.target.value)}
              placeholder="e.g. Mumbai, Bengaluru, Delhi"
            />
          </Field>

          <Field label="Select your Expense Date (Starting)" required htmlFor="claim-start">
            <Input
              id="claim-start"
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </Field>

          <Field label="Select your Expense Date (End)" required htmlFor="claim-end">
            <Input
              id="claim-end"
              type="date"
              value={endDate}
              min={startDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </Field>

          <Field label="Payment Mode (How did you pay?)" required htmlFor="claim-pay-mode">
            <Select
              id="claim-pay-mode"
              value={paymentMode}
              onChange={(e) => setPaymentMode(e.target.value as 'ONLINE' | 'CASH')}
            >
              <option value="ONLINE">Online Payment (UPI / Card / Net Banking)</option>
              <option value="CASH">Cash Payment</option>
            </Select>
          </Field>

          <Field label="Please Select Type of Expense" required htmlFor="claim-exp-type">
            <Select
              id="claim-exp-type"
              value={expenseType}
              onChange={(e) =>
                setExpenseType(e.target.value as 'FOOD' | 'TRAVEL' | 'OTHERS')
              }
            >
              <option value="FOOD">Food (Daily meals &amp; refreshments)</option>
              <option value="TRAVEL">Travel (Local transit / Auto / Taxi)</option>
              <option value="OTHERS">Others (Specify below)</option>
            </Select>
          </Field>

          {expenseType === 'OTHERS' && (
            <Field label="Specify Other Expense Type" required htmlFor="claim-exp-other" className="sm:col-span-2">
              <Input
                id="claim-exp-other"
                value={expenseTypeOther}
                onChange={(e) => setExpenseTypeOther(e.target.value)}
                placeholder="e.g. SIM card recharge, printing, campaign supplies"
              />
            </Field>
          )}

          <Field label="Total Claim Amount (₹)" required htmlFor="claim-amt">
            <Input
              id="claim-amt"
              type="number"
              min="1"
              step="1"
              value={totalAmount}
              onChange={(e) => setTotalAmount(e.target.value)}
              placeholder="e.g. 3500"
            />
          </Field>

          <Field label="Attachment (Bill / Receipt / PDF)" htmlFor="claim-attachment">
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => setCameraModalOpen(true)}
                  disabled={isUploading}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface text-xs font-medium cursor-pointer hover:bg-surface-raised transition-colors text-text"
                >
                  <Camera size={14} className="text-primary" />
                  <span>Capture via Camera</span>
                </button>

                <label className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface text-xs font-medium cursor-pointer hover:bg-surface-raised transition-colors">
                  <Upload size={14} className="text-text-subtle" />
                  <span>Upload from Files</span>
                  <input
                    type="file"
                    accept="image/*,application/pdf,.doc,.docx"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) handleFileUpload(file);
                    }}
                    disabled={isUploading}
                  />
                </label>
              </div>

              {attachmentUrl && (
                <div className="pt-2">
                  <div className="flex items-center gap-1.5 text-2xs font-semibold text-emerald-600 mb-1.5">
                    <span>✓ Document Attached Successfully</span>
                  </div>
                  <TelemetryFilePreview path={attachmentUrl} name="Attached Receipt" />
                </div>
              )}

              {cameraModalOpen && (
                <CameraCaptureModal
                  open={cameraModalOpen}
                  onClose={() => setCameraModalOpen(false)}
                  title="Capture Receipt / Bill Photo"
                  description="Align your expense receipt or tax invoice in the camera frame and tap to capture."
                  onCapture={(file) => handleFileUpload(file)}
                />
              )}
            </div>
          </Field>

          <Field label="Remarks" htmlFor="claim-remarks" className="sm:col-span-2">
            <Input
              id="claim-remarks"
              value={remarks}
              onChange={(e) => setRemarks(e.target.value)}
              placeholder="Enter remarks or justification for this multi-day expense claim..."
            />
          </Field>
        </div>

        {/* Employee Receiving Account Details */}
        <div className="rounded-xl border border-border/70 bg-surface-raised/60 p-3.5 space-y-3">
          <div>
            <h5 className="text-xs font-semibold text-text">
              Employee Payout Details (Receive Money)
            </h5>
            <p className="text-2xs text-text-muted mt-0.5">
              Company Finance will disburse your reimbursement directly to this UPI ID or Bank Account.
            </p>
          </div>

          <div className="grid gap-2.5 sm:grid-cols-2">
            <Field label="Enter Your UPI Payment ID" htmlFor="claim-upi" className="sm:col-span-2">
              <Input
                id="claim-upi"
                value={upiId}
                onChange={(e) => setUpiId(e.target.value)}
                placeholder="e.g. name@okhdfcbank or 9876543210@paytm (employee receives money on this ID)"
              />
            </Field>

            <Field label="Bank Name" htmlFor="claim-bank-name">
              <Input
                id="claim-bank-name"
                value={bankName}
                onChange={(e) => setBankName(e.target.value)}
                placeholder="e.g. HDFC Bank, SBI, ICICI"
              />
            </Field>

            <Field label="Bank Ac Number" htmlFor="claim-bank-ac">
              <Input
                id="claim-bank-ac"
                value={bankAccountNumber}
                onChange={(e) => setBankAccountNumber(e.target.value)}
                placeholder="e.g. 50100234567890"
              />
            </Field>

            <Field label="Account Holder Name" htmlFor="claim-holder">
              <Input
                id="claim-holder"
                value={accountHolderName}
                onChange={(e) => setAccountHolderName(e.target.value)}
                placeholder="e.g. Priya Sharma"
              />
            </Field>

            <Field label="Enter IFSC Code (optional)" htmlFor="claim-ifsc">
              <Input
                id="claim-ifsc"
                value={ifscCode}
                onChange={(e) => setIfscCode(e.target.value.toUpperCase())}
                placeholder="e.g. HDFC0001234"
              />
            </Field>
          </div>
        </div>
      </div>

      <div className="mt-4 flex items-center justify-end gap-2 border-t border-border/50 pt-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={!isFormValid || createMutation.isPending}
          loading={createMutation.isPending}
          onClick={() => createMutation.mutate()}
        >
          Submit Reimbursement Claim ({formatInr(numAmount)})
        </Button>
      </div>
    </Modal>
  );
}

function ManagerEndorseModal({
  claim,
  isOpen,
  onClose,
  onSuccess,
}: {
  claim: ExpenseClaim;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [notes, setNotes] = useState('');

  const endorseMutation = useMutation({
    mutationFn: (endorsed: boolean) =>
      endorseExpenseClaim(claim.id, { endorsed, notes: notes || null }),
    onSuccess: (res) => {
      toast.success(
        res.status === 'MANAGER_ENDORSED'
          ? 'Claim endorsed and routed to Finance Desk'
          : 'Claim rejected',
      );
      onSuccess();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to review claim');
    },
  });

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title="Manager L1 Reimbursement Endorsement"
      description={`Review ${claim.user_name}'s claim ${claim.claim_code} totaling ${formatInr(claim.total_claimed_amount)}.`}
      className="max-w-md"
    >
      <div className="space-y-3">
        <Field label="Manager Review Notes" htmlFor="mgr-notes">
          <Input
            id="mgr-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. Verified campaign deployment and work travel."
          />
        </Field>
      </div>

      <div className="mt-4 flex items-center justify-end gap-2 border-t border-border/50 pt-3">
        <Button
          variant="danger"
          size="sm"
          loading={endorseMutation.isPending}
          onClick={() => endorseMutation.mutate(false)}
        >
          Reject Claim
        </Button>
        <Button
          variant="primary"
          size="sm"
          loading={endorseMutation.isPending}
          onClick={() => endorseMutation.mutate(true)}
        >
          Endorse &amp; Forward to Finance
        </Button>
      </div>
    </Modal>
  );
}

function FinanceAuditModal({
  claim,
  isOpen,
  onClose,
  onSuccess,
}: {
  claim: ExpenseClaim;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [notes, setNotes] = useState('');
  const [deductions, setDeductions] = useState<Record<number, { amount: number; reason: string }>>(
    {},
  );

  const auditMutation = useMutation({
    mutationFn: (decision: ExpenseStatus) => {
      const deductionList: ItemDeductionPayload[] = Object.entries(deductions)
        .filter(([_, d]) => d.amount > 0)
        .map(([idStr, d]) => ({
          item_id: Number(idStr),
          deducted_amount: d.amount,
          deduction_reason: d.reason || 'Deducted per corporate travel cap limits.',
        }));

      return auditExpenseClaim(claim.id, {
        decision,
        deductions: deductionList,
        notes: notes || null,
      });
    },
    onSuccess: (res) => {
      toast.success(`Claim audit complete: ${res.status.replace('_', ' ')}`);
      onSuccess();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to audit claim');
    },
  });

  const setItemDeduction = (itemId: number, patch: Partial<{ amount: number; reason: string }>) => {
    setDeductions((prev) => ({
      ...prev,
      [itemId]: {
        amount: patch.amount ?? prev[itemId]?.amount ?? 0,
        reason: patch.reason ?? prev[itemId]?.reason ?? '',
      },
    }));
  };

  const totalDeducted = Object.values(deductions).reduce((acc, d) => acc + (d.amount || 0), 0);
  const netApproved = Math.max(0, claim.total_claimed_amount - totalDeducted);

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title="Finance Reimbursement Audit Desk"
      description={`Audit receipts, apply item-level deductions, and approve payout for ${claim.claim_code}.`}
      className="max-w-2xl"
    >
      <div className="space-y-4 max-h-[70vh] overflow-y-auto px-1">
        <div className="flex items-center justify-between rounded-lg bg-surface-raised p-3 text-xs border border-border/50">
          <div>
            <span className="text-text-muted">Total Claimed: </span>
            <span className="font-bold text-text">{formatInr(claim.total_claimed_amount)}</span>
          </div>
          <div>
            <span className="text-text-muted">Total Deductions: </span>
            <span className="font-bold text-danger">{formatInr(totalDeducted)}</span>
          </div>
          <div>
            <span className="text-text-muted">Net Approved: </span>
            <span className="font-bold text-emerald-600">{formatInr(netApproved)}</span>
          </div>
        </div>

        {/* Manager (L1) Endorsement Card */}
        <div
          className={cn(
            'rounded-lg border p-3 text-xs space-y-1.5',
            claim.manager_endorsed
              ? 'border-emerald-500/40 bg-emerald-50/50 dark:bg-emerald-950/20'
              : claim.manager_reviewed_at
                ? 'border-danger/40 bg-danger-soft'
                : 'border-warning/40 bg-warning-soft',
          )}
        >
          <div className="flex items-center justify-between">
            <span className="text-2xs font-bold uppercase tracking-wider text-text-subtle flex items-center gap-1.5">
              <UserCheck
                size={13}
                className={claim.manager_endorsed ? 'text-emerald-600' : 'text-text-muted'}
              />
              Manager (L1) Recommendation
            </span>
            <Badge tone={claim.manager_endorsed ? 'success' : claim.manager_reviewed_at ? 'danger' : 'warning'}>
              {claim.manager_endorsed
                ? 'Endorsed by Manager'
                : claim.manager_reviewed_at
                  ? 'Rejected by Manager'
                  : 'Awaiting Manager Review'}
            </Badge>
          </div>
          <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-2xs pt-1">
            <div>
              <span className="text-text-muted">Reviewed By: </span>
              <span className="font-semibold text-text">{claim.manager_name || 'Direct Manager'}</span>
            </div>
            <div>
              <span className="text-text-muted">Review Time: </span>
              <span className="font-semibold text-text">
                {claim.manager_reviewed_at ? dayTime(claim.manager_reviewed_at) : 'Pending'}
              </span>
            </div>
            <div className="col-span-2 pt-0.5">
              <span className="text-text-muted">Manager Remarks / Notes: </span>
              <span className="font-medium text-text">
                {claim.manager_notes ? `"${claim.manager_notes}"` : 'No remarks provided.'}
              </span>
            </div>
          </div>
        </div>

        {/* Claim & Payout Info for Audit */}
        <div className="rounded-lg border border-border/60 bg-surface p-3 text-xs space-y-1.5">
          <span className="text-2xs font-bold uppercase tracking-wider text-text-subtle">
            Claim Summary &amp; Employee Payout Account
          </span>
          <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-2xs">
            <div>
              <span className="text-text-muted">Payment Mode: </span>
              <span className="font-semibold text-text">{claim.employee_payment_mode || '—'}</span>
            </div>
            <div>
              <span className="text-text-muted">Expense Type: </span>
              <span className="font-semibold text-text">
                {claim.expense_type === 'OTHERS'
                  ? claim.expense_type_other || 'Others'
                  : claim.expense_type || '—'}
              </span>
            </div>
            <div className="col-span-2">
              <span className="text-text-muted">Remarks: </span>
              <span className="text-text">{claim.remarks || 'None'}</span>
            </div>
            {claim.attachment_url && (
              <div className="col-span-2 pt-1 border-t border-border/40">
                <span className="text-text-muted block text-2xs mb-1 font-semibold">Attached Bill / Receipt: </span>
                <TelemetryFilePreview path={claim.attachment_url} name="Attached Expense Bill" />
              </div>
            )}
            <div className="col-span-2 pt-1 border-t border-border/40 grid grid-cols-2 gap-x-2 gap-y-1">
              <div>
                <span className="text-text-muted">UPI ID: </span>
                <span className="font-mono font-bold text-primary">{claim.upi_id || '—'}</span>
              </div>
              <div>
                <span className="text-text-muted">Bank Name: </span>
                <span className="font-semibold text-text">{claim.bank_name || '—'}</span>
              </div>
              <div>
                <span className="text-text-muted">Bank A/c: </span>
                <span className="font-mono font-semibold text-text">{claim.bank_account_number || '—'}</span>
              </div>
              <div>
                <span className="text-text-muted">IFSC: </span>
                <span className="font-mono text-text">{claim.ifsc_code || '—'}</span>
              </div>
              <div className="col-span-2">
                <span className="text-text-muted">A/c Holder: </span>
                <span className="font-semibold text-text">{claim.account_holder_name || claim.user_name}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Item deduction inputs */}
        <div className="space-y-3">
          <h5 className="text-xs font-semibold text-text">Audit Item Lines &amp; Deductions</h5>
          {claim.items.map((it) => {
            const currentDed = deductions[it.id] || { amount: 0, reason: '' };
            return (
              <div
                key={it.id}
                className="rounded-xl border border-border/60 bg-surface/70 p-3 space-y-2 text-xs"
              >
                <div className="flex items-baseline justify-between">
                  <div>
                    <span className="font-semibold text-text">{it.vendor_merchant_name}</span>
                    <span className="text-2xs text-text-subtle ml-2">
                      ({it.category.replace('_', ' ')} · {it.expense_date})
                    </span>
                  </div>
                  <span className="font-bold text-text">{formatInr(it.claimed_amount)}</span>
                </div>

                <div className="grid gap-2 sm:grid-cols-2">
                  <Field label="Deduct Amount (₹)" htmlFor={`ded-${it.id}`}>
                    <Input
                      id={`ded-${it.id}`}
                      type="number"
                      min="0"
                      max={it.claimed_amount}
                      value={currentDed.amount || ''}
                      onChange={(e) =>
                        setItemDeduction(it.id, {
                          amount: Math.min(it.claimed_amount, parseFloat(e.target.value) || 0),
                        })
                      }
                      placeholder="0.00"
                    />
                  </Field>

                  <Field label="Deduction Reason" htmlFor={`reason-${it.id}`}>
                    <Input
                      id={`reason-${it.id}`}
                      value={currentDed.reason}
                      onChange={(e) => setItemDeduction(it.id, { reason: e.target.value })}
                      placeholder="e.g. Exceeds ₹150 unbilled cap"
                    />
                  </Field>
                </div>
              </div>
            );
          })}
        </div>

        <Field label="Finance Audit Notes" htmlFor="aud-notes">
          <Input
            id="aud-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. Audited against hotel stay verification and policy limits."
          />
        </Field>
      </div>

      <div className="mt-4 flex items-center justify-end gap-2 border-t border-border/50 pt-3">
        <Button
          variant="danger"
          size="sm"
          loading={auditMutation.isPending}
          onClick={() => auditMutation.mutate('REJECTED')}
        >
          Reject Claim
        </Button>
        <Button
          variant="primary"
          size="sm"
          loading={auditMutation.isPending}
          onClick={() =>
            auditMutation.mutate(totalDeducted > 0 ? 'PARTIALLY_APPROVED' : 'FINANCE_APPROVED')
          }
        >
          {totalDeducted > 0
            ? `Partially Approve (${formatInr(netApproved)})`
            : `Full Approve (${formatInr(claim.total_claimed_amount)})`}
        </Button>
      </div>
    </Modal>
  );
}

function SettleClaimModal({
  claim,
  isOpen,
  onClose,
  onSuccess,
}: {
  claim: ExpenseClaim;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const todayStr = new Date().toISOString().slice(0, 10);
  const [mode, setMode] = useState<PaymentMode>('NEFT');
  const [payDate, setPayDate] = useState(todayStr);
  const [utr, setUtr] = useState('');

  const settleMutation = useMutation({
    mutationFn: () =>
      settleExpenseClaim(claim.id, {
        payment_mode: mode,
        payment_date: payDate,
        bank_utr_number: utr.trim(),
      }),
    onSuccess: () => {
      toast.success(`Claim ${claim.claim_code} marked as SETTLED!`);
      onSuccess();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to settle claim');
    },
  });

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title="Record Bank Disbursement Payout"
      description={`Record final bank payout for ${claim.claim_code} to ${claim.user_name} (${formatInr(claim.total_approved_amount)}).`}
      className="max-w-md"
    >
      <div className="space-y-3">
        {/* Beneficiary Details */}
        <div className="rounded-lg border border-border/60 bg-surface-raised p-3 text-xs space-y-1">
          <span className="text-2xs font-bold uppercase tracking-wider text-text-subtle">
            Beneficiary Payout Details
          </span>
          <div className="text-2xs space-y-1 pt-1">
            <div className="flex justify-between">
              <span className="text-text-muted">UPI ID:</span>
              <span className="font-mono font-bold text-primary">{claim.upi_id || 'Not specified'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">Bank Name:</span>
              <span className="font-medium text-text">{claim.bank_name || 'Not specified'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">Bank A/c No:</span>
              <span className="font-mono font-bold text-text">{claim.bank_account_number || 'Not specified'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">A/c Holder:</span>
              <span className="font-medium text-text">{claim.account_holder_name || claim.user_name}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">IFSC Code:</span>
              <span className="font-mono text-text">{claim.ifsc_code || '—'}</span>
            </div>
          </div>
        </div>

        <Field label="Payment Mode" required htmlFor="pay-mode">
          <Select id="pay-mode" value={mode} onChange={(e) => setMode(e.target.value as PaymentMode)}>
            <option value="NEFT">NEFT Bank Transfer</option>
            <option value="RTGS">RTGS Fast Transfer</option>
            <option value="UPI">UPI Instant Payment</option>
            <option value="CHEQUE">Bank Cheque</option>
            <option value="OTHER">Other</option>
          </Select>
        </Field>

        <Field label="Disbursement Date" required htmlFor="pay-date">
          <Input
            id="pay-date"
            type="date"
            value={payDate}
            onChange={(e) => setPayDate(e.target.value)}
          />
        </Field>

        <Field label="Bank UTR / Transaction Reference Number" required htmlFor="pay-utr">
          <Input
            id="pay-utr"
            value={utr}
            onChange={(e) => setUtr(e.target.value)}
            placeholder="e.g. HDFCN202611200001234"
          />
        </Field>
      </div>

      <div className="mt-4 flex items-center justify-end gap-2 border-t border-border/50 pt-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={!utr.trim() || settleMutation.isPending}
          loading={settleMutation.isPending}
          onClick={() => settleMutation.mutate()}
          className="bg-emerald-600 hover:bg-emerald-700 text-white"
        >
          Confirm Payout &amp; Settle ({formatInr(claim.total_approved_amount)})
        </Button>
      </div>
    </Modal>
  );
}
