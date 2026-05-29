-- Drive sync state: per-drive Microsoft Graph delta cursor + last-run status.
-- Drives the SharePoint/OneDrive sync worker (wired in a later slice). The
-- stored delta_token lets each run fetch only items changed since the last
-- sync; last_synced_at gates the wall-clock (~12h) cadence; last_status /
-- last_error surface the most recent run to the health endpoint.
CREATE TABLE IF NOT EXISTS drive_sync_state (
  drive_id        TEXT        PRIMARY KEY,
  delta_token     TEXT,
  last_synced_at  TIMESTAMPTZ,
  last_status     TEXT,
  last_error      TEXT,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
