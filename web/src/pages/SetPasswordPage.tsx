import { useMutation, useQuery } from '@tanstack/react-query';
import { AlertCircle, ShieldCheck } from 'lucide-react';
import { useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';

import { CompanyCredit, CreatorCredit, LogoLockup } from '@/components/Logo';
import { PasswordChecklist } from '@/components/PasswordChecklist';
import { ThemeToggle } from '@/components/ThemeToggle';
import { Button, Field, Input, Spinner } from '@/components/ui';
import { errorMessage, previewToken, setPassword } from '@/lib/api';
import { passwordIssues } from '@/lib/password';
import { useAuth } from '@/store/auth';

export default function SetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get('token') ?? '';

  const [password, setPasswordValue] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);

  const preview = useQuery({
    queryKey: ['token', token],
    queryFn: () => previewToken(token),
    enabled: Boolean(token),
    retry: false,
  });

  const issues = useMemo(
    () => passwordIssues(password, preview.data?.email, preview.data?.full_name),
    [password, preview.data],
  );
  const mismatch = confirm.length > 0 && password !== confirm;
  const ready = password.length > 0 && issues.length === 0 && !mismatch && confirm.length > 0;

  const mutation = useMutation({
    mutationFn: () => setPassword(token, password),
    // The error box sits right above the button; a toast would only repeat it.
    meta: { errorToast: false },
    onSuccess: () => {
      // Whoever redeemed this link is almost certainly not whoever was last
      // signed in on this browser. Without clearing, accepting an invite on a
      // colleague's machine drops you straight into their session.
      useAuth.getState().signOut();
      toast.success('Password set. Sign in to continue.');
      navigate('/login', { replace: true });
    },
    onError: (err) => setError(errorMessage(err, 'Could not set your password.')),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    mutation.mutate();
  };

  const isInvite = preview.data?.purpose === 'INVITE';

  return (
    <div className="flex min-h-dvh flex-col bg-canvas px-6 py-8 portrait:min-h-[52rem]">
      <div className="mx-auto flex w-full max-w-sm items-center justify-between">
        <LogoLockup />
        <ThemeToggle />
      </div>

      <div className="flex flex-1 items-center justify-center">
        <div className="w-full max-w-sm animate-slide-up">
          {!token || preview.isError ? (
            <div className="text-center">
              <AlertCircle size={28} className="mx-auto text-danger" />
              <h1 className="mt-4 text-xl font-semibold tracking-tight">This link is not valid</h1>
              <p className="mt-2 text-sm leading-relaxed text-text-muted">
                Invitation and reset links expire. Ask an administrator to send you a fresh one.
              </p>
              <Link to="/login" className="mt-6 inline-block">
                <Button variant="secondary">Back to sign in</Button>
              </Link>
            </div>
          ) : preview.isPending ? (
            <div className="flex justify-center py-12">
              <Spinner className="h-5 w-5" />
            </div>
          ) : (
            <>
              <ShieldCheck size={24} className="text-brand" strokeWidth={2} />
              <h1 className="mt-4 text-2xl font-semibold tracking-tight">
                {isInvite ? 'Set your password' : 'Choose a new password'}
              </h1>
              <p className="mt-2 text-sm leading-relaxed text-text-muted">
                {isInvite ? 'Welcome, ' : 'Signed in as '}
                <span className="font-medium text-text">{preview.data.full_name}</span> &mdash;{' '}
                {preview.data.email}
              </p>

              <form onSubmit={submit} className="mt-8 space-y-4" noValidate>
                <Field label="New password" htmlFor="password" required>
                  <Input
                    id="password"
                    type="password"
                    autoComplete="new-password"
                    autoFocus
                    required
                    value={password}
                    onChange={(e) => setPasswordValue(e.target.value)}
                    placeholder="••••••••••"
                  />
                </Field>

                <Field
                  label="Confirm password"
                  htmlFor="confirm"
                  required
                  error={mismatch ? 'Passwords do not match.' : null}
                >
                  <Input
                    id="confirm"
                    type="password"
                    autoComplete="new-password"
                    required
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    placeholder="••••••••••"
                    aria-invalid={mismatch}
                  />
                </Field>

                <PasswordChecklist password={password} issues={issues} />

                {error && (
                  <div
                    role="alert"
                    className="flex items-start gap-2 rounded-md bg-danger-soft px-3 py-2.5 text-xs text-danger"
                  >
                    <AlertCircle size={14} className="mt-px shrink-0" />
                    <span>{error}</span>
                  </div>
                )}

                <Button
                  type="submit"
                  size="lg"
                  className="w-full"
                  disabled={!ready}
                  loading={mutation.isPending}
                >
                  {isInvite ? 'Set password and continue' : 'Update password'}
                </Button>
              </form>
            </>
          )}
        </div>
      </div>

      <CompanyCredit className="mx-auto w-full max-w-sm justify-center pt-6" />
      <CreatorCredit className="mx-auto w-full max-w-sm pt-1.5 justify-center" />
    </div>
  );
}
