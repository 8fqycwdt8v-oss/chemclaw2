"""External-knowledge + investigation tables.

ExternalFact / Paper / PaperChunk are the cached scientific-literature
surface. Investigation / WorldModelEntry / Hypothesis /
HypothesisRanking are the agent's persistent research-thread state
(Kosmos-style world model + Co-Scientist tournament). CodeExecution
audits agent-authored Python runs from the sandbox.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _now, _uuid_pk


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
    api/db/queries/paper_chunks.py:insert_paper_chunks make re-ingest idempotent.
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


class Investigation(Base):
    """Long-horizon research thread that outlives any single chat session.

    Holds the open-ended objective (Kosmos-style) and groups the world-model
    entries + hypotheses that arise while pursuing it. Owner-scoped via
    `created_by`; `session_id` is nullable so sessions can come and go
    without orphaning investigations.
    """

    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[str | None] = mapped_column(sa.Text)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    objective: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'active'"))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class WorldModelEntry(Base):
    """Kosmos-style structured persistent state entry.

    One row per atomic fact / assumption / open_question / evidence — kept
    granular so the agent can mark individual entries superseded as it
    refines its view rather than rewriting one giant JSON blob. `confidence`
    is an optional 0–1 self-reported score; `payload` is a JSONB escape
    hatch for kind-specific extras (citations, fingerprints, etc.).
    """

    __tablename__ = "world_model_entries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False,
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    confidence: Mapped[float | None] = mapped_column(sa.Float)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'active'"))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Hypothesis(Base):
    """A scientific hypothesis under tournament-style evaluation.

    `parent_id` chains evolved children back to their predecessor (Google
    AI Co-Scientist's Evolution agent pattern). `elo_rating` is the Co-
    Scientist Ranking agent's tournament signal; `hypothesis_rankings`
    stores the audit trail of pairwise comparisons that produced it.
    """

    __tablename__ = "hypotheses"

    id: Mapped[uuid.UUID] = _uuid_pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("hypotheses.id", ondelete="SET NULL"),
    )
    statement: Mapped[str] = mapped_column(sa.Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'proposed'"))
    elo_rating: Mapped[float] = mapped_column(sa.Float, nullable=False, server_default=sa.text("1000.0"))
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class HypothesisRanking(Base):
    """Pairwise hypothesis comparison — Co-Scientist tournament audit log.

    Each row records one judged matchup; the `hypotheses.elo_rating` of
    both contestants is eager-updated in the same transaction (see
    api/db/queries/hypotheses.py:record_pairwise_ranking).
    """

    __tablename__ = "hypothesis_rankings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False,
    )
    hypothesis_a_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("hypotheses.id", ondelete="CASCADE"), nullable=False,
    )
    hypothesis_b_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("hypotheses.id", ondelete="CASCADE"), nullable=False,
    )
    winner: Mapped[str] = mapped_column(sa.Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(sa.Text)
    decided_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    decided_at: Mapped[datetime] = _now()


class CodeExecution(Base):
    """Audit record for one agent-authored Python snippet run by the sandbox.

    Either `investigation_id` or `session_id` must be set (CHECK constraint
    in the migration) so every execution is traceable to a research thread
    or a chat turn. `status` is the high-level outcome; `exit_code` is the
    process exit code with sentinels 124 (timeout) and 137 (SIGKILL).
    """

    __tablename__ = "code_executions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("investigations.id", ondelete="CASCADE"),
    )
    session_id: Mapped[str | None] = mapped_column(sa.Text)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    language: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'python'"))
    stdout: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("''"))
    stderr: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("''"))
    exit_code: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'completed'"))
    # PNG artefacts captured from the sandbox tempdir. Shape:
    # [{filename, mime, size_bytes, b64}]. See Tier 3 §M.
    artifacts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = _now()
