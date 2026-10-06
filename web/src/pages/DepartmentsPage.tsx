import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2, Check, Pencil, Plus, Trash2, X } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import toast from 'react-hot-toast';

import { ConfirmDialog } from '@/components/ConfirmDialog';
import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  Input,
  ItemCard,
  ItemList,
  PageHeader,
  Skeleton,
} from '@/components/ui';
import {
  createDepartment,
  deleteDepartment,
  errorMessage,
  fetchDepartments,
  renameDepartment,
} from '@/lib/api';
import type { Department } from '@/types';

/**
 * The department list everyone else picks from - on Team, on My profile, and
 * in the team filters. Admins keep it tidy here: add, rename, and remove one
 * nobody is in any more (the server refuses while it still has people).
 */
export default function DepartmentsPage() {
  const queryClient = useQueryClient();
  const departments = useQuery({ queryKey: ['departments'], queryFn: fetchDepartments });

  const [name, setName] = useState('');
  const [renaming, setRenaming] = useState<{ id: number; name: string } | null>(null);
  const [removing, setRemoving] = useState<Department | null>(null);

  // People carry their department's name, so the team list moves with it.
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['departments'] });
    queryClient.invalidateQueries({ queryKey: ['users'] });
  };

  const add = useMutation({
    mutationFn: (value: string) => createDepartment(value),
    meta: { errorFallback: 'Could not add that department.' },
    onSuccess: (created) => {
      refresh();
      setName('');
      toast.success(`Department “${created.name}” added`);
    },
  });

  const rename = useMutation({
    mutationFn: (vars: { id: number; name: string }) => renameDepartment(vars.id, vars.name),
    meta: { errorFallback: 'Could not rename that department.' },
    onSuccess: (updated) => {
      refresh();
      setRenaming(null);
      toast.success(`Renamed to “${updated.name}”`);
    },
  });

  const remove = useMutation({
    mutationFn: (department: Department) => deleteDepartment(department.id),
    meta: { errorFallback: 'Could not remove that department.' },
    onSuccess: (_, department) => {
      refresh();
      setRemoving(null);
      toast.success(`Department “${department.name}” removed`);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (name.trim().length >= 2) add.mutate(name.trim());
  };

  const rows = departments.data ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Departments"
        description="The list people are placed in on Team and My profile. Every change is recorded in the activity log."
      />

      <Card>
        <CardHeader title="Add a department" />
        <form onSubmit={submit} className="flex flex-col gap-3 p-4 sm:flex-row sm:p-5">
          <Input
            aria-label="Department name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Field Operations"
            maxLength={80}
            className="sm:max-w-sm"
          />
          <Button type="submit" loading={add.isPending} disabled={name.trim().length < 2}>
            <Plus size={15} />
            Add department
          </Button>
        </form>
      </Card>

      <Card>
        <CardHeader
          title={`${rows.length} ${rows.length === 1 ? 'department' : 'departments'}`}
          description="A department can be removed once nobody is in it."
        />
        {departments.isPending ? (
          <div className="space-y-2 p-5">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : departments.isError ? (
          <EmptyState
            icon={<Building2 size={28} />}
            title="Could not load the departments"
            description={errorMessage(departments.error)}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<Building2 size={28} />}
            title="No departments yet"
            description="Add the first one above."
          />
        ) : (
          // Last thing in the card, so the band keeps the card's rounded foot.
          <ItemList className="rounded-b-xl">
            {rows.map((department) => {
              const editing = renaming?.id === department.id;
              return (
                // An empty department - the kind that can be removed - reads quieter.
                <ItemCard
                  key={department.id}
                  accent={department.member_count > 0 ? 'brand' : 'neutral'}
                  className="flex flex-wrap items-center gap-3"
                >
                  {editing ? (
                    <form
                      className="flex min-w-0 flex-1 items-center gap-2"
                      onSubmit={(e) => {
                        e.preventDefault();
                        if (renaming.name.trim().length >= 2) {
                          rename.mutate({ id: department.id, name: renaming.name.trim() });
                        }
                      }}
                    >
                      <Input
                        autoFocus
                        aria-label={`New name for ${department.name}`}
                        value={renaming.name}
                        maxLength={80}
                        onChange={(e) => setRenaming({ id: department.id, name: e.target.value })}
                        onKeyDown={(e) => e.key === 'Escape' && setRenaming(null)}
                        className="max-w-sm"
                      />
                      <Button type="submit" size="sm" loading={rename.isPending} aria-label="Save name">
                        <Check size={14} />
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => setRenaming(null)}
                        aria-label="Cancel renaming"
                      >
                        <X size={14} />
                      </Button>
                    </form>
                  ) : (
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{department.name}</p>
                      <p className="text-xs text-text-subtle">
                        {department.member_count} {department.member_count === 1 ? 'person' : 'people'}
                      </p>
                    </div>
                  )}

                  {!editing && (
                    <div className="flex gap-1.5">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => setRenaming({ id: department.id, name: department.name })}
                      >
                        <Pencil size={13} />
                        Rename
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={department.member_count > 0}
                        title={
                          department.member_count > 0
                            ? `Move its ${department.member_count} ${department.member_count === 1 ? 'person' : 'people'} to another department first`
                            : `Remove ${department.name}`
                        }
                        onClick={() => setRemoving(department)}
                      >
                        <Trash2 size={13} />
                        Remove
                      </Button>
                    </div>
                  )}
                </ItemCard>
              );
            })}
          </ItemList>
        )}
      </Card>

      <ConfirmDialog
        open={removing !== null}
        title={`Remove ${removing?.name ?? 'this department'}?`}
        confirmLabel="Remove department"
        loading={remove.isPending}
        onConfirm={() => removing && remove.mutate(removing)}
        onClose={() => setRemoving(null)}
      >
        <p>Nobody is in it, so nothing else changes. The removal is recorded in the activity log.</p>
      </ConfirmDialog>
    </div>
  );
}
