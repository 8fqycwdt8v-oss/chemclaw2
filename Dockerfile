FROM python:3.11-slim AS builder
WORKDIR /app

# Install system dependencies needed to build asyncpg, cryptography, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies in a virtual env (cached layer).
COPY pyproject.toml .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -e "." && \
    /opt/venv/bin/pip install --no-cache-dir \
        packages/mcp-servers/mcp_molfp \
        packages/mcp-servers/mcp_rxnfp

# Copy application code after deps are cached.
COPY api/ ./api/
COPY packages/mcp-servers/ ./packages/mcp-servers/
COPY .claude/skills ./.claude/skills

# ── Runtime image ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runner
WORKDIR /app

# Runtime system libs only (no compilers).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
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

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
