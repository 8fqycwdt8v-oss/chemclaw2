-- Fixed-window rate limit counters shared across all app instances.
-- Each (key, window_start) pair holds a count; window_start is epoch-ms
-- rounded down to the window boundary. The upsert increment is atomic —
-- ON CONFLICT DO UPDATE takes a row lock before executing the update,
-- so concurrent increments from multiple machines are serialized correctly.
CREATE TABLE rate_limits (
  key          TEXT    NOT NULL,
  window_start BIGINT  NOT NULL,
  count        INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (key, window_start)
);

-- Expire old windows to prevent unbounded table growth.
-- pg_cron or the application can sweep rows older than 1 hour.
CREATE INDEX rate_limits_window_idx ON rate_limits (window_start);
