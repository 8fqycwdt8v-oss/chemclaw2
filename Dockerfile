FROM node:22-alpine AS base
RUN corepack enable pnpm

# Install dependencies only when needed
FROM base AS deps
WORKDIR /app
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/db/package.json packages/db/
COPY packages/agent-tools/package.json packages/agent-tools/
COPY workers/fp-worker/package.json workers/fp-worker/
RUN pnpm install --frozen-lockfile

# Build the application
FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm turbo build --filter=@chemclaw2/web

# Python + chemistry deps for the MCP fingerprinting servers
# (spawned per request by /api/fingerprint and by the worker process).
FROM base AS runner
WORKDIR /app
ENV NODE_ENV=production

RUN apk add --no-cache python3 py3-pip
ENV PATH="/opt/venv/bin:$PATH"

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

# Web app standalone bundle
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/public ./apps/web/public 2>/dev/null || true

# Worker source + workspace deps (tsx runs the worker; ESM resolution in Node
# without .js extensions across workspaces is fragile, so we keep TS source).
COPY --from=builder --chown=nextjs:nodejs /app/workers/fp-worker ./workers/fp-worker
COPY --from=builder --chown=nextjs:nodejs /app/packages ./packages
COPY --from=builder --chown=nextjs:nodejs /app/node_modules ./node_modules

# Python venv + MCP servers (spawned per request by /api/fingerprint and by the worker)
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir rdkit drfp mcp && \
    /opt/venv/bin/pip install --no-cache-dir -e ./packages/mcp-servers/mcp_molfp \
                                              -e ./packages/mcp-servers/mcp_rxnfp && \
    chown -R nextjs:nodejs /opt/venv

USER nextjs
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

# Process selected by Fly via [processes] in fly.toml.
# Default to the web app for local docker run.
CMD ["node", "apps/web/server.js"]
