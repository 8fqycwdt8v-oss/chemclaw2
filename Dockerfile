FROM python:3.11-slim AS builder
WORKDIR /app

# Install system dependencies needed to build asyncpg, cryptography, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies in a virtual env. Copy `pyproject.toml`
# first so the base `pip install -e .` step is cacheable when only
# source files change.
COPY pyproject.toml .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -e "."

# Copy MCP server source BEFORE installing them — pip needs the local
# directories to exist. (Pre-existing bug from before Tier 3: the
# previous Dockerfile ran the MCP install before copying the source,
# which would have failed in a clean build. Docker isn't part of CI so
# nobody noticed.)
COPY packages/mcp-servers/ ./packages/mcp-servers/
RUN /opt/venv/bin/pip install --no-cache-dir \
        packages/mcp-servers/mcp_molfp \
        packages/mcp-servers/mcp_rxnfp \
        packages/mcp-servers/mcp_retrosynth \
        packages/mcp-servers/mcp_rxn_conditions \
        packages/mcp-servers/mcp_codesandbox \
        packages/mcp-servers/mcp_tabular

# Copy application code after deps are cached.
COPY api/ ./api/
COPY .claude/skills ./.claude/skills

# ── Runtime image ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runner
WORKDIR /app

# Runtime system libs only (no compilers).
# `bubblewrap` (bwrap) is the strongest isolation tier the
# mcp_codesandbox sandbox can pick — see sandbox.py:_bwrap_available.
# When CAP_SYS_ADMIN is denied (default Docker seccomp), the sandbox
# probes bwrap and falls back to `unshare`/plain subprocess. Shipping
# bwrap costs ~70 KB and lets hosts that DO grant CAP_SYS_ADMIN get
# container-grade isolation for free.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    bubblewrap \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system --gid 1001 appuser \
    && adduser --system --uid 1001 --ingroup appuser appuser

COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv
COPY --from=builder --chown=appuser:appuser /app/api ./api
COPY --from=builder --chown=appuser:appuser /app/packages ./packages
COPY --from=builder --chown=appuser:appuser /app/.claude ./.claude

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PROJECT_ROOT=/app
ENV SKILLS_DIR=/app/.claude/skills

USER appuser
EXPOSE 8080

# Local healthcheck so `docker run` + orchestrators that don't honour
# Fly's external probe still know whether the API is live. Fly's
# external probe (configured in fly.toml) does the heavy lifting in
# production. Both target /api/health on port 8080.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status == 200 else 1)" \
    || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
