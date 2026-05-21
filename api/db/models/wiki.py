"""Wiki tables: pages, chunks, citations, contradictions, subscriptions,
embedded tables, proposed edits.

All wiki child tables back-populate to `WikiPage`. The CASCADE delete on
each FK matches the migration files.
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
