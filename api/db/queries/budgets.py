"""Budget queries — Python port of packages/db/src/queries/budgets.ts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

BudgetPeriod = str  # 'day' | 'week' | 'month'


def period_start_for(period: BudgetPeriod, now: datetime | None = None) -> datetime:
    d = (now or datetime.now(tz=timezone.utc)).replace(tzinfo=timezone.utc)
    if period == "day":
        return d.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        d = d.replace(hour=0, minute=0, second=0, microsecond=0)
        # Rewind to Monday (ISO week start)
        return d - timedelta(days=(d.weekday()))
    # month
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def get_project_budget(db: AsyncSession, project_key: str) -> dict[str, Any] | None:
    result = await db.execute(
        text("SELECT * FROM project_budgets WHERE project_key = :pk"),
        {"pk": project_key},
    )
    row = result.one_or_none()
    return dict(row._mapping) if row else None


async def get_budget_with_spend(
    db: AsyncSession, project_key: str
) -> dict[str, Any] | None:
    budget = await get_project_budget(db, project_key)
    if not budget:
        return None
    period_start = period_start_for(budget["period"])
    result = await db.execute(
        text("""
            SELECT tool_calls, experiments, tokens
            FROM project_budget_spend
            WHERE project_key = :pk AND period_start = :ps
        """),
        {"pk": project_key, "ps": period_start},
    )
    spend = result.one_or_none()
    return {
        **budget,
        "period_start": period_start,
        "spend": dict(spend._mapping) if spend else {"tool_calls": 0, "experiments": 0, "tokens": 0},
    }


async def increment_spend(
    db: AsyncSession,
    project_key: str,
    period: BudgetPeriod,
    tool_calls: int = 0,
    experiments: int = 0,
    tokens: int = 0,
) -> None:
    period_start = period_start_for(period)
    async with db.begin():
        await db.execute(
            text("""
                INSERT INTO project_budget_spend (project_key, period_start, tool_calls, experiments, tokens)
                VALUES (:pk, :ps, :tc, :ex, :tok)
                ON CONFLICT (project_key, period_start) DO UPDATE SET
                    tool_calls = project_budget_spend.tool_calls + EXCLUDED.tool_calls,
                    experiments = project_budget_spend.experiments + EXCLUDED.experiments,
                    tokens = project_budget_spend.tokens + EXCLUDED.tokens,
                    updated_at = now()
            """),
            {"pk": project_key, "ps": period_start, "tc": tool_calls, "ex": experiments, "tok": tokens},
        )


async def try_consume_tool_call(
    db: AsyncSession,
    project_key: str,
) -> dict[str, Any] | None:
    """Atomically reserve one tool_call against the project's budget.

    Returns:
      - None: no budget configured for this project (caller should allow).
      - {"ok": True,  "used": N, "cap": cap_or_None}: reserved; tool may run.
      - {"ok": False, "used": N, "cap": cap}: cap would be exceeded; block.

    Replaces the read-then-increment pattern used previously by the
    PreToolUse hook, which had a TOCTOU window where two concurrent tool
    calls could both pass the check before either incremented. Here the
    INSERT/UPDATE WHERE clause does the cap check inside the same SQL
    statement, so concurrent calls serialize correctly.

    Note: this charges for the call up-front (before the tool executes),
    not after. A tool that subsequently fails still counts against the cap
    — that's the intended semantics: the cap budgets attempts, not just
    successes. PostToolUse no longer needs to increment tool_calls (it
    still increments tokens/experiments via increment_spend).
    """
    # All reads + the conditional upsert happen inside one transaction so
    # we don't trip "A transaction is already begun" when this is called on
    # a shared session that previously executed an autobegun SELECT.
    async with db.begin():
        budget_row = await db.execute(
            text(
                "SELECT period, tool_calls_cap FROM project_budgets "
                "WHERE project_key = :pk"
            ),
            {"pk": project_key},
        )
        b = budget_row.one_or_none()
        if b is None:
            return None
        period = b.period
        cap = b.tool_calls_cap
        period_start = period_start_for(period)

        if cap is not None and cap <= 0:
            return {"ok": False, "used": 0, "cap": cap}

        if cap is None:
            sql = text("""
                INSERT INTO project_budget_spend (project_key, period_start, tool_calls)
                VALUES (:pk, :ps, 1)
                ON CONFLICT (project_key, period_start) DO UPDATE
                SET tool_calls = project_budget_spend.tool_calls + 1,
                    updated_at = now()
                RETURNING tool_calls
            """)
            params: dict[str, Any] = {"pk": project_key, "ps": period_start}
        else:
            sql = text("""
                INSERT INTO project_budget_spend (project_key, period_start, tool_calls)
                VALUES (:pk, :ps, 1)
                ON CONFLICT (project_key, period_start) DO UPDATE
                SET tool_calls = project_budget_spend.tool_calls + 1,
                    updated_at = now()
                WHERE project_budget_spend.tool_calls + 1 <= :cap
                RETURNING tool_calls
            """)
            params = {"pk": project_key, "ps": period_start, "cap": cap}

        result = await db.execute(sql, params)
        row = result.one_or_none()
        if row is not None:
            return {"ok": True, "used": row.tool_calls, "cap": cap}
        # WHERE clause rejected the UPDATE — cap would be exceeded. Read
        # current value for telemetry; safe because UPDATE didn't fire.
        cur = await db.execute(
            text(
                "SELECT tool_calls FROM project_budget_spend "
                "WHERE project_key = :pk AND period_start = :ps"
            ),
            {"pk": project_key, "ps": period_start},
        )
        cur_row = cur.one_or_none()
        used = cur_row.tool_calls if cur_row else cap
        return {"ok": False, "used": used, "cap": cap}


async def record_override(
    db: AsyncSession,
    session_id: str,
    user_id: str,
    gate_name: str,
    justification: str,
    prompt: str,
) -> None:
    import hashlib
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
    async with db.begin():
        await db.execute(
            text("""
                INSERT INTO agent_overrides
                    (session_id, user_id, gate_name, justification, prompt_hash)
                VALUES (:sid, :uid, :gate, :justification, :hash)
            """),
            {
                "sid": session_id, "uid": user_id, "gate": gate_name,
                "justification": justification, "hash": prompt_hash,
            },
        )


async def upsert_project_budget(
    db: AsyncSession,
    project_key: str,
    period: str,
    tool_calls_cap: int | None,
    experiments_cap: int | None,
    tokens_cap: int | None,
    updated_by: str,
) -> None:
    """Insert or update a project budget policy."""
    async with db.begin():
        await db.execute(
            text("""
                INSERT INTO project_budgets
                    (project_key, period, tool_calls_cap, experiments_cap, tokens_cap, updated_by)
                VALUES (:pk, :period, :tc, :ex, :tok, :uid)
                ON CONFLICT (project_key) DO UPDATE SET
                    period = EXCLUDED.period,
                    tool_calls_cap = EXCLUDED.tool_calls_cap,
                    experiments_cap = EXCLUDED.experiments_cap,
                    tokens_cap = EXCLUDED.tokens_cap,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
            """),
            {
                "pk": project_key,
                "period": period,
                "tc": tool_calls_cap,
                "ex": experiments_cap,
                "tok": tokens_cap,
                "uid": updated_by,
            },
        )


async def delete_project_budget(db: AsyncSession, project_key: str) -> bool:
    """Delete a project budget policy. Returns True if a row was deleted."""
    async with db.begin():
        result = await db.execute(
            text("DELETE FROM project_budgets WHERE project_key = :pk RETURNING project_key"),
            {"pk": project_key},
        )
        return result.one_or_none() is not None
