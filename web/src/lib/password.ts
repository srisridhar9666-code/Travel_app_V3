const MIN_PASSWORD_LENGTH = 4;

/** Simple password policy: users can use simple passwords including their name/email. */
export function passwordIssues(password: string, _email?: string, _name?: string): string[] {
  const issues: string[] = [];
  if (password.length < MIN_PASSWORD_LENGTH) {
    issues.push(`At least ${MIN_PASSWORD_LENGTH} characters`);
  }
  return issues;
}
