"""Session, user, and per-session infrastructure tables.

User, AgentSession, rate-limit + audit + tool-permission + budget +
feedback / override + eval-run tables — everything that's part of the
agent runtime's session-keeping layer, not the chemistry / wiki /
knowledge content layers.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _now, _uuid_pk


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    email: Mapped[str | None] = mapped_column(sa.Text)
    role: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    project_key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    subpath: Mapped[str] = mapped_column(sa.Text, primary_key=True, server_default=sa.text("''"))
    entries: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'"))
    mtime: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    insert_seq: Mapped[int | None] = mapped_column(sa.BigInteger, sa.Identity(always=False))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    table_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    row_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    operation: Mapped[str] = mapped_column(sa.Text, nullable=False)
    old_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    changed_by: Mapped[str | None] = mapped_column(sa.Text)
    changed_at: Mapped[datetime] = _now()


class RateLimit(Base):
    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    window_start: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))


class AgentFeedback(Base):
    __tablename__ = "agent_feedback"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    turn_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    score: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(sa.Text)
    user_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        sa.UniqueConstraint("session_id", "turn_index", "user_id", name="agent_feedback_session_turn_user"),
    )


class AgentOverride(Base):
    __tablename__ = "agent_overrides"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    user_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    gate_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    justification: Mapped[str] = mapped_column(sa.Text, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = _now()


class ToolPermission(Base):
    __tablename__ = "tool_permissions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    scope: Mapped[str] = mapped_column(sa.Text, nullable=False)
    scope_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    tool_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    mode: Mapped[str] = mapped_column(sa.Text, nullable=False)
    updated_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        sa.UniqueConstraint("scope", "scope_id", "tool_name", name="tool_permissions_unique"),
    )


class ProjectBudget(Base):
    __tablename__ = "project_budgets"

    project_key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    period: Mapped[str] = mapped_column(sa.Text, nullable=False)
    tool_calls_cap: Mapped[int | None] = mapped_column(sa.BigInteger)
    experiments_cap: Mapped[int | None] = mapped_column(sa.Integer)
    tokens_cap: Mapped[int | None] = mapped_column(sa.BigInteger)
    updated_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    updated_at: Mapped[datetime] = _now()
    created_at: Mapped[datetime] = _now()


class ProjectBudgetSpend(Base):
    __tablename__ = "project_budget_spend"

    project_key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    period_start: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), primary_key=True)
    tool_calls: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, server_default=sa.text("0"))
    experiments: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    tokens: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, server_default=sa.text("0"))
    updated_at: Mapped[datetime] = _now()


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    fixtures_total: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    fixtures_passed: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    scores: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'"))
    notes: Mapped[str | None] = mapped_column(sa.Text)
