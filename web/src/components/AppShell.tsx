import { useMutation, useQuery } from '@tanstack/react-query';
import {
  BarChart3,
  Bell,
  Bug,
  Building2,
  CalendarCheck,
  CheckSquare,
  ChevronDown,
  ClipboardCheck,
  FolderKanban,
  History,
  LayoutDashboard,
  LogOut,
  Menu,
  Monitor,
  PanelLeftClose,
  PanelLeftOpen,
  Receipt,
  ScrollText,
  Smartphone,
  Store,
  Users,
  UsersRound,
  Wallet,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';

import { BugReportWidget } from '@/components/BugReportWidget';
import { CompanyCredit, CreatorCredit, Logo, LogoLockup } from '@/components/Logo';
import NotificationBell from '@/components/NotificationBell';
import { PageErrorBoundary, ServerStatusBanner } from '@/components/ServerStatus';
import { ThemeToggle } from '@/components/ThemeToggle';
import { Spinner } from '@/components/ui';
import {
  fetchInvoices,
  fetchMe,
  fetchQueueCounts,
  fetchTeamChanges,
  fetchTeamReviews,
  fetchUnreadCount,
  logout,
  saveThemePreference,
} from '@/lib/api';
import { cn } from '@/lib/utils';
import { isPhone, setViewMode, viewMode } from '@/lib/viewMode';
import { useAuth } from '@/store/auth';
import { useTheme } from '@/store/theme';
import { ADMIN_ROLES, ROLE_LABELS, isAdminRole, isSuperAdmin, type Role } from '@/types';

type Group = 'Workspace' | 'Operations' | 'Manage';

interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  roles?: Role[];
  /** Which section it sits under in the sidebar. */
  group: Group;
  /** A live count beside it: unread notices, requests waiting on a decision,
   *  managers' team changes waiting on an admin, or team requests waiting on
   *  the manager's recommendation. */
  badge?: 'unread' | 'queue' | 'team' | 'review' | 'invoices';
  /** On a phone, the first three of these (lowest first) get the bottom bar. */
  bottom?: number;
}

const ADMINS = ADMIN_ROLES;

// My profile is not here: the name card at the foot of the sidebar opens it.
const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, group: 'Workspace', bottom: 1 },
  { to: '/requests', label: 'My requests', icon: CalendarCheck, group: 'Workspace', bottom: 2 },
  { to: '/expenses', label: 'Reimbursements', icon: Wallet, group: 'Workspace' },
  { to: '/notifications', label: 'Notifications', icon: Bell, group: 'Workspace', badge: 'unread', bottom: 3 },
  { to: '/approvals', label: 'Approvals & Orders', icon: CheckSquare, roles: [...ADMINS, 'VENDOR'], group: 'Operations', badge: 'queue', bottom: 0 },
  // A manager's half of two-level approval. On the phone bar too: for a
  // manager it is the page with work waiting on them.
  { to: '/team-approvals', label: 'Team approvals', icon: ClipboardCheck, roles: ['MANAGER'], group: 'Operations', badge: 'review', bottom: 0 },
  { to: '/travel-logs', label: 'Travel logs', icon: History, roles: [...ADMINS, 'FINANCE'], group: 'Operations' },
  { to: '/analytics', label: 'Cost analytics', icon: BarChart3, roles: [...ADMINS, 'FINANCE'], group: 'Operations' },
  // Every admin tier reads invoices; the count is for the super admin, the
  // only one who can approve the ones waiting. Finance reviews reconciled vendor invoices, and vendors raise them.
  { to: '/invoices', label: 'Invoices', icon: Receipt, roles: [...ADMINS, 'FINANCE', 'VENDOR'], group: 'Operations', badge: 'invoices' },
  { to: '/my-team', label: 'My team', icon: UsersRound, roles: ['MANAGER'], group: 'Manage' },
  { to: '/projects', label: 'Campaigns', icon: FolderKanban, roles: [...ADMINS, 'MANAGER'], group: 'Manage' },
  { to: '/team', label: 'Team', icon: Users, roles: ADMINS, group: 'Manage', badge: 'team' },
  { to: '/departments', label: 'Departments', icon: Building2, roles: ADMINS, group: 'Manage' },
  { to: '/vendors', label: 'Vendors', icon: Store, roles: ADMINS, group: 'Manage' },
  { to: '/audit', label: 'Activity log', icon: ScrollText, roles: ADMINS, group: 'Manage' },
  { to: '/bug-reports', label: 'Bug reports', icon: Bug, roles: ADMINS, group: 'Manage' },
];

const GROUPS: Group[] = ['Workspace', 'Operations', 'Manage'];

// Per-browser conveniences, so storage that is blocked or cleared costs only
// the remembered layout.
const RAIL_KEY = 'sriyatra.sidebar.rail';
const CLOSED_KEY = 'sriyatra.sidebar.closed';

function readPref(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writePref(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Private mode or blocked storage: the choice lasts until reload.
  }
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}

function badgeText(count: number) {
  return count > 99 ? '99+' : String(count);
}

/**
 * Mirrors the theme choice to the server so it follows the user to another
 * device. A component of its own, so a theme switch re-renders only this and
 * the toggle - never the page under the shell, which on a phone held the new
 * colours up. The saved user is not written back for the same reason; the next
 * profile read brings it. Failure is silent - the local choice still applies.
 */
function ThemeSync() {
  const preference = useTheme((s) => s.preference);
  const saved = useAuth((s) => s.user?.theme_preference);
  // What the server holds, as far as this tab knows.
  const onServer = useRef(saved);
  useEffect(() => {
    onServer.current = saved;
  }, [saved]);
  useEffect(() => {
    if (saved === undefined || preference === onServer.current) return;
    onServer.current = preference;
    saveThemePreference(preference).catch(() => {
      onServer.current = undefined;
    });
  }, [preference, saved]);
  return null;
}

export default function AppShell() {
  const location = useLocation();
  const user = useAuth((s) => s.user);
  const signOut = useAuth((s) => s.signOut);
  const [drawerOpen, setDrawerOpen] = useState(false);
  // Desktop only: the sidebar folded down to an icon rail.
  const [rail, setRail] = useState(() => readPref(RAIL_KEY) === '1');
  const [closed, setClosed] = useState<Group[]>(() => {
    try {
      return JSON.parse(readPref(CLOSED_KEY) ?? '[]');
    } catch {
      return [];
    }
  });

  const toggleRail = () => {
    setRail((value) => {
      writePref(RAIL_KEY, value ? '0' : '1');
      return !value;
    });
  };
  const toggleGroup = (group: Group) => {
    setClosed((current) => {
      const next = current.includes(group) ? current.filter((g) => g !== group) : [...current, group];
      writePref(CLOSED_KEY, JSON.stringify(next));
      return next;
    });
  };

  // Navigating should never leave the mobile drawer covering the page.
  useEffect(() => setDrawerOpen(false), [location.pathname]);

  // Escape closes the drawer, and the page behind must not scroll while it is open.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setDrawerOpen(false);
    window.addEventListener('keydown', onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = previous;
    };
  }, [drawerOpen]);

  // The copy of the user saved at sign-in goes stale: an admin may rename
  // them, change their role or department, or switch the account off. Reading
  // it again on load and whenever the tab regains focus keeps the shell
  // honest, and a switched-off account is signed out there and then (401).
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe, refetchOnWindowFocus: true });
  useEffect(() => {
    if (me.data) useAuth.getState().setUser(me.data);
  }, [me.data]);

  const isAdmin = isAdminRole(user?.role);
  // The same queries the bell and the approvals page read, so the counts agree.
  const unread = useQuery({ queryKey: ['unread-count'], queryFn: fetchUnreadCount });
  const queue = useQuery({
    queryKey: ['queue-counts'],
    queryFn: () => fetchQueueCounts(),
    enabled: isAdmin,
  });
  const teamChanges = useQuery({
    queryKey: ['team-changes', 'PENDING'],
    queryFn: () => fetchTeamChanges('PENDING'),
    enabled: isAdmin,
  });
  // The same query Team approvals reads, so the badge and the tab agree.
  const reviews = useQuery({
    queryKey: ['team-reviews', 'waiting'],
    queryFn: () => fetchTeamReviews('waiting'),
    enabled: user?.role === 'MANAGER',
  });
  // Invoices waiting for a super admin. Read with the list's own call, so the
  // number matches the Submitted tab.
  const invoices = useQuery({
    queryKey: ['invoices', 'badge'],
    queryFn: () => fetchInvoices({ status: 'SUBMITTED', limit: 1 }),
    enabled: isSuperAdmin(user?.role) || user?.role === 'FINANCE',
  });
  const countFor = (item: NavItem) =>
    item.badge === 'unread'
      ? (unread.data?.unread ?? 0)
      : item.badge === 'queue' && queue.data
        ? // Cabs asking for one more day wait on an admin too.
          queue.data.awaiting +
          queue.data.partially_approved +
          (queue.data.cab_extensions ?? 0) +
          (queue.data.cancellations ?? 0)
        : item.badge === 'team'
          ? (teamChanges.data?.pending ?? 0)
          : item.badge === 'review'
            ? (reviews.data?.total ?? 0)
            : item.badge === 'invoices'
              ? (invoices.data?.counts.SUBMITTED ?? 0)
              : 0;

  const signOutMutation = useMutation({
    mutationFn: logout,
    // The session ends locally regardless - a failed call must not trap
    // someone in a session they have asked to leave - so a failure is not
    // worth a red toast either.
    meta: { errorToast: false },
    onSettled: () => {
      signOut();
      toast.success('Signed out');
    },
  });

  const visible = NAV.filter((item) => !item.roles || (user && item.roles.includes(user.role)));
  const isCurrent = (item: NavItem) =>
    item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to);
  const currentLabel =
    visible.find(isCurrent)?.label ?? (location.pathname.startsWith('/profile') ? 'My profile' : undefined);
  const bottomItems = visible
    .filter((item) => item.bottom !== undefined)
    .sort((a, b) => (a.bottom ?? 0) - (b.bottom ?? 0))
    .slice(0, 3);
  // Ground staff see one short list, so section headings would only add noise.
  const sectioned = visible.length > 4;
  // Phones open the desktop layout; this is the way back, and forth.
  const phone = isPhone();
  const view = viewMode();

  return (
    <div className="min-h-dvh bg-canvas">
      {/* The first thing a keyboard user reaches. Without it, every page starts
          with the whole menu before the content. Visually hidden until focused. */}
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-primary-fg"
      >
        Skip to content
      </a>

      {drawerOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm lg:hidden"
          onClick={() => setDrawerOpen(false)}
          aria-hidden
        />
      )}

      <aside
        // A drawer below lg and a permanent sidebar above it. Deliberately not
        // aria-hidden when closed: the same element is the desktop navigation,
        // and hiding it on that breakpoint would remove the nav entirely.
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col border-r border-sidebar-fg/10 bg-sidebar text-sidebar-fg shadow-xl transition-[transform,width] duration-300 ease-out lg:shadow-none',
          drawerOpen ? 'translate-x-0' : 'max-lg:-translate-x-full',
          rail ? 'lg:w-[76px]' : 'lg:w-64',
        )}
      >
        <div className={cn('flex h-16 shrink-0 items-center justify-between gap-2 px-4', rail && 'lg:justify-center lg:px-0')}>
          <Link to="/" aria-label="Sriyatra home" className="min-w-0 rounded-lg focus-visible:outline-offset-2">
            <LogoLockup className={cn(rail && 'lg:hidden')} />
            {rail && <LogoLockup markOnly className="hidden lg:flex" />}
          </Link>
          <button
            type="button"
            onClick={() => setDrawerOpen(false)}
            className="rounded-md p-1.5 text-sidebar-fg/70 hover:bg-sidebar-fg/10 hover:text-sidebar-fg lg:hidden"
            aria-label="Close menu"
          >
            <X size={18} />
          </button>
        </div>

        <nav aria-label="Main" className="flex-1 space-y-4 overflow-y-auto px-3 pb-4 pt-2">
          {GROUPS.map((group, index) => {
            const items = visible.filter((item) => item.group === group);
            if (items.length === 0) return null;
            // The section holding the current page never hides it.
            const open = !closed.includes(group) || items.some(isCurrent);
            const listId = `nav-${group.toLowerCase()}`;
            return (
              <div key={group}>
                {sectioned && (
                  <button
                    type="button"
                    onClick={() => toggleGroup(group)}
                    aria-expanded={open}
                    aria-controls={listId}
                    className={cn(
                      'mb-1.5 flex w-full items-center justify-between rounded-md px-3 py-1 text-2xs font-semibold uppercase tracking-[0.12em] text-sidebar-fg/60 transition-colors hover:text-sidebar-fg/85',
                      rail && 'lg:hidden',
                    )}
                  >
                    {group}
                    <ChevronDown size={14} className={cn('transition-transform', !open && '-rotate-90')} />
                  </button>
                )}
                {rail && index > 0 && <div className="mx-3 mb-3 hidden border-t border-sidebar-fg/10 lg:block" />}
                <ul
                  id={listId}
                  // The rail has no headings to reopen a section from, so it
                  // always shows every item.
                  className={cn('space-y-1', !open && 'hidden', !open && rail && 'lg:block')}
                >
                  {items.map((item) => {
                    const Icon = item.icon;
                    const count = countFor(item);
                    return (
                      <li key={item.to}>
                        <NavLink
                          to={item.to}
                          end={item.to === '/'}
                          title={rail ? item.label : undefined}
                          className={({ isActive }) =>
                            cn(
                              'relative flex h-10 items-center gap-3 rounded-xl px-3 text-sm transition-colors',
                              isActive
                                ? 'bg-brand-soft/70 font-semibold text-brand-strong dark:bg-sidebar-fg/[0.14] dark:text-sidebar-fg'
                                : 'text-sidebar-fg/70 hover:bg-sidebar-fg/[0.05] hover:text-sidebar-fg',
                              rail && 'lg:justify-center lg:px-0',
                            )
                          }
                        >
                          {({ isActive }) => (
                            <>
                              {isActive && (
                                <span className="absolute inset-y-2.5 left-0 w-[3px] rounded-full bg-brand" />
                              )}
                              <Icon size={19} strokeWidth={isActive ? 2.25 : 2} className="shrink-0" />
                              <span className={cn('truncate', rail && 'lg:sr-only')}>{item.label}</span>
                              {count > 0 && (
                                <span
                                  aria-label={`${count} waiting`}
                                  className={cn(
                                    'ml-auto min-w-[1.375rem] rounded-full bg-sidebar-fg px-1.5 text-center text-2xs font-bold leading-5 text-sidebar tabular-nums',
                                    rail && 'lg:absolute lg:right-1.5 lg:top-1 lg:ml-0 lg:min-w-[1.125rem] lg:px-1 lg:leading-4',
                                  )}
                                >
                                  {badgeText(count)}
                                </span>
                              )}
                            </>
                          )}
                        </NavLink>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </nav>

        <div className="shrink-0 border-t border-sidebar-fg/10 p-3">
          {phone && (
            <button
              type="button"
              onClick={() => setViewMode(view === 'desktop' ? 'mobile' : 'desktop')}
              title={view === 'desktop' ? 'Switch to phone view' : 'Switch to desktop view'}
              className={cn(
                'mb-2 flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-xs font-medium text-sidebar-fg/70 transition-colors hover:bg-sidebar-fg/[0.05] hover:text-sidebar-fg',
                rail && 'lg:justify-center lg:px-0',
              )}
            >
              {view === 'desktop' ? <Smartphone size={16} /> : <Monitor size={16} />}
              <span className={cn(rail && 'lg:sr-only')}>
                {view === 'desktop' ? 'Switch to phone view' : 'Switch to desktop view'}
              </span>
            </button>
          )}
          <div className={cn('flex items-center gap-1', rail && 'lg:flex-col lg:gap-2')}>
            <NavLink
              to="/profile"
              title="My profile"
              className={({ isActive }) =>
                cn(
                  'flex min-w-0 flex-1 items-center gap-3 rounded-xl px-2 py-2 transition-colors hover:bg-sidebar-fg/[0.05]',
                  isActive && 'bg-sidebar-fg/[0.08] dark:bg-sidebar-fg/[0.12]',
                  rail && 'lg:flex-none lg:justify-center lg:p-1',
                )
              }
            >
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand-soft text-xs font-semibold text-brand-strong ring-1 ring-sidebar-fg/10">
                {user ? initials(user.full_name) : '?'}
              </span>
              <span className={cn('min-w-0 flex-1', rail && 'lg:hidden')}>
                <span className="block truncate text-sm font-medium text-sidebar-fg">{user?.full_name}</span>
                <span className="block truncate text-xs text-sidebar-fg/60">
                  {user ? ROLE_LABELS[user.role] : ''}
                </span>
              </span>
            </NavLink>
            <button
              type="button"
              title="Sign out"
              aria-label="Sign out"
              disabled={signOutMutation.isPending}
              onClick={() => signOutMutation.mutate()}
              className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-sidebar-fg/70 transition-colors hover:bg-sidebar-fg/10 hover:text-sidebar-fg disabled:opacity-60"
            >
              {signOutMutation.isPending ? <Spinner className="h-4 w-4" /> : <LogOut size={16} />}
            </button>
          </div>

          <div className={cn('mt-2 flex items-center justify-between gap-2 pl-2', rail && 'lg:justify-center lg:pl-0')}>
            <div className={cn('min-w-0 space-y-0.5', rail && 'lg:hidden')}>
              <CompanyCredit short />
              <CreatorCredit />
            </div>
            <button
              type="button"
              onClick={toggleRail}
              aria-label={rail ? 'Expand the sidebar' : 'Collapse the sidebar'}
              title={rail ? 'Expand the sidebar' : 'Collapse the sidebar'}
              className="hidden h-8 w-8 shrink-0 place-items-center rounded-lg text-sidebar-fg/60 transition-colors hover:bg-sidebar-fg/10 hover:text-sidebar-fg lg:grid"
            >
              {rail ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            </button>
          </div>
        </div>
      </aside>

      <div className={cn('transition-[padding] duration-300 ease-out', rail ? 'lg:pl-[76px]' : 'lg:pl-64')}>
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border bg-surface/80 px-4 backdrop-blur-md sm:px-6 lg:px-8">
          <Link to="/" aria-label="Sriyatra home" className="shrink-0 lg:hidden">
            <Logo variant="mark" className="h-8 w-8" />
          </Link>
          <span className="truncate text-base font-semibold">{currentLabel ?? 'Sriyatra'}</span>

          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            <NotificationBell />
            <ThemeToggle />
            <ThemeSync />
          </div>
        </header>

        <main
          id="main"
          tabIndex={-1}
          // Bottom padding on phones clears the tab bar.
          className="mx-auto w-full max-w-7xl animate-fade-in px-4 pb-28 pt-6 sm:px-6 sm:pt-8 lg:px-8 lg:pb-8 focus:outline-none"
        >
          <ServerStatusBanner isAdmin={isAdmin} />
          <PageErrorBoundary key={location.pathname}>
            <Outlet />
          </PageErrorBoundary>
        </main>
        <BugReportWidget />

        {/* Route changes are silent in a single-page app. This announces the
            new page so a screen-reader user knows the link did something. */}
        <p aria-live="polite" className="sr-only">
          {currentLabel ? `${currentLabel} page` : ''}
        </p>
      </div>

      {/* Phones: the pages people open most, one thumb away, and the full
          menu behind More. */}
      <nav
        aria-label="Quick links"
        className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface/95 backdrop-blur-md lg:hidden"
        style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
      >
        <ul className="grid grid-cols-4">
          {bottomItems.map((item) => {
            const Icon = item.icon;
            const count = countFor(item);
            return (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === '/'}
                  className={({ isActive }) =>
                    cn(
                      'flex h-16 flex-col items-center justify-center gap-1 text-[0.6875rem] font-medium transition-colors',
                      isActive ? 'text-brand-strong' : 'text-text-muted hover:text-text',
                    )
                  }
                >
                  <span className="relative">
                    <Icon size={21} />
                    {count > 0 && (
                      <span
                        aria-label={`${count} waiting`}
                        className="absolute -right-3 -top-1.5 min-w-[1.125rem] rounded-full bg-[rgb(200_30_30)] px-1 text-center text-[0.625rem] font-bold leading-4 text-white tabular-nums"
                      >
                        {badgeText(count)}
                      </span>
                    )}
                  </span>
                  <span className="max-w-full truncate px-1">{item.label}</span>
                </NavLink>
              </li>
            );
          })}
          <li>
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              aria-label="Open the full menu"
              aria-expanded={drawerOpen}
              className="flex h-16 w-full flex-col items-center justify-center gap-1 text-[0.6875rem] font-medium text-text-muted hover:text-text"
            >
              <Menu size={21} />
              More
            </button>
          </li>
        </ul>
      </nav>
    </div>
  );
}
