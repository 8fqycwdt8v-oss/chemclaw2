import { verifyWebhook } from '@clerk/nextjs/webhooks';
import type { WebhookEvent } from '@clerk/nextjs/webhooks';
import { NextRequest, NextResponse } from 'next/server';
import { upsertUserFromClerk, softDeleteUser } from '@chemclaw2/db';

/**
 * Clerk → DB user mirror webhook. Source of truth is Clerk; this route just
 * projects user.created / user.updated / user.deleted events into the `users`
 * table so audit joins and "who is admin" queries can resolve user identity
 * without hitting the Clerk API per row.
 *
 * Signature is verified by Clerk's built-in svix wrapper. The signing secret
 * is read from `CLERK_WEBHOOK_SIGNING_SECRET` automatically — without it
 * `verifyWebhook` throws and the route returns 400, which Clerk retries with
 * exponential backoff.
 *
 * Middleware excludes /api/webhooks/* from auth gating (apps/web/middleware.ts).
 */
export async function POST(req: NextRequest) {
  let evt: WebhookEvent;
  try {
    evt = await verifyWebhook(req);
  } catch (err) {
    console.error('[clerk-webhook] signature verification failed:', err);
    return NextResponse.json({ error: 'invalid signature' }, { status: 400 });
  }

  try {
    switch (evt.type) {
      case 'user.created':
      case 'user.updated': {
        const data = evt.data;
        const primaryEmail = data.email_addresses?.find(
          (e) => e.id === data.primary_email_address_id,
        )?.email_address ?? null;
        const role = (data.public_metadata as { role?: string } | null)?.role ?? null;
        await upsertUserFromClerk({ userId: data.id, email: primaryEmail, role });
        break;
      }
      case 'user.deleted': {
        if (evt.data.id) await softDeleteUser(evt.data.id);
        break;
      }
      default:
        // Ignore other event types (session.*, organization.*) — we only mirror users.
        break;
    }
  } catch (err) {
    console.error('[clerk-webhook] DB sync failed:', err);
    return NextResponse.json({ error: 'sync failed' }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}
