const BLOCKED_TERMS = [
  'synthesize fentanyl', 'fentanyl synthesis', 'make fentanyl',
  'synthesize carfentanil', 'carfentanil synthesis',
  'synthesize methamphetamine', 'meth synthesis', 'cook meth',
  'synthesize heroin', 'heroin synthesis',
  'synthesize mdma', 'mdma synthesis',
  'schedule i synthesis', 'schedule ii synthesis',
];

export function scheduledSubstanceGate(prompt: string): { blocked: boolean; reason?: string } {
  const lower = prompt.toLowerCase();
  for (const term of BLOCKED_TERMS) {
    if (lower.includes(term.toLowerCase())) {
      return {
        blocked: true,
        reason: `Request blocked: synthesis instructions for scheduled/controlled substances are not permitted. Term matched: "${term}"`,
      };
    }
  }
  return { blocked: false };
}
