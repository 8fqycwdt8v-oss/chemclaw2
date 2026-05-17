import { logger } from './logger';

let installed = false;

/**
 * Attach `unhandledRejection`/`uncaughtException` handlers exactly once per
 * process. Subsequent calls are no-ops so multiple entrypoints (web worker,
 * fp-worker, scripts) can safely opt in.
 */
export function installProcessHandlers(component: string): void {
  if (installed) return;
  installed = true;
  process.on('unhandledRejection', (reason) => {
    logger.error('unhandled_rejection', { component }, reason);
  });
  process.on('uncaughtException', (err) => {
    logger.error('uncaught_exception', { component }, err);
  });
}
