import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Archive,
  ArchiveRestore,
  FolderKanban,
  Pencil,
  Plus,
  Search,
  Trash2,
} from 'lucide-react';
import { useState, type FormEvent } from 'react';
import toast from 'react-hot-toast';

import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Modal } from '@/components/Modal';
import { PlacePicker } from '@/components/PlacePicker';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
  ZEBRA_ROWS,
} from '@/components/ui';
import {
  archiveProject,
  createProject,
  deleteProject,
  errorMessage,
  fetchProjects,
  restoreProject,
  updateProject,
  type ProjectPayload,
} from '@/lib/api';
import { campaignPlace } from '@/lib/projects';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import {
  isAdminRole,
  PROJECT_STATUS_HELP,
  PROJECT_STATUS_LABELS,
  type Project,
  type ProjectStatus,
} from '@/types';

/** Every field as the inputs hold it - strings, "" for blank. */
interface FormState {
  name: string;
  code: string;
  client_name: string;
  state: string;
  city: string;
  status: ProjectStatus;
  start_date: string;
  end_date: string;
  description: string;
}

const BLANK: FormState = {
  name: '',
  code: '',
  client_name: '',
  state: '',
  city: '',
  status: 'ACTIVE',
  start_date: '',
  end_date: '',
  description: '',
};

const ALL_STATUSES = Object.keys(PROJECT_STATUS_LABELS) as ProjectStatus[];

/** A new campaign is either open or on hold; finishing or putting one away
 *  only makes sense once it has run. */
const NEW_STATUSES: ProjectStatus[] = ['ACTIVE', 'PAUSED'];

const STATUS_TONE: Record<ProjectStatus, 'success' | 'warning' | 'info' | 'neutral'> = {
  ACTIVE: 'success',
  PAUSED: 'warning',
  COMPLETED: 'info',
  ARCHIVED: 'neutral',
};

function formatRange(project: Project) {
  const fmt = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  if (project.start_date && project.end_date)
    return `${fmt(project.start_date)} – ${fmt(project.end_date)}`;
  if (project.start_date) return `from ${fmt(project.start_date)}`;
  if (project.end_date) return `until ${fmt(project.end_date)}`;
  return '—';
}

function requestsLabel(count: number) {
  return `${count} ${count === 1 ? 'request' : 'requests'}`;
}

export default function ProjectsPage() {
  const queryClient = useQueryClient();
  // Managers create and edit campaigns; archiving and deleting stay with admins.
  const isAdmin = isAdminRole(useAuth((s) => s.user)?.role);

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [editing, setEditing] = useState<Project | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<FormState>(BLANK);
  const [formError, setFormError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<{
    kind: 'archive' | 'delete';
    project: Project;
  } | null>(null);

  const projects = useQuery({
    queryKey: ['projects', search, statusFilter],
    queryFn: () =>
      fetchProjects({
        search: search.trim() || undefined,
        status: statusFilter || undefined,
        page_size: 100,
      }),
  });

  // Campaign names and the list itself feed every report's filters and tables.
  const refresh = () => {
    for (const key of ['projects', 'filter-options', 'insights', 'travel-logs', 'analytics']) {
      queryClient.invalidateQueries({ queryKey: [key] });
    }
  };

  const save = useMutation({
    mutationFn: () => {
      // Blanks go as null: a blank date clears it rather than failing to parse.
      // No code: every campaign's ID is the server's to give.
      const payload: ProjectPayload = {
        name: form.name,
        client_name: form.client_name || null,
        state: form.state || null,
        city: form.city || null,
        status: form.status,
        start_date: form.start_date || null,
        end_date: form.end_date || null,
        description: form.description || null,
      };
      if (editing?.is_fallback) {
        // Locked on the server; leaving it out keeps the save about what the
        // admin could actually change.
        delete payload.status;
      }
      return editing ? updateProject(editing.id, payload) : createProject(payload);
    },
    meta: { errorFallback: 'Could not save this campaign.' },
    onSuccess: (project) => {
      toast.success(
        editing ? `${project.name} saved` : `${project.name} created as ${project.code}`,
      );
      setFormOpen(false);
      setEditing(null);
      setForm(BLANK);
      setFormError(null);
      refresh();
    },
    onError: (err) => setFormError(errorMessage(err, 'Could not save this campaign.')),
  });

  const toggleArchive = useMutation({
    mutationFn: (project: Project) =>
      project.status === 'ARCHIVED' ? restoreProject(project.id) : archiveProject(project.id),
    onSuccess: (updated) => {
      toast.success(
        updated.status === 'ARCHIVED' ? `${updated.name} archived` : `${updated.name} restored`,
      );
      setConfirming(null);
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (project: Project) => deleteProject(project.id),
    meta: { errorFallback: 'Could not delete this campaign.' },
    onSuccess: (_, project) => {
      toast.success(`${project.name} deleted`);
      setConfirming(null);
      refresh();
    },
    // Most likely someone raised a request against it meanwhile. The toast says
    // so; the refreshed row then offers Archive instead.
    onError: () => {
      setConfirming(null);
      refresh();
    },
  });

  const openCreate = () => {
    setEditing(null);
    setForm(BLANK);
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (project: Project) => {
    setEditing(project);
    setForm({
      name: project.name,
      code: project.code,
      client_name: project.client_name ?? '',
      state: project.state ?? '',
      city: project.city ?? '',
      status: project.status,
      start_date: project.start_date ?? '',
      end_date: project.end_date ?? '',
      description: project.description ?? '',
    });
    setFormError(null);
    setFormOpen(true);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);
    if (form.start_date && form.end_date && form.end_date < form.start_date) {
      setFormError("End date can't be before the start date.");
      return;
    }
    save.mutate();
  };

  const rows = projects.data?.items ?? [];
  const statusChoices = (editing ? ALL_STATUSES : NEW_STATUSES).filter(
    (s) => isAdmin || s !== 'ARCHIVED' || editing?.status === 'ARCHIVED',
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Campaigns</h1>
          <p className="mt-1.5 max-w-2xl text-sm text-text-muted">
            Every travel, cab and hotel request is tagged against a campaign. Archive hides one
            from new requests and keeps its history. A campaign with no requests can be deleted.
          </p>
        </div>
        <Button onClick={openCreate}>
          <Plus size={15} />
          New campaign
        </Button>
      </div>

      <Card>
        <CardHeader
          title={`${projects.data?.total ?? 0} ${projects.data?.total === 1 ? 'campaign' : 'campaigns'}`}
          action={
            <div className="flex gap-2">
              <div className="relative">
                <Search
                  size={14}
                  className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-subtle"
                />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search name, code, client or place"
                  className="w-56 pl-8"
                  aria-label="Search campaigns"
                />
              </div>
              <Select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                aria-label="Filter by status"
                className="w-36"
              >
                <option value="">All statuses</option>
                {ALL_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {PROJECT_STATUS_LABELS[s]}
                  </option>
                ))}
              </Select>
            </div>
          }
        />

        {projects.isPending ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : projects.isError ? (
          <EmptyState
            icon={<FolderKanban size={28} />}
            title="Could not load campaigns"
            description={errorMessage(projects.error)}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<FolderKanban size={28} />}
            title={search || statusFilter ? 'Nothing matches that' : 'No campaigns yet'}
            description={
              search || statusFilter
                ? 'Try a different search or filter.'
                : 'Create one so ground staff have something to tag their requests against.'
            }
            action={
              !search && !statusFilter ? (
                <Button onClick={openCreate}>
                  <Plus size={15} />
                  New campaign
                </Button>
              ) : undefined
            }
          />
        ) : (
          // Rounded at the foot, so the banded rows keep the card's corners.
          <div className="overflow-x-auto rounded-b-xl">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-2xs uppercase tracking-widest text-text-subtle">
                  <th className="px-5 py-2.5 font-semibold">Campaign</th>
                  <th className="hidden px-5 py-2.5 font-semibold md:table-cell">Client</th>
                  <th className="hidden px-5 py-2.5 font-semibold lg:table-cell">Location</th>
                  <th className="hidden px-5 py-2.5 font-semibold xl:table-cell">Dates</th>
                  <th className="px-5 py-2.5 font-semibold">Status</th>
                  <th className="px-5 py-2.5 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              {/* Banded, so a wide row is easy to follow across. */}
              <tbody className={cn('divide-y divide-border', ZEBRA_ROWS)}>
                {rows.map((project) => (
                  <tr
                    key={project.id}
                    className={
                      project.status === 'ARCHIVED'
                        ? 'opacity-60 transition-colors hover:bg-surface-sunken/60'
                        : 'transition-colors hover:bg-surface-sunken/60'
                    }
                  >
                    <td className="px-5 py-3">
                      <div className="flex flex-wrap items-center gap-2 font-medium">
                        {project.name}
                        {project.is_fallback && (
                          <Badge tone="neutral">Built-in</Badge>
                        )}
                      </div>
                      <div className="text-xs text-text-muted">
                        <span className="font-mono">{project.code}</span>
                        {' · '}
                        {requestsLabel(project.request_count)}
                      </div>
                    </td>
                    <td className="hidden px-5 py-3 text-text-muted md:table-cell">
                      {project.client_name || '—'}
                    </td>
                    <td className="hidden px-5 py-3 text-text-muted lg:table-cell">
                      {campaignPlace(project) || '—'}
                    </td>
                    <td className="hidden px-5 py-3 text-xs text-text-muted xl:table-cell">
                      {formatRange(project)}
                    </td>
                    <td className="px-5 py-3">
                      <Badge tone={STATUS_TONE[project.status]}>
                        {PROJECT_STATUS_LABELS[project.status]}
                      </Badge>
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Edit"
                          aria-label={`Edit ${project.name}`}
                          onClick={() => openEdit(project)}
                        >
                          <Pencil size={14} />
                        </Button>
                        {isAdmin && !project.is_fallback &&
                          (project.status === 'ARCHIVED' ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              title="Restore (make Active again)"
                              aria-label={`Restore ${project.name}`}
                              loading={
                                toggleArchive.isPending &&
                                toggleArchive.variables?.id === project.id
                              }
                              onClick={() => toggleArchive.mutate(project)}
                            >
                              <ArchiveRestore size={14} />
                            </Button>
                          ) : (
                            <Button
                              variant="ghost"
                              size="sm"
                              title="Archive (hide from new requests)"
                              aria-label={`Archive ${project.name}`}
                              onClick={() => setConfirming({ kind: 'archive', project })}
                            >
                              <Archive size={14} />
                            </Button>
                          ))}
                        {isAdmin && !project.is_fallback && project.request_count === 0 && (
                          <Button
                            variant="ghost"
                            size="sm"
                            title="Delete"
                            aria-label={`Delete ${project.name}`}
                            className="text-danger hover:text-danger"
                            onClick={() => setConfirming({ kind: 'delete', project })}
                          >
                            <Trash2 size={14} />
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <ConfirmDialog
        open={confirming?.kind === 'archive'}
        title={`Archive “${confirming?.project.name ?? ''}”?`}
        tone="primary"
        confirmLabel="Archive campaign"
        loading={toggleArchive.isPending}
        onConfirm={() => confirming && toggleArchive.mutate(confirming.project)}
        onClose={() => setConfirming(null)}
      >
        <p>
          Staff won't be able to pick it on new requests. Its{' '}
          {requestsLabel(confirming?.project.request_count ?? 0)}, costs and history stay in
          reports and the activity log.
        </p>
        <p>You can restore it any time.</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={confirming?.kind === 'delete'}
        title={`Delete “${confirming?.project.name ?? ''}”?`}
        tone="danger"
        confirmLabel="Delete campaign"
        loading={remove.isPending}
        onConfirm={() => confirming && remove.mutate(confirming.project)}
        onClose={() => setConfirming(null)}
      >
        <p>This campaign has no requests, so nothing else is affected.</p>
        <p>This can't be undone. The activity log keeps a record of it.</p>
      </ConfirmDialog>

      <Modal
        open={formOpen}
        onClose={() => {
          setFormOpen(false);
          setFormError(null);
        }}
        title={editing ? `Edit ${editing.name}` : 'New campaign'}
        description={
          editing
            ? 'Changes are recorded in the activity log.'
            : 'Ground staff will tag their requests against this. Only the name is required.'
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setFormOpen(false)}>
              Cancel
            </Button>
            <Button form="project-form" type="submit" loading={save.isPending}>
              {editing ? 'Save changes' : 'Create campaign'}
            </Button>
          </>
        }
      >
        <form id="project-form" onSubmit={submit} className="space-y-4" noValidate>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Campaign name" htmlFor="name" required className="sm:col-span-2">
              <Input
                id="name"
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Monsoon Retail Audit"
              />
            </Field>

            <fieldset className="space-y-1.5 sm:col-span-2">
              <legend className="mb-1.5 block text-xs font-medium text-text">Status</legend>
              {editing?.is_fallback ? (
                <p className="text-xs text-text-subtle">
                  Built-in, always Active. Requests for a campaign that isn't listed yet go
                  here.
                </p>
              ) : (
                <div className="grid gap-2 sm:grid-cols-2">
                  {statusChoices.map((s) => {
                    const active = form.status === s;
                    return (
                      <button
                        key={s}
                        type="button"
                        onClick={() => setForm({ ...form, status: s })}
                        aria-pressed={active}
                        className={cn(
                          'rounded-md border px-3 py-2.5 text-left transition-colors',
                          active
                            ? 'border-primary bg-surface-sunken'
                            : 'border-border hover:border-border-strong',
                        )}
                      >
                        <span className="block text-sm font-semibold text-text">
                          {PROJECT_STATUS_LABELS[s]}
                        </span>
                        <span className="mt-0.5 block text-xs text-text-muted">
                          {PROJECT_STATUS_HELP[s]}
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </fieldset>

            <Field
              label="Campaign ID"
              htmlFor="code"
              hint={
                editing
                  ? 'Given when the campaign was created. It never changes.'
                  : 'Given automatically when you save, the next in sequence (CMP-year-number).'
              }
            >
              <Input
                id="code"
                value={editing ? form.code : 'Assigned on save'}
                readOnly
                disabled
                className="font-mono"
              />
            </Field>

            <Field label="Client (optional)" htmlFor="client_name">
              <Input
                id="client_name"
                value={form.client_name}
                onChange={(e) => setForm({ ...form, client_name: e.target.value })}
                placeholder="Acme Retail"
              />
            </Field>

            <div className="sm:col-span-2">
              <PlacePicker
                label="Campaign"
                id="project_place"
                state={form.state}
                city={form.city}
                hint="Optional. Leave it blank if the campaign covers the whole state."
                onChange={({ state, city }) => setForm({ ...form, state, city })}
              />
              {editing?.location && !form.state && (
                <p className="mt-1.5 text-xs text-text-subtle">
                  Previously typed as “{editing.location}”. Pick a state to replace it.
                </p>
              )}
            </div>

            <Field label="Start date (optional)" htmlFor="start_date">
              <Input
                id="start_date"
                type="date"
                value={form.start_date}
                onChange={(e) => setForm({ ...form, start_date: e.target.value })}
              />
            </Field>

            <Field
              label="End date (optional)"
              htmlFor="end_date"
              hint="Leave blank if open-ended."
            >
              <Input
                id="end_date"
                type="date"
                value={form.end_date}
                min={form.start_date || undefined}
                onChange={(e) => setForm({ ...form, end_date: e.target.value })}
              />
            </Field>
          </div>

          {formError && (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
              {formError}
            </p>
          )}
        </form>
      </Modal>
    </div>
  );
}
