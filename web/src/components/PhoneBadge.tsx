import { useState } from 'react';
import { Check, Copy, Phone } from 'lucide-react';
import toast from 'react-hot-toast';

import { cn } from '@/lib/utils';

export interface PhoneBadgeProps {
  phone: string | null | undefined;
  name?: string;
  className?: string;
  size?: 'xs' | 'sm' | 'md';
  showDial?: boolean;
}

/**
 * Copies phone number to clipboard with toast notification, avoiding
 * unwanted browser protocol prompts ("wants to open this application")
 * while still offering an optional explicit dial action.
 */
export function PhoneBadge({
  phone,
  name,
  className,
  size = 'sm',
  showDial = true,
}: PhoneBadgeProps) {
  const [copied, setCopied] = useState(false);

  if (!phone) {
    return <span className="text-2xs text-text-subtle">No phone</span>;
  }

  const cleanDigits = phone.replace(/[^\d+]/g, '');

  const copyToClipboard = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();

    let success = false;
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(phone);
        success = true;
      } else {
        const textArea = document.createElement('textarea');
        textArea.value = phone;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        textArea.style.top = '-999999px';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        success = document.execCommand('copy');
        document.body.removeChild(textArea);
      }
    } catch {
      success = false;
    }

    if (success) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      toast.success(
        name ? `Copied ${name}'s phone (${phone})` : `Copied phone number (${phone}) to clipboard`,
        { id: `phone-copy-${cleanDigits}` },
      );
    } else {
      toast.error('Could not copy phone number to clipboard', {
        id: `phone-copy-err-${cleanDigits}`,
      });
    }
  };

  const dialNumber = (e: React.MouseEvent) => {
    e.stopPropagation();
    window.location.href = `tel:${cleanDigits}`;
  };

  const iconSize = size === 'xs' ? 10 : size === 'md' ? 13 : 11;

  return (
    <span
      className={cn(
        'group inline-flex items-center gap-1 rounded-md border border-border bg-surface-sunken px-2 py-0.5 font-medium transition-colors hover:border-brand/40 hover:bg-brand-soft/20',
        size === 'xs' && 'px-1.5 py-0 text-2xs',
        size === 'sm' && 'text-xs',
        size === 'md' && 'px-2.5 py-1 text-sm',
        className,
      )}
    >
      <button
        type="button"
        onClick={copyToClipboard}
        title={`Click to copy phone number (${phone})`}
        className="inline-flex items-center gap-1 font-semibold text-brand hover:text-brand-strong focus:outline-none"
      >
        {copied ? (
          <Check size={iconSize} className="text-success" />
        ) : (
          <Copy size={iconSize} className="opacity-70 group-hover:opacity-100" />
        )}
        <span>{phone}</span>
      </button>

      {showDial && (
        <button
          type="button"
          onClick={dialNumber}
          title={`Dial ${name ? `${name} (${phone})` : phone}`}
          className="ml-0.5 rounded p-0.5 text-text-muted hover:bg-brand-soft hover:text-brand focus:outline-none"
        >
          <Phone size={iconSize} />
        </button>
      )}
    </span>
  );
}
