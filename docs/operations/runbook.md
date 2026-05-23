# Incident runbook

One page per failure mode. Every entry: what you see, how to confirm,
what to do, when to escalate.

## Database unreachable

**Symptom**: `/api/readiness` returns 503; every request 500s with
`OperationalError`; `readiness_db_check_failed` in logs.

**Confirm**:
```bash
fly postgres ssh
psql -c 'SELECT 1'
```

**Fix**:
- If `psql` works but the app can't connect: stale pool. Restart the app
  process group (`fly machines restart --process-group web`).
- If `psql` fails: Fly Postgres outage. Check Fly status page; failover
  is automatic for HA clusters.

## Worker backlog growing

**Symptom**: `/api/readiness` reports `backlog_degraded: true`; the
fingerprint queue is climbing.

**Confirm**:
```bash
fly logs --process-group worker | grep heartbeat
```
The heartbeat line shows `cycle_n` and `items_processed`. If the cycle is
running but `items_processed` is 0, the worker is up but stuck on an MCP
call. If the cycle isn't running, the worker is dead.

**Fix**:
- `fly machines restart --process-group worker`.
- If it crashes immediately: bump MCP_LOG_LEVEL=DEBUG and look at the
  next 30 seconds of logs.

## Rate-limit denials spiking

**Symptom**: `rate_limit_denied` log lines climbing; legitimate users
seeing 429s.

**Confirm**:
```sql
SELECT bucket, count(*)
FROM rate_limits
WHERE window_start > (extract(epoch from now())*1000 - 60000)
GROUP BY bucket
ORDER BY count(*) DESC
LIMIT 20;
```
(`bucket` is the prefix of `key` before the first `:`.)

**Fix**:
- Single user hammering an endpoint: confirm with their `user_id` and
  reach out before raising limits.
- Across many users: limits too tight for actual traffic. Edit the
  `rate_limit("bucket", N)` call site, ship a PR.

## JWKS fetch errors

**Symptom**: `jwks_client_error` in logs; all auth returning 401.

**Confirm**: Clerk dashboard → JWKS endpoint status.

**Fix**:
- Network blip — wait. JWKS cache TTL is 1 h; cache is in-process so a
  restart re-fetches.
- Clerk rotated keys — chemclaw2 should pick up automatically on TTL
  expiry. If urgent: `fly machines restart --process-group web`.
- Wrong `CLERK_JWKS_URL` env var: confirm against Clerk dashboard.

## MCP subprocess hang

**Symptom**: `mcp_call_timeout` in logs; specific tools returning generic
"Tool failed" errors.

**Confirm**: which `server_module` shows up in the timeout log. Reproduce
locally:
```bash
docker compose run --rm app python -m mcp_<name>.server < /tmp/probe.json
```

**Fix**:
- RDKit / DRFP can ignore SIGTERM until the C frame returns. Worker has
  a 2 s SIGKILL backstop — if that's also failing, something is in a busy
  loop. Patch the MCP server.

## Hostile traffic / SSRF attempt

**Symptom**: `SSRF blocked: ... non-public address` in logs at WARNING.

**Action**: this is the guard working — no action needed. Audit the
caller:
```sql
SELECT key, count(*) FROM rate_limits WHERE key LIKE 'wiki%' GROUP BY key;
```

If the same `user_id` is hitting SSRF-block repeatedly, treat as
abuse — revoke admin perms / block at the edge.

## Substance gate spike

**Symptom**: `scheduled_substance_attempt` log lines climbing.

**Action**: each is one chat-turn block. Look at the `prompt_len` field
to gauge volume. Cross-reference Clerk user ids; coordinate with policy
team.

## CORS misconfig at deploy

**Symptom**: `fly deploy` succeeds but `fly logs` shows
`RuntimeError: CORS_ALLOWED_ORIGINS must be set to an explicit
non-localhost origin list when ENV=prod`.

**Fix**: this is the prod CORS gate refusing to start (CLAUDE.md §security).
Set `CORS_ALLOWED_ORIGINS` via `fly secrets set` to an explicit
non-localhost origin list, then `fly deploy` again.
