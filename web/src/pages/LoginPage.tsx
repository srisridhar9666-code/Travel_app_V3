import { useMutation } from '@tanstack/react-query';
import { AlertCircle, ArrowRight, Plane } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';

import { COMPANY_NAME, CompanyCredit, CompanyLogo, CreatorCredit, Logo, LogoLockup } from '@/components/Logo';
import { ThemeToggle } from '@/components/ThemeToggle';
import { Button, Field, Input } from '@/components/ui';
import { errorMessage, forgotPassword, login } from '@/lib/api';
import { useAuth } from '@/store/auth';

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const signIn = useAuth((s) => s.signIn);
  const authenticated = useAuth((s) => s.isAuthenticated());
  // Why the last session ended ("Your account is deactivated..."), set by the
  // store when the server refused a request. Shown until they try again.
  const notice = useAuth((s) => s.notice);
  const clearNotice = useAuth((s) => s.clearNotice);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<'signin' | 'forgot'>('signin');

  const signInMutation = useMutation({
    mutationFn: () => login(email.trim(), password),
    // These two show their error in the box right above the button; a toast
    // would only repeat it.
    meta: { errorToast: false },
    onSuccess: (result) => {
      signIn(result);
      toast.success(`Welcome, ${result.user.full_name.split(' ')[0]}`);
      const from = (location.state as { from?: string } | null)?.from ?? '/';
      navigate(from, { replace: true });
    },
    onError: (err) => setError(errorMessage(err, 'Could not sign in.')),
  });

  const forgotMutation = useMutation({
    mutationFn: () => forgotPassword(email.trim()),
    meta: { errorToast: false },
    onSuccess: (result) => {
      toast.success(result.detail);
      setMode('signin');
    },
    onError: (err) => setError(errorMessage(err)),
  });

  // Already signed in: skip the form entirely.
  if (authenticated) return <Navigate to="/" replace />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    clearNotice();
    if (mode === 'signin') signInMutation.mutate();
    else forgotMutation.mutate();
  };

  const busy = signInMutation.isPending || forgotMutation.isPending;
  const shown = error ?? notice;

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1fr_1.1fr] portrait:min-h-[52rem]">
      {/* Form side */}
      <div className="flex flex-col px-6 py-8 sm:px-12">
        <div className="flex items-center justify-between">
          <LogoLockup />
          <ThemeToggle />
        </div>

        <div className="flex flex-1 items-center justify-center">
          <div className="w-full max-w-sm animate-slide-up">
            <h1 className="text-2xl font-semibold tracking-tight">
              {mode === 'signin' ? 'Sign in' : 'Reset your password'}
            </h1>
            <p className="mt-2 text-sm leading-relaxed text-text-muted">
              {mode === 'signin'
                ? 'Travel, cab and accommodation requests for the field team.'
                : "Enter your work email and we'll send you a link to set a new password."}
            </p>

            <form onSubmit={submit} className="mt-8 space-y-4" noValidate>
              <Field label="Work email" htmlFor="email" required>
                <Input
                  id="email"
                  type="email"
                  autoComplete="username"
                  autoFocus
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@designboxed.com"
                  aria-invalid={Boolean(shown)}
                />
              </Field>

              {mode === 'signin' && (
                <Field label="Password" htmlFor="password" required>
                  <Input
                    id="password"
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••••"
                    aria-invalid={Boolean(shown)}
                  />
                </Field>
              )}

              {shown && (
                <div
                  role="alert"
                  className="flex items-start gap-2 rounded-md bg-danger-soft px-3 py-2.5 text-xs text-danger"
                >
                  <AlertCircle size={14} className="mt-px shrink-0" />
                  <span>{shown}</span>
                </div>
              )}

              <Button type="submit" size="lg" loading={busy} className="w-full">
                {mode === 'signin' ? 'Sign in' : 'Send reset link'}
                {!busy && <ArrowRight size={15} />}
              </Button>
            </form>

            <button
              type="button"
              onClick={() => {
                setMode(mode === 'signin' ? 'forgot' : 'signin');
                setError(null);
                clearNotice();
              }}
              className="mt-5 text-xs text-text-muted underline-offset-4 hover:text-text hover:underline"
            >
              {mode === 'signin' ? 'Forgot your password?' : 'Back to sign in'}
            </button>

            <p className="mt-8 border-t border-border pt-5 text-xs leading-relaxed text-text-subtle">
              Accounts are created by an administrator. If you do not have one yet, ask your admin
              to send you an invitation.
            </p>
          </div>
        </div>

        {/* Under the form rather than on the brand panel, which phones never see. */}
        <CompanyCredit className="justify-center pt-6" />
        <CreatorCredit className="pt-1.5 justify-center" />
      </div>

      {/* Brand side. Hidden on small screens - ground staff sign in from phones,
          and a decorative panel there would just push the form off-screen. */}
      <div className="relative hidden overflow-hidden bg-surface-sunken lg:block">
        <div
          aria-hidden
          className="absolute inset-0 opacity-[0.04]"
          style={{
            backgroundImage:
              'linear-gradient(rgb(var(--text)) 1px, transparent 1px), linear-gradient(90deg, rgb(var(--text)) 1px, transparent 1px)',
            backgroundSize: '56px 56px',
          }}
        />
        <div
          aria-hidden
          className="absolute -right-24 -top-24 h-96 w-96 rounded-full bg-brand/10 blur-3xl"
        />

        <div className="relative flex h-full flex-col justify-between p-12">
          <Logo className="h-60 w-auto self-start" />

          <div className="max-w-md">
            <Plane size={22} className="mb-5 text-brand" strokeWidth={2} />
            <p className="text-xl font-medium leading-snug tracking-tight">
              Every request, approval and booking in one place &mdash; with a record of who decided
              what, and when.
            </p>
            <p className="mt-4 text-sm leading-relaxed text-text-muted">
              Travel, cabs and stays for field teams
            </p>
          </div>

          <div className="flex items-end gap-4">
            <CompanyLogo className="h-28 w-auto" />
            <p className="pb-1 text-2xs uppercase leading-relaxed tracking-widest text-text-subtle">
              A product of
              <br />
              {COMPANY_NAME}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
