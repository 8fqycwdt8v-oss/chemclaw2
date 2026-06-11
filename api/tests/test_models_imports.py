"""Back-compat enforcement: every model class must remain importable from
`api.db.models` regardless of which submodule it now lives in.

The package split (base / sessions / chem / wiki / campaigns / knowledge)
keeps the import surface stable via re-exports in `__init__.py`. Any
future submodule reorganisation must keep this test passing — otherwise
external callers (and the query layer) break with `ImportError`.
"""
from __future__ import annotations


def test_all_model_classes_resolve_off_api_db_models() -> None:
    from api.db.models import (
        AgentFeedback,
        AgentOverride,
        AgentSession,
        AuditLog,
        Base,
        CampaignStep,
        CodeExecution,
        Compound,
        EvalRun,
        ExternalFact,
        Hypothesis,
        HypothesisRanking,
        Investigation,
        Paper,
        PaperChunk,
        ProjectBudget,
        ProjectBudgetSpend,
        Property,
        RateLimit,
        Reaction,
        ReactionConditionPrediction,
        ReactionOutcome,
        SynthesisCampaign,
        ToolPermission,
        User,
        WikiChunk,
        WikiCitation,
        WikiContradiction,
        WikiPage,
        WikiProposedEdit,
        WikiSubscription,
        WikiTable,
        WorldModelEntry,
    )

    # Every class must be registered against the same Base.metadata — otherwise
    # cross-table FKs and relationship strings won't resolve at query time.
    expected = {
        AgentFeedback, AgentOverride, AgentSession, AuditLog,
        CampaignStep, CodeExecution, Compound, EvalRun, ExternalFact,
        Hypothesis, HypothesisRanking, Investigation, Paper, PaperChunk,
        ProjectBudget, ProjectBudgetSpend, Property, RateLimit, Reaction,
        ReactionConditionPrediction, ReactionOutcome, SynthesisCampaign,
        ToolPermission, User, WikiChunk, WikiCitation, WikiContradiction,
        WikiPage, WikiProposedEdit, WikiSubscription, WikiTable,
        WorldModelEntry,
    }
    mapper_classes = {m.class_ for m in Base.registry.mappers}
    missing = expected - mapper_classes
    assert not missing, f"classes not in Base.registry: {missing}"


def test_compound_property_cross_submodule_relationship_resolves() -> None:
    """Compound lives in models/chem.py; Property lives there too, but the
    cross-module path is exercised by the wiki/knowledge relationships
    (WikiPage→WikiChunk works inside one submodule, but the Compound
    self-test catches the relationship-registry wiring up-front)."""
    from api.db.models import Compound, Property
    # The Mapped[list[Property]] annotation must resolve via Base.registry.
    rel = Compound.__mapper__.relationships["properties"]
    assert rel.mapper.class_ is Property
    back = Property.__mapper__.relationships["compound"]
    assert back.mapper.class_ is Compound
