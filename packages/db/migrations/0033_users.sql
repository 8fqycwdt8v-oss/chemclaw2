-- Clerk-synced user mirror. Source of truth lives in Clerk; this table is a
-- denormalised projection populated by the /api/webhooks/clerk route handler.
-- Audit joins, "who is admin" queries, and post-deletion record retention all
-- read from here without an extra Clerk API call per row.
CREATE TABLE IF NOT EXISTS users (
  user_id    TEXT PRIMARY KEY,
  email      TEXT,
  role       TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS users_role_idx ON users (role);

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY users_all ON users FOR ALL USING (true) WITH CHECK (true);
