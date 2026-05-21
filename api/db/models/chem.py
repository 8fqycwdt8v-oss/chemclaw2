"""Chemistry-domain tables: compounds, properties, reactions, outcomes,
condition predictions.

`Property` lives here (not in `knowledge`) so the `Compound.properties`
relationship resolves without a cross-module import. ReactionOutcome
and ReactionConditionPrediction both reference `campaign_steps.id` by
string-FK only — no Python-level import of `CampaignStep` needed.
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

    outcomes: Mapped[list[ReactionOutcome]] = relationship(back_populates="reaction", cascade="all, delete-orphan")


class ReactionOutcome(Base):
    __tablename__ = "reaction_outcomes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    reaction_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("reactions.id", ondelete="CASCADE"), nullable=False
    )
    campaign_step_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("campaign_steps.id", ondelete="SET NULL")
    )
    eln_experiment_id: Mapped[str | None] = mapped_column(sa.Text)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    yield_pct: Mapped[float | None] = mapped_column(sa.Numeric)
    conditions_actual: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    observations: Mapped[str | None] = mapped_column(sa.Text)
    failure_reason: Mapped[str | None] = mapped_column(sa.Text)
    recorded_at: Mapped[datetime] = _now()
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)

    reaction: Mapped[Reaction] = relationship(back_populates="outcomes")


class ReactionConditionPrediction(Base):
    __tablename__ = "reaction_condition_predictions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    reaction_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("reactions.id", ondelete="CASCADE")
    )
    rxn_smiles: Mapped[str] = mapped_column(sa.Text, nullable=False)
    drfp_bits: Mapped[str | None] = mapped_column(sa.Text)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(sa.Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(sa.Double)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    used_in_step_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("campaign_steps.id", ondelete="SET NULL")
    )
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = _now()
