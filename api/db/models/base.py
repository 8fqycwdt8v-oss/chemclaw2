"""Shared SQLAlchemy 2.0 declarative base + column helpers.

Every model submodule under `api.db.models` inherits from `Base` here so
they all share one `MetaData`. The `__init__` of this package imports
every submodule so that `Base.registry` resolves cross-table
relationship strings (e.g. `relationship("Property", ...)`) without the
caller having to know which submodule a class lives in.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _now() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), server_default=func.now(), nullable=False)
