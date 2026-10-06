import { cva, type VariantProps } from 'class-variance-authority';
import { Loader2 } from 'lucide-react';
import {
  forwardRef,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from 'react';

import { cn } from '@/lib/utils';

/* -------------------------------------------------------------------------
   Button

   `primary` is ink, not brand red. Red belongs to `danger` - in a product
   whose main verbs are Approve and Reject, one red has to mean one thing.
   ------------------------------------------------------------------------- */

const buttonStyles = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium transition-[background-color,border-color,color,opacity] disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        primary: 'bg-primary text-primary-fg shadow-sm hover:bg-primary-hover active:translate-y-px',
        secondary:
          'border border-border bg-surface text-text shadow-sm hover:border-border-strong hover:bg-surface-sunken',
        ghost: 'text-text-muted hover:bg-surface-sunken hover:text-text',
        danger: 'bg-danger text-white hover:opacity-90',
        link: 'text-brand-strong underline-offset-4 hover:underline',
      },
      size: {
        sm: 'h-9 px-3.5 text-xs',
        md: 'h-10 px-4 text-sm',
        lg: 'h-12 px-6 text-sm',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
);

interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonStyles> {
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, loading, children, disabled, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(buttonStyles({ variant, size }), className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Loader2 size={14} className="animate-spin" />}
      {children}
    </button>
  ),
);
Button.displayName = 'Button';

/* ------------------------------------------------------------------------- */

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        // 16px below sm: iOS zooms the page into any input smaller than that.
        'h-10 w-full rounded-md border border-border bg-surface px-3 text-base text-text shadow-sm transition-colors sm:text-sm',
        'placeholder:text-text-subtle hover:border-border-strong focus:border-border-strong',
        'disabled:cursor-not-allowed disabled:opacity-60',
        'aria-[invalid=true]:border-danger',
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = 'Input';

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        'h-10 w-full rounded-md border border-border bg-surface px-2.5 text-base text-text shadow-sm transition-colors sm:text-sm',
        'hover:border-border-strong disabled:cursor-not-allowed disabled:opacity-60',
        className,
      )}
      {...props}
    >
      {children}
    </select>
  ),
);
Select.displayName = 'Select';

interface FieldProps {
  label: string;
  htmlFor?: string;
  hint?: string;
  error?: string | null;
  required?: boolean;
  children: ReactNode;
  className?: string;
}

export function Field({ label, htmlFor, hint, error, required, children, className }: FieldProps) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <label htmlFor={htmlFor} className="block text-xs font-medium text-text">
        {label}
        {required && <span className="ml-0.5 text-danger">*</span>}
      </label>
      {children}
      {error ? (
        <p className="text-xs text-danger">{error}</p>
      ) : hint ? (
        <p className="text-xs text-text-subtle">{hint}</p>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------------- */

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn('rounded-xl border border-border bg-surface shadow-sm', className)}>
      {children}
    </div>
  );
}

export function CardHeader({ title, description, action }: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-border px-4 py-4 sm:px-5">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
        {description && <p className="mt-0.5 text-xs text-text-muted">{description}</p>}
      </div>
      {action}
    </div>
  );
}

const badgeStyles = cva(
  'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-2xs font-semibold',
  {
    variants: {
      tone: {
        neutral: 'bg-surface-sunken text-text-muted ring-1 ring-inset ring-border',
        success: 'bg-success-soft text-success',
        warning: 'bg-warning-soft text-warning',
        danger: 'bg-danger-soft text-danger',
        info: 'bg-info-soft text-info',
        brand: 'bg-brand-soft text-brand-strong',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
);

export function Badge({
  tone,
  className,
  children,
}: VariantProps<typeof badgeStyles> & { className?: string; children: ReactNode }) {
  return <span className={cn(badgeStyles({ tone }), className)}>{children}</span>;
}

/* ------------------------------------------------------------------------- */

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn('animate-spin text-text-subtle', className)} />;
}

/** Shimmering placeholder, sized by the caller. Better than a spinner for
 *  content whose shape we already know. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={cn('relative overflow-hidden rounded bg-surface-sunken', className)}>
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-black/5 to-transparent dark:via-white/5" />
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      {icon && <div className="mb-3 text-text-subtle">{icon}</div>}
      <p className="text-sm font-medium">{title}</p>
      {description && (
        <p className="mt-1 max-w-sm text-xs leading-relaxed text-text-muted">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/* -------------------------------------------------------------------------
   Page header — title, one line of context, and the page's own actions.
   Stacks on a phone; sits in one row from sm up.
   ------------------------------------------------------------------------- */

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h1>
        {description && <p className="mt-1.5 max-w-2xl text-sm text-text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/* -------------------------------------------------------------------------
   Lists of distinct items - requests, asks, notices.

   An item here is several lines (a route, its people, their statuses), so a
   hairline between two of them reads as just another line. Each item is its
   own card instead, on a sunken band with space between, a coloured left edge
   saying where it stands, and its number - so where one ends and the next
   begins is never a matter of reading carefully.
   ------------------------------------------------------------------------- */

export type Accent = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'brand';

/** The left edge of an item card. Full class names, so Tailwind keeps them. */
const ACCENT_EDGE: Record<Accent, string> = {
  neutral: 'border-l-border-strong',
  info: 'border-l-info',
  success: 'border-l-success',
  warning: 'border-l-warning',
  danger: 'border-l-danger',
  brand: 'border-l-brand',
};

/** The band the cards sit on. A <ul>; put ItemCards in it. */
export function ItemList({ className, children }: { className?: string; children: ReactNode }) {
  return <ul className={cn('space-y-3 bg-surface-sunken/60 p-3 sm:p-4', className)}>{children}</ul>;
}

/** One item: its own bordered card, with a coloured left edge for its state. */
export function ItemCard({
  accent = 'neutral',
  className,
  children,
}: {
  accent?: Accent;
  className?: string;
  children: ReactNode;
}) {
  return (
    <li
      className={cn(
        'rounded-lg border border-l-4 border-border bg-surface px-4 py-3.5 shadow-sm sm:px-5',
        ACCENT_EDGE[accent],
        className,
      )}
    >
      {children}
    </li>
  );
}

/** "#12" - the number people quote on the phone, set apart from the text. */
export function ItemNumber({ value, className }: { value: number | string; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded bg-surface-sunken px-1.5 py-0.5 font-mono text-2xs font-semibold tabular-nums text-text-muted ring-1 ring-inset ring-border',
        className,
      )}
    >
      #{value}
    </span>
  );
}

/** Table body rows alternate a band, so a long row is easy to follow across,
 *  and the row under the pointer takes a light brand tint - never the band's
 *  colour, which out-ranks a row's own hover and would hide it. Put on the
 *  <tbody>. */
export const ZEBRA_ROWS =
  '[&>tr:nth-child(even)]:bg-surface-sunken [&>tr:hover]:!bg-brand-soft/70';
