"""SQLAlchemy 2.0 declarative models — one-to-one mapping of the migrations.

Re-exports every model class so existing `from api.db.models import X`
imports keep working unchanged after the file → package split. Equally
important: importing this package eagerly loads every submodule, which
populates `Base.registry` with every class — required for
`relationship("ClassName")` string lookups to resolve across submodules
(e.g. `WikiPage` → `WikiChunk` in wiki.py; `Compound` → `Property` in
chem.py).
"""
from __future__ import annotations

from .base import Base
from .campaigns import AgentTodo, CampaignStep, SynthesisCampaign
from .chem import (
    Compound,
    Property,
    Reaction,
    ReactionConditionPrediction,
    ReactionOutcome,
)
from .knowledge import (
    CodeExecution,
    ExternalFact,
    Hypothesis,
    HypothesisRanking,
    Investigation,
    Paper,
    PaperChunk,
    WorldModelEntry,
)
from .sessions import (
    AgentFeedback,
    AgentOverride,
    AgentSession,
    AuditLog,
    EvalRun,
    ProjectBudget,
    ProjectBudgetSpend,
    RateLimit,
    ToolPermission,
    User,
)
from .wiki import (
    WikiChunk,
    WikiCitation,
    WikiContradiction,
    WikiPage,
    WikiProposedEdit,
    WikiSubscription,
    WikiTable,
)

__all__ = [
    "AgentFeedback",
    "AgentOverride",
    "AgentSession",
    "AgentTodo",
    "AuditLog",
    "Base",
    "CampaignStep",
    "CodeExecution",
    "Compound",
    "EvalRun",
    "ExternalFact",
    "Hypothesis",
    "HypothesisRanking",
    "Investigation",
    "Paper",
    "PaperChunk",
    "ProjectBudget",
    "ProjectBudgetSpend",
    "Property",
    "RateLimit",
    "Reaction",
    "ReactionConditionPrediction",
    "ReactionOutcome",
    "SynthesisCampaign",
    "ToolPermission",
    "User",
    "WikiChunk",
    "WikiCitation",
    "WikiContradiction",
    "WikiPage",
    "WikiProposedEdit",
    "WikiSubscription",
    "WikiTable",
    "WorldModelEntry",
]
