import { Clock, ThumbsDown, ThumbsUp } from 'lucide-react';

import { dayTime } from '@/lib/requests';
import { cn } from '@/lib/utils';
import { RECOMMENDATION_LABELS, type RequestTraveller } from '@/types';

/**
 * The first level of a two-level approval, as one line: what the traveller's
 * manager said, or that they have not said anything yet.
 *
 * Nothing when the traveller has no manager - there is nobody to wait for.
 * Only for screens whose readers are sent the recommendation (admins, and the
 * traveller's own manager): for anyone else the API leaves it out, and this
 * would read "Waiting" for advice that has in fact been given.
 */
export function ManagerReview({
  traveller,
  className,
}: {
  traveller: RequestTraveller;
  className?: string;
}) {
  const who = traveller.manager_reviewed_by_name ?? traveller.manager_name;

  if (traveller.manager_recommendation) {
    const recommended = traveller.manager_recommendation === 'RECOMMENDED';
    const Icon = recommended ? ThumbsUp : ThumbsDown;
    return (
      <p
        className={cn(
          'inline-flex max-w-full items-start gap-1.5 rounded-md px-2 py-1 text-xs',
          recommended ? 'bg-success-soft text-success' : 'bg-danger-soft text-danger',
          className,
        )}
      >
        <Icon size={13} className="mt-px shrink-0" />
        <span className="min-w-0 break-words">
          <span className="font-semibold">
            {who ? `L1 Manager ${who}: ` : 'L1 Manager: '}
            {RECOMMENDATION_LABELS[traveller.manager_recommendation]}
          </span>
          {traveller.manager_comment && (
            <span className="text-text-muted"> - "{traveller.manager_comment}"</span>
          )}
          {traveller.manager_reviewed_at && (
            <span className="ml-1 text-2xs opacity-80 font-normal">
              ({dayTime(traveller.manager_reviewed_at)})
            </span>
          )}
        </span>
      </p>
    );
  }

  if (!traveller.manager_name) return null;

  // Decided without a word from the manager: the admin may do that, and the
  // record should say so plainly rather than look like the manager agreed.
  if (traveller.status !== 'PENDING') {
    return (
      <p className={cn('text-2xs text-text-subtle', className)}>
        No L1 review from {traveller.manager_name}
      </p>
    );
  }

  return (
    <p
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md bg-warning-soft px-2 py-1 text-xs font-medium text-warning',
        className,
      )}
    >
      <Clock size={13} className="shrink-0" />
      Awaiting L1 Review ({traveller.manager_name})
    </p>
  );
}
