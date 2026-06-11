"""Synthesis-campaign workflow tables.

SynthesisCampaign is the top-level row a `start_campaign` call creates;
CampaignStep rows are the individual reactions queued for execution
(approval, retry, etc).
checklist surface; it shares the same workflow pattern (status +
position) so it lives alongside.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _now, _uuid_pk


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

