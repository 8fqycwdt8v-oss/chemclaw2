-- G4: per-event notification tracking. Each terminal-state transition surfaces
-- exactly once to the user via /api/notifications. NULL = unread; the route
-- sets NOW() after emitting.
ALTER TABLE synthesis_campaigns
  ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ NULL;
