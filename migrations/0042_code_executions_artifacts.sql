-- Phase C / Tier 3 §M: matplotlib figure capture for sandbox runs.
--
-- The sandbox prelude sets matplotlib to the Agg backend; after the
-- subprocess exits, the sandbox library walks its tempdir for *.png
-- files, base64-encodes them (up to 1.5 MB total per execution), and
-- attaches them to the SandboxResult. The api-layer `run_code` tool
-- persists that list here.
--
-- JSONB shape: [{filename, mime, size_bytes, b64}]. Inline storage
-- (no separate artifacts table) — Postgres TOAST handles the ~2 MB
-- rows transparently and the 1.5 MB cap keeps `list_code_executions`
-- responses reasonable when they elide the b64 payload.
ALTER TABLE code_executions
    ADD COLUMN IF NOT EXISTS artifacts JSONB NOT NULL DEFAULT '[]'::jsonb;
