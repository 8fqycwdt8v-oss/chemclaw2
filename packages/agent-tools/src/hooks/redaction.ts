const CONTROLLED_TERMS = ['fentanyl', 'carfentanil', 'methamphetamine', 'heroin'];
const SSN_RE = /\b\d{3}-\d{2}-\d{4}\b/g;

export function checkToolInput(
  _toolName: string,
  toolInput: Record<string, unknown>,
): { action: 'allow'; input?: Record<string, unknown> } | { action: 'block'; reason: string } {
  const inputStr = JSON.stringify(toolInput);

  // Check for controlled substance terms — block entirely
  for (const term of CONTROLLED_TERMS) {
    if (inputStr.toLowerCase().includes(term)) {
      return {
        action: 'block',
        reason: `Tool input blocked: contains controlled substance term "${term}"`,
      };
    }
  }

  // Check for SSN pattern — redact and allow
  const sanitized = inputStr.replace(SSN_RE, '[REDACTED-SSN]');
  if (sanitized !== inputStr) {
    return {
      action: 'allow',
      input: JSON.parse(sanitized) as Record<string, unknown>,
    };
  }

  return { action: 'allow' };
}
