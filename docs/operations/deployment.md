# Deployment runbook

chemclaw2 deploys to Fly.io. CI builds the Docker image (`Dockerfile`,
multi-stage, non-root) and `.github/workflows/deploy.yml` runs
`flyctl deploy --remote-only` on green CI to `main`.

## First-time setup

```bash
fly auth login
fly apps create chemclaw2
fly secrets set \
    ANTHROPIC_API_KEY=sk-ant-… \
    OPENAI_API_KEY=sk-… \
    DATABASE_URL=postgres://… \
    AZURE_TENANT_ID=00000000-0000-0000-0000-000000000000 \
    AZURE_BACKEND_CLIENT_ID=00000000-0000-0000-0000-000000000000 \
    AZURE_REQUIRED_ROLE=chemclaw.user \
    ADMIN_USER_IDS=<entra-oid> \
    CORS_ALLOWED_ORIGINS=https://app.example.com \
    ENV=prod \
    LOG_FORMAT=json
fly postgres create --name chemclaw2-db   # or attach an existing one
fly postgres attach chemclaw2-db --app chemclaw2
```

Add the secret for the deploy workflow:

```bash
gh secret set FLY_API_TOKEN --body "$(fly auth token)"
```

## Routine deploys

Open a PR → CI goes green → merge to `main` → `deploy.yml` ships it.
Manual deploy:

```bash
fly deploy --remote-only
```

## Scaling

```bash
# Add web replicas (fly handles load balancing).
fly scale count 3 --process-group web

# Bigger VM (default is shared-cpu-1x / 1 GB for web, 512 MB worker).
fly scale memory 2048 --process-group web

# Set autostop policy.
fly autoscale set min=1 max=5 --process-group web
```

Database pool: each web replica opens `DB_POOL_SIZE + DB_POOL_MAX_OVERFLOW`
connections to Postgres. Confirm that `replicas × (size + overflow) <
postgres max_connections × 0.8` (leaves headroom for the worker + admin
sessions).

## Rolling back

```bash
fly releases                                 # list past deployments
fly deploy --image <image-sha>               # redeploy a previous build
```

For database schema problems: each migration runs in `--single-transaction`,
so a partial-failure migration rolls back cleanly. Re-apply once fixed.
For "the migration shipped but we want it gone", write a reversing
migration as the next slot rather than `DROP`ing schema by hand in prod —
the migration log is the source of truth.

## Health checks & drain

- **`/api/health`** (liveness, no DB) — Fly probe target. If this returns
  500/503 Fly restarts the machine.
- **`/api/readiness`** (DB ping + fingerprint backlog) — wire this to
  load-balancer drain triggers (or to a separate Fly check group) so a
  blip doesn't restart the process.

## Logs

```bash
fly logs                         # live tail
fly logs --json | jq '.message'  # structured (when LOG_FORMAT=json)
fly logs | grep request_id=abc   # follow one request across instances
```

Set `LOG_FORMAT=json` in prod so log aggregators (Datadog, Vector,
Loki) get one JSON object per line including `request_id`, `route`,
`status`, `latency_ms`, and every `extra=` field flattened.

## Common issues

- **"Connection refused" from app**: postgres is down or `DATABASE_URL`
  is wrong. `fly postgres ssh` then `psql` to confirm.
- **429s after a deploy**: rate-limit table bloat from a previous high-
  traffic spike. The `sweep_rate_limit_rows` worker runs every cycle,
  but a one-off `DELETE FROM rate_limits WHERE window_start < extract(epoch from now()-interval '2 hours')*1000`
  clears the backlog immediately.
- **Worker stuck**: heartbeat log line every cycle includes
  `cycle_n`. If `cycle_n` hasn't ticked, the worker is wedged — restart
  the process group: `fly machines restart --process-group worker`.
- **MCP subprocess hang**: `fp_worker` enforces a per-call timeout with
  SIGTERM → SIGKILL backstop. If you see `mcp_call_timeout` in logs,
  the bug is in the MCP server. Bump verbosity with `MCP_LOG_LEVEL=DEBUG`
  and reproduce.
