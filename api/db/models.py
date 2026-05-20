"""SQLAlchemy 2.0 declarative models — one-to-one mapping of packages/db/src/schema/*.ts."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── helpers ──────────────────────────────────────────────────────────────────

def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _now() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), server_default=func.now(), nullable=False)


# ── users ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    email: Mapped[str | None] = mapped_column(sa.Text)
    role: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


# ── sessions ─────────────────────────────────────────────────────────────────

class AgentSession(Base):
    __tablename__ = "agent_sessions"

    project_key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    subpath: Mapped[str] = mapped_column(sa.Text, primary_key=True, server_default=sa.text("''"))
    entries: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'"))
    mtime: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    insert_seq: Mapped[int | None] = mapped_column(sa.BigInteger, sa.Identity(always=False))


# ── compounds ─────────────────────────────────────────────────────────────────

class Compound(Base):
    __tablename__ = "compounds"

    id: Mapped[uuid.UUID] = _uuid_pk()
    smiles: Mapped[str] = mapped_column(sa.Text, nullable=False)
    canon_smiles: Mapped[str | None] = mapped_column(sa.Text)
    name: Mapped[str | None] = mapped_column(sa.Text)
    cas_number: Mapped[str | None] = mapped_column(sa.Text)
    # bit(2048) stored as text; casts happen in raw SQL queries
    morgan_fp: Mapped[str | None] = mapped_column(sa.Text)
    morgan_fp_popcount: Mapped[int | None] = mapped_column(sa.Integer)
    fp_computed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)

    properties: Mapped[list[Property]] = relationship(back_populates="compound")


# ── reactions ─────────────────────────────────────────────────────────────────

class Reaction(Base):
    __tablename__ = "reactions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    rxn_smiles: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str | None] = mapped_column(sa.Text)
    conditions: Mapped[str | None] = mapped_column(sa.Text)
    drfp: Mapped[str | None] = mapped_column(sa.Text)
    fp_computed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)


# ── wiki ──────────────────────────────────────────────────────────────────────

class WikiPage(Base):
    __tablename__ = "wiki_pages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'"))
    content_text: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("''"))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    updated_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    needs_review: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    archived: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    maturity: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'exploratory'"))
    project: Mapped[str | None] = mapped_column(sa.Text)
    valid_from: Mapped[datetime] = _now()
    valid_to: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    chunks: Mapped[list[WikiChunk]] = relationship(back_populates="page", cascade="all, delete-orphan")
    citations: Mapped[list[WikiCitation]] = relationship(back_populates="page", cascade="all, delete-orphan")
    contradictions: Mapped[list[WikiContradiction]] = relationship(back_populates="page", cascade="all, delete-orphan")
    subscriptions: Mapped[list[WikiSubscription]] = relationship(back_populates="page", cascade="all, delete-orphan")
    tables: Mapped[list[WikiTable]] = relationship(back_populates="page", cascade="all, delete-orphan")


class WikiChunk(Base):
    __tablename__ = "wiki_chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    page_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False)
    chunk_idx: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))

    page: Mapped[WikiPage] = relationship(back_populates="chunks")

    __table_args__ = (sa.UniqueConstraint("page_id", "chunk_idx", name="wiki_chunks_page_chunk_unique"),)


class WikiCitation(Base):
    __tablename__ = "wiki_citations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    page_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False)
    citation_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_id: Mapped[str | None] = mapped_column(sa.Text)
    label: Mapped[str] = mapped_column(sa.Text, nullable=False)
    disputed: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("false"))

    page: Mapped[WikiPage] = relationship(back_populates="citations")


class WikiContradiction(Base):
    __tablename__ = "wiki_contradictions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    page_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False)
    citation_a: Mapped[str] = mapped_column(sa.Text, nullable=False)
    citation_b: Mapped[str] = mapped_column(sa.Text, nullable=False)
    proposed_winner: Mapped[str] = mapped_column(sa.Text, nullable=False)
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resolved_by: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _now()

    page: Mapped[WikiPage] = relationship(back_populates="contradictions")


class WikiSubscription(Base):
    __tablename__ = "wiki_subscriptions"

    user_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    page_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("wiki_pages.id", ondelete="CASCADE"), primary_key=True
    )
    last_seen_version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    created_at: Mapped[datetime] = _now()

    page: Mapped[WikiPage] = relationship(back_populates="subscriptions")


class WikiTable(Base):
    __tablename__ = "wiki_tables"

    id: Mapped[uuid.UUID] = _uuid_pk()
    page_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    anchor: Mapped[str | None] = mapped_column(sa.Text)
    headers: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    rows: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _now()

    page: Mapped[WikiPage] = relationship(back_populates="tables")


class WikiProposedEdit(Base):
    __tablename__ = "wiki_proposed_edits"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    citations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'"))
    proposed_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'pending'"))
    previous_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    reviewed_by: Mapped[str | None] = mapped_column(sa.Text)
    review_comment: Mapped[str | None] = mapped_column(sa.Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    applied_page_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("wiki_pages.id"))
    created_at: Mapped[datetime] = _now()


# ── campaigns ─────────────────────────────────────────────────────────────────

class SynthesisCampaign(Base):
    __tablename__ = "synthesis_campaigns"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_smiles: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'planning'"))
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    wiki_page_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("wiki_pages.id"))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    notified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    steps: Mapped[list[CampaignStep]] = relationship(back_populates="campaign", cascade="all, delete-orphan")


class CampaignStep(Base):
    __tablename__ = "campaign_steps"

    id: Mapped[uuid.UUID] = _uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("synthesis_campaigns.id", ondelete="CASCADE"), nullable=False
    )
    step_idx: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    reaction_smiles: Mapped[str | None] = mapped_column(sa.Text)
    conditions: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'pending'"))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    retry_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    next_retry_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    requires_approval: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    updated_at: Mapped[datetime] = _now()

    campaign: Mapped[SynthesisCampaign] = relationship(back_populates="steps")


# ── audit ─────────────────────────────────────────────────────────────────────

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


# ── rate limits ───────────────────────────────────────────────────────────────

class RateLimit(Base):
    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    window_start: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))


# ── feedback / overrides ──────────────────────────────────────────────────────

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


# ── tool permissions ──────────────────────────────────────────────────────────

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


# ── budgets ───────────────────────────────────────────────────────────────────

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


# ── todos ─────────────────────────────────────────────────────────────────────

class AgentTodo(Base):
    __tablename__ = "agent_todos"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    user_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'pending'"))
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


# ── external facts ────────────────────────────────────────────────────────────

class ExternalFact(Base):
    __tablename__ = "external_facts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_text: Mapped[str | None] = mapped_column(sa.Text)
    first_seen: Mapped[datetime] = _now()
    last_seen: Mapped[datetime] = _now()
    fetched_by: Mapped[str] = mapped_column(sa.Text, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("source_type", "source_id", name="external_facts_source_unique"),
    )


# ── properties ────────────────────────────────────────────────────────────────

class Property(Base):
    __tablename__ = "properties"

    id: Mapped[uuid.UUID] = _uuid_pk()
    compound_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("compounds.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    value_num: Mapped[float | None] = mapped_column(sa.Double)
    value_text: Mapped[str | None] = mapped_column(sa.Text)
    unit: Mapped[str | None] = mapped_column(sa.Text)
    method: Mapped[str | None] = mapped_column(sa.Text)
    source_citation_id: Mapped[str | None] = mapped_column(sa.Text)
    measured_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)

    compound: Mapped[Compound] = relationship(back_populates="properties")


# ── papers ────────────────────────────────────────────────────────────────────

class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    doi: Mapped[str | None] = mapped_column(sa.Text)
    pubmed_id: Mapped[str | None] = mapped_column(sa.Text)
    url: Mapped[str | None] = mapped_column(sa.Text)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    abstract: Mapped[str | None] = mapped_column(sa.Text)
    content_text: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _now()
    created_by: Mapped[str | None] = mapped_column(sa.Text)

    chunks: Mapped[list[PaperChunk]] = relationship(back_populates="paper", cascade="all, delete-orphan")


class PaperChunk(Base):
    """Body chunks of a paper, embedded for hybrid retrieval.

    No `created_by` column on purpose — ownership inherits from the parent
    paper via FK + `ON DELETE CASCADE`, matching the wiki_pages → wiki_chunks
    relationship. The chunk_idx + ON CONFLICT clause in
    api/db/queries/papers.py:insert_paper_chunks make re-ingest idempotent.
    """

    __tablename__ = "paper_chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    paper_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False)
    chunk_idx: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(sa.Text)
    page: Mapped[int | None] = mapped_column(sa.Integer)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    created_at: Mapped[datetime] = _now()

    paper: Mapped[Paper] = relationship(back_populates="chunks")

    __table_args__ = (sa.UniqueConstraint("paper_id", "chunk_idx", name="paper_chunks_paper_chunk_unique"),)


# ── eval runs ─────────────────────────────────────────────────────────────────

class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    fixtures_total: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    fixtures_passed: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    scores: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'"))
    notes: Mapped[str | None] = mapped_column(sa.Text)
