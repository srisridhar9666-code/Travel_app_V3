import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Info, Mail, Power, Send, XCircle } from 'lucide-react';
import { useState, type FormEvent, type ReactNode } from 'react';
import toast from 'react-hot-toast';
import { Link } from 'react-router-dom';

import { Badge, Button, Card, CardHeader, Input, Skeleton } from '@/components/ui';
import { errorMessage, fetchEmailStatus, sendTestEmail, toggleEmailService } from '@/lib/api';
import { formatInstant } from '@/lib/time';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import type { EmailSettings, EmailTestResult } from '@/types';

/** Where a test send stopped, in the words of someone watching it happen. */
const STAGE_LABELS: Record<string, string> = {
  config: 'Checking the settings',
  connect: 'Connecting to the mail server',
  login: 'Signing in to the mail account',
  send: 'Handing the message over',
  done: 'Delivered to the mail server',
};

function Notice({
  tone,
  children,
}: {
  tone: 'danger' | 'warning' | 'info' | 'success';
  children: ReactNode;
}) {
  const Icon =
    tone === 'danger' ? XCircle : tone === 'success' ? CheckCircle2 : tone === 'info' ? Info : AlertTriangle;
  return (
    <div
      role={tone === 'danger' ? 'alert' : undefined}
      className={cn(
        'flex items-start gap-2.5 rounded-lg px-3.5 py-3 text-sm',
        tone === 'danger' && 'bg-danger-soft text-danger',
        tone === 'warning' && 'bg-warning-soft text-warning',
        tone === 'info' && 'bg-info-soft text-info',
        tone === 'success' && 'bg-success-soft text-success',
      )}
    >
      <Icon size={17} className="mt-0.5 shrink-0" />
      <div className="min-w-0 space-y-1 break-words leading-relaxed">{children}</div>
    </div>
  );
}

/** Everything about the settings a person could act on, worst first. */
function Warnings({ settings, problem }: { settings: EmailSettings; problem: string | null }) {
  const env = settings.env_file;
  const typos = Object.entries(env.unknown_keys).filter(([, guess]) => guess);
  return (
    <>
      {problem && (
        <Notice tone="danger">
          <p className="font-semibold">Email is not going out.</p>
          <p>{problem}.</p>
        </Notice>
      )}
      {settings.restart_needed && (
        <Notice tone="warning">
          <p className="font-semibold">Restart the API to use the saved settings.</p>
          <p>
            backend/.env was saved at {formatInstant(env.modified_at)}, after the API started at{' '}
            {formatInstant(settings.started_at)}. Settings are read once, at start-up.
          </p>
        </Notice>
      )}
      {typos.map(([key, guess]) => (
        <Notice key={key} tone="warning">
          <p>
            backend/.env has <code className="font-mono">{key}</code>, which this app does not
            read. Did you mean <code className="font-mono">{guess}</code>?
          </p>
        </Notice>
      ))}
      {settings.password_looks_wrong && (
        <Notice tone="warning">
          <p>
            The app password is {settings.password}. A Gmail app password is 16 letters - your
            normal Gmail password will not work here. Create one at Google Account &gt; Security
            &gt; App passwords (2-Step Verification must be on).
          </p>
        </Notice>
      )}
      {settings.allowlist.length > 0 && (
        <Notice tone="warning">
          <p className="font-semibold">
            Email is set up - but only {settings.allowlist.length === 1 ? 'one address' : `${settings.allowlist.length} addresses`} can receive it.
          </p>
          <p>
            EMAIL_ALLOWLIST is set to {settings.allowlist.join(', ')}, a safety rail for testing with
            made-up people. Email to anyone else is recorded as &ldquo;Not sent&rdquo; and never
            retried. When real employees use the app, delete the EMAIL_ALLOWLIST line from
            backend/.env and restart the API.
          </p>
        </Notice>
      )}
      {env.encoding === 'utf-16' && (
        <Notice tone="info">
          <p>
            backend/.env is saved as UTF-16 (Windows Notepad does this). It was read, but save it
            as UTF-8 to be safe.
          </p>
        </Notice>
      )}
    </>
  );
}

function SettingsList({ settings }: { settings: EmailSettings }) {
  const env = settings.env_file;
  const rows: [string, ReactNode][] = [
    ['Sending', settings.enabled ? 'On' : 'Off (EMAIL_ENABLED)'],
    ['Mail server', `${settings.host}:${settings.port} (${settings.security === 'SSL' ? 'SSL' : 'STARTTLS'})`],
    ['Signs in as', settings.username ?? 'not set'],
    ['App password', settings.password],
    [
      'Sent from',
      settings.from_address ? `${settings.from_name} <${settings.from_address}>` : 'not set',
    ],
    ['Links in emails open', settings.links_point_to],
    [
      'Settings file',
      env.exists ? (
        <>
          <span className="break-all font-mono text-xs">{env.path}</span>
          {env.modified_at && (
            <span className="block text-xs text-text-subtle">
              Saved {formatInstant(env.modified_at)}
            </span>
          )}
        </>
      ) : (
        <span>
          None at <span className="break-all font-mono text-xs">{env.path}</span>
          {settings.from_environment.length > 0 && ' - using environment variables'}
        </span>
      ),
    ],
    ['API started', formatInstant(settings.started_at)],
  ];
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
      {rows.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs font-medium text-text-subtle">{label}</dt>
          <dd className="mt-0.5 break-words text-sm">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function TestResult({ result }: { result: EmailTestResult }) {
  if (result.ok) {
    return (
      <Notice tone="success">
        <p className="font-semibold">Sent to {result.to}.</p>
        <p>
          The mail server accepted it. If it is not in the inbox within a minute, check the spam
          folder.
        </p>
        {!result.allowlisted && (
          <p>
            Ordinary notices to this address are still held back by EMAIL_ALLOWLIST; the test
            ignores it.
          </p>
        )}
      </Notice>
    );
  }
  return (
    <Notice tone="danger">
      <p className="font-semibold">
        Not sent. Stopped at: {STAGE_LABELS[result.stage] ?? result.stage}.
      </p>
      {result.error && (
        <p className="rounded bg-surface/60 px-2 py-1 font-mono text-xs text-text">{result.error}</p>
      )}
      {result.hint && <p className="text-text">{result.hint}</p>}
    </Notice>
  );
}

/**
 * Is email actually going out? The answer used to live in a server log on
 * someone else's machine; this puts it, and a way to prove it, on the screen.
 */
export default function EmailDeliveryCard() {
  const queryClient = useQueryClient();
  const user = useAuth((s) => s.user);
  const [to, setTo] = useState(user?.email ?? '');
  const status = useQuery({ queryKey: ['email-status'], queryFn: fetchEmailStatus });

  const test = useMutation({
    mutationFn: () => sendTestEmail(to.trim() || undefined),
    meta: { errorFallback: 'Could not run the test.' },
    onSuccess: (result) => {
      // A 200 can still be a failed send: the server ran the test and reports
      // where it stopped. The card below keeps the full detail and the hint.
      if (result.ok) {
        toast.success(`Test email sent to ${result.to}`);
      } else {
        const stage = STAGE_LABELS[result.stage] ?? result.stage;
        toast.error(`Test email not sent — stopped at ${stage.toLowerCase()}`);
      }
      queryClient.setQueryData(['email-status'], {
        problem: status.data?.problem ?? null,
        settings: result.settings,
      });
      queryClient.invalidateQueries({ queryKey: ['ledger'] });
    },
  });

  const toggleEmail = useMutation({
    mutationFn: (enabled: boolean) => toggleEmailService(enabled),
    meta: { errorFallback: 'Could not update email service status.' },
    onSuccess: (result, enabled) => {
      toast.success(enabled ? 'Email service enabled' : 'Email service disabled');
      queryClient.setQueryData(['email-status'], result);
      queryClient.invalidateQueries({ queryKey: ['email-status'] });
      queryClient.invalidateQueries({ queryKey: ['ledger'] });
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    test.mutate();
  };

  const data = status.data;
  const ready = data && !data.problem;

  return (
    <Card>
      <CardHeader
        title="Email delivery"
        description="Whether this server can send email, and a test send to prove it."
        action={
          data ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant={data.settings.enabled ? 'danger' : 'primary'}
                loading={toggleEmail.isPending}
                onClick={() => toggleEmail.mutate(!data.settings.enabled)}
                title={data.settings.enabled ? 'Disable email service' : 'Enable email service'}
              >
                <Power size={13} />
                {data.settings.enabled ? 'Turn off email service' : 'Turn on email service'}
              </Button>
              <Badge tone={ready ? (data.settings.restart_needed ? 'warning' : 'success') : 'danger'}>
                {ready ? (data.settings.restart_needed ? 'Restart needed' : 'Set up') : 'Not sending'}
              </Badge>
            </div>
          ) : undefined
        }
      />
      <div className="space-y-4 px-4 py-4 sm:px-5">
        {data && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface-sunken p-3.5">
            <div className="space-y-0.5">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-text">Email Service Master Switch:</span>
                <Badge tone={data.settings.enabled ? 'success' : 'neutral'}>
                  {data.settings.enabled ? 'Active / ON' : 'Paused / OFF'}
                </Badge>
              </div>
              <p className="text-xs text-text-muted">
                {data.settings.enabled
                  ? 'Email dispatch is active. Approvals, booking confirmations, and reminders are sent via SMTP.'
                  : 'Email sending is currently turned off. In-app notifications continue as usual.'}
              </p>
            </div>
            <Button
              size="sm"
              variant={data.settings.enabled ? 'danger' : 'primary'}
              loading={toggleEmail.isPending}
              onClick={() => toggleEmail.mutate(!data.settings.enabled)}
            >
              <Power size={13} />
              {data.settings.enabled ? 'Turn OFF' : 'Turn ON'}
            </Button>
          </div>
        )}

        {status.isPending ? (
          <Skeleton className="h-28 w-full" />
        ) : status.isError ? (
          <Notice tone="danger">
            <p>Could not read the email settings: {errorMessage(status.error)}</p>
          </Notice>
        ) : (
          <>
            <Warnings settings={data!.settings} problem={data!.problem} />
            <SettingsList settings={data!.settings} />
          </>
        )}

        <form onSubmit={submit} className="space-y-3 border-t border-border pt-4">
          <label htmlFor="test-email-to" className="block text-sm font-medium">
            Send a test email
          </label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative min-w-0 flex-1">
              <Mail
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-subtle"
              />
              <Input
                id="test-email-to"
                type="email"
                value={to}
                onChange={(e) => setTo(e.target.value)}
                placeholder="you@designboxed.com"
                className="pl-9"
              />
            </div>
            <Button type="submit" loading={test.isPending} className="sm:w-auto">
              {!test.isPending && <Send size={15} />}
              Send test email
            </Button>
          </div>
          <p className="text-xs text-text-subtle">
            Signs in to the mail account and sends one message, then says exactly where it stopped
            if it fails. It can take up to a minute when the mail server cannot be reached.
          </p>
          {test.isError && (
            <Notice tone="danger">
              <p>{errorMessage(test.error, 'Could not run the test.')}</p>
            </Notice>
          )}
          {test.data && <TestResult result={test.data} />}
        </form>
      </div>
    </Card>
  );
}

/** A one-line warning for an admin's landing page when email is not reaching
 *  people, linking to the card above. Silent when all is well. */
export function EmailProblemBanner() {
  const status = useQuery({
    queryKey: ['email-status'],
    queryFn: fetchEmailStatus,
    staleTime: 5 * 60 * 1000,
  });
  const data = status.data;
  if (!data) return null;

  const { settings } = data;
  const message = data.problem
    ? `Email is not going out: ${data.problem}.`
    : settings.restart_needed
      ? 'Email settings were changed after the API started. Restart the API to use them.'
      : settings.allowlist.length > 0
        ? `Email is set up, but only ${settings.allowlist.join(', ')} receives it: EMAIL_ALLOWLIST in backend/.env holds back everyone else. Remove that line and restart the API to email all employees.`
        : null;
  if (!message) return null;

  return (
    <div
      role="status"
      className="flex flex-col gap-2 rounded-xl bg-warning-soft px-4 py-3 text-sm text-warning sm:flex-row sm:items-center sm:justify-between"
    >
      <span className="flex min-w-0 items-start gap-2.5">
        <AlertTriangle size={17} className="mt-0.5 shrink-0" />
        <span className="break-words">{message}</span>
      </span>
      <Link
        to="/notifications?tab=ledger"
        className="shrink-0 font-semibold underline underline-offset-4 hover:no-underline"
      >
        Check and send a test
      </Link>
    </div>
  );
}
