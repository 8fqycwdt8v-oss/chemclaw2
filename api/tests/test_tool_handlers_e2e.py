"""End-to-end tool-handler coverage via the SDK adapter.

These tests close a real gap: today the SDK handler dispatch path
(`tool.handler({...}) -> {"content": [{"type": "text", "text": "..."}]}`)
is only exercised at the adapter layer (`test_build_chemclaw_mcp_server`)
and at the query layer (e.g. `test_investigations.py`). What was missing
is *the wired-together middle*: a model-style invocation that calls the
actual SDK-decorated handler with a JSON-shaped args dict, against a
real DB.

Coverage strategy — one representative happy-path test per tool module
(chem / knowledge / investigation / external / campaign), plus a few
focused error-envelope checks. We deliberately don't re-test
query-layer logic; that's covered elsewhere. Here we only verify:

  1. The handler runs without raising into the SDK loop.
  2. The SDK envelope shape is correct (content[0].type == "text",
     text is JSON-decodable, `is_error` not set on success).
  3. The decoded payload contains the keys the agent expects.
  4. Validation errors surface as `{"error": "..."}` payloads, not
     `is_error: True` (those are reserved for unhandled exceptions —
     CLAUDE.md §security-4).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest


def _decode(response: dict[str, Any]) -> dict[str, Any]:
    """Pull the JSON payload out of an SDK content envelope."""
    assert response.get("is_error") is not True, response
    assert response["content"][0]["type"] == "text"
    return json.loads(response["content"][0]["text"])


# ── chem tools ───────────────────────────────────────────────────────────────


async def test_chem_compound_similarity_search_validates_fingerprint(
    session_factory,
) -> None:
    """Non-2048-bit input must surface a structured {error} payload — not
    raise into the SDK loop, not return is_error."""
    from api.agent.tools_chem import build_chem_tools
    tools = {t.name: t for t in build_chem_tools(session_factory)}
    response = await tools["compound_similarity_search"].handler({
        "fingerprint_bits": "010101",  # too short
    })
    payload = _decode(response)
    assert "error" in payload
    assert "2048" in payload["error"]


async def test_chem_list_reaction_outcomes_rejects_non_uuid(
    session_factory,
) -> None:
    """UUID validation happens inside the handler — should return error
    payload (not raise) when given garbage."""
    from api.agent.tools_chem import build_chem_tools
    tools = {t.name: t for t in build_chem_tools(session_factory)}
    response = await tools["list_reaction_outcomes"].handler({
        "reaction_id": "not-a-uuid",
    })
    payload = _decode(response)
    assert "error" in payload
    assert "UUID" in payload["error"]


async def test_chem_substructure_search_round_trip(session_factory) -> None:
    """Happy path: the handler returns a `candidates` list (possibly
    empty) without error."""
    from api.agent.tools_chem import build_chem_tools
    tools = {t.name: t for t in build_chem_tools(session_factory)}
    response = await tools["substructure_search"].handler({
        "smarts": "c1ccccc1",
        "max_candidates": 10,
    })
    payload = _decode(response)
    assert payload["smarts"] == "c1ccccc1"
    assert isinstance(payload["candidates"], list)


# ── investigation tools ──────────────────────────────────────────────────────


async def test_investigation_start_and_list_round_trip(
    session_factory, user_id: str,
) -> None:
    """Create an investigation via the SDK handler, then list it via the
    list handler. Both go through wrap_tool's envelope path."""
    from api.agent.tools_investigation import build_investigation_tools
    tools = {
        t.name: t for t in build_investigation_tools(user_id, "s-test", session_factory)
    }
    create_resp = await tools["start_investigation"].handler({
        "title": "Test investigation",
        "objective": "Verify SDK handler dispatch end-to-end.",
    })
    created = _decode(create_resp)
    assert created["status"] == "active"
    assert created["id"]
    inv_id = created["id"]

    list_resp = await tools["list_investigations"].handler({})
    listed = _decode(list_resp)
    assert any(row["id"] == inv_id for row in listed["investigations"])


async def test_investigation_start_validates_title(
    session_factory, user_id: str,
) -> None:
    """Empty title returns {error}, not is_error."""
    from api.agent.tools_investigation import build_investigation_tools
    tools = {
        t.name: t for t in build_investigation_tools(user_id, "s-test", session_factory)
    }
    response = await tools["start_investigation"].handler({
        "title": "   ",
        "objective": "anything",
    })
    payload = _decode(response)
    assert "error" in payload
    assert "title" in payload["error"]


async def test_investigation_world_model_owner_scoping(
    session_factory, user_id: str,
) -> None:
    """world_model_add on a stranger's investigation returns {error},
    proving the handler does the cross-table ownership check."""
    from api.agent.tools_investigation import build_investigation_tools
    from api.db.queries.investigations import create_investigation

    stranger = f"u-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        iid = await create_investigation(
            db, "Stranger's inv", "Stranger's objective", stranger,
        )

    tools = {
        t.name: t for t in build_investigation_tools(user_id, "s-test", session_factory)
    }
    response = await tools["world_model_add"].handler({
        "investigation_id": iid,
        "kind": "fact",
        "content": "should be rejected",
    })
    payload = _decode(response)
    assert "error" in payload
    assert "not owned" in payload["error"]


async def test_investigation_run_code_requires_anchor(
    session_factory, user_id: str,
) -> None:
    """Bound session_id=None at factory time + no investigation_id arg
    must surface the {error} guard (not raise) — proves the handler's
    arg-validation path is reachable through the adapter."""
    from api.agent.tools_investigation import build_investigation_tools
    tools = {
        t.name: t for t in build_investigation_tools(user_id, None, session_factory)
    }
    response = await tools["run_code"].handler({"code": "print(1)"})
    payload = _decode(response)
    assert "error" in payload
    assert "investigation_id" in payload["error"] or "session_id" in payload["error"]


async def test_investigation_list_code_executions_round_trip(
    session_factory, user_id: str,
) -> None:
    """List handler returns an envelope with `executions` list (empty is
    fine; we're testing the dispatch + ownership scoping)."""
    from api.agent.tools_investigation import build_investigation_tools
    tools = {
        t.name: t for t in build_investigation_tools(user_id, f"s-{uuid.uuid4().hex[:8]}", session_factory)
    }
    response = await tools["list_code_executions"].handler({"limit": 5})
    payload = _decode(response)
    assert isinstance(payload["executions"], list)


# ── knowledge tools ──────────────────────────────────────────────────────────


async def test_knowledge_wiki_lookup_returns_results_shape(
    session_factory, user_id: str,
) -> None:
    """wiki_lookup with `query` exercises the FTS branch; verify the
    response is a structured payload, not is_error."""
    from api.agent.tools_knowledge import build_knowledge_tools
    tools = {t.name: t for t in build_knowledge_tools(user_id, session_factory)}
    response = await tools["wiki_lookup"].handler({
        "query": "any test query",
        "mode": "fts",
        "limit": 3,
    })
    payload = _decode(response)
    assert "results" in payload or "error" in payload


async def test_knowledge_record_external_fact_round_trip(
    session_factory, user_id: str,
) -> None:
    """Record + read back: the SDK-handler write path returns {id} that
    the underlying query function actually persisted."""
    from api.agent.tools_knowledge import build_knowledge_tools
    from api.db.queries.knowledge import get_external_fact_by_source_id

    tools = {t.name: t for t in build_knowledge_tools(user_id, session_factory)}
    source_id = f"test-fact-{uuid.uuid4().hex[:8]}"
    response = await tools["record_external_fact"].handler({
        "source_type": "test",
        "source_id": source_id,
        "content_text": "A short factual claim.",
        "payload": {"k": "v"},
    })
    payload = _decode(response)
    assert payload["id"]

    async with session_factory() as db:
        row = await get_external_fact_by_source_id(db, source_id)
    assert row is not None
    assert row["source_type"] == "test"


async def test_knowledge_record_contradiction_rejects_bad_winner(
    session_factory, user_id: str,
) -> None:
    """`proposed_winner` outside {a, b, inconclusive} surfaces {error}."""
    from api.agent.tools_knowledge import build_knowledge_tools
    tools = {t.name: t for t in build_knowledge_tools(user_id, session_factory)}
    response = await tools["record_contradiction"].handler({
        "page_slug": "nonexistent",
        "citation_a": "c-1",
        "citation_b": "c-2",
        "proposed_winner": "neither",  # invalid
        "reason": "test",
    })
    payload = _decode(response)
    assert "error" in payload
    assert "proposed_winner" in payload["error"]


async def test_knowledge_verify_citation_handles_unknown_id(
    session_factory, user_id: str,
) -> None:
    """Unknown citation_id returns found=False with no exception."""
    from api.agent.tools_knowledge import build_knowledge_tools
    tools = {t.name: t for t in build_knowledge_tools(user_id, session_factory)}
    response = await tools["verify_citation"].handler({
        "citation_id": f"unknown-{uuid.uuid4().hex[:8]}",
    })
    payload = _decode(response)
    assert payload["found"] is False
    assert payload["stale"] is None


# ── external tools ───────────────────────────────────────────────────────────


async def test_external_propose_retrosynthesis_validates_smiles(
    session_factory, user_id: str,
) -> None:
    """Empty SMILES surfaces {error} (validation in handler, no network call)."""
    from api.agent.tools_external import build_external_tools
    tools = {t.name: t for t in build_external_tools(user_id, session_factory)}
    response = await tools["propose_retrosynthesis"].handler({
        "target_smiles": "",
    })
    payload = _decode(response)
    assert "error" in payload


async def test_external_propose_retrosynthesis_deep_reports_when_extras_missing(
    session_factory, user_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the [retrosynth] extra, the deep tool should report a
    structured {error} rather than crash. We force the ImportError path
    via a sys.modules stub."""
    import sys
    monkeypatch.setitem(sys.modules, "aizynthfinder", None)  # type: ignore[arg-type]
    monkeypatch.setitem(sys.modules, "aizynthfinder.aizynthfinder", None)  # type: ignore[arg-type]
    from api.agent.tools_external import build_external_tools
    tools = {t.name: t for t in build_external_tools(user_id, session_factory)}
    response = await tools["propose_retrosynthesis_deep"].handler({
        "target_smiles": "CCO",
    })
    payload = _decode(response)
    # Either error envelope or the wrapper-level retrosynth degraded
    # message — both are acceptable; what we verify is non-crash.
    assert isinstance(payload, dict)


async def test_external_name_to_structure_validates_input(
    session_factory, user_id: str,
) -> None:
    """Empty name surfaces {error}."""
    from api.agent.tools_external import build_external_tools
    tools = {t.name: t for t in build_external_tools(user_id, session_factory)}
    response = await tools["name_to_structure"].handler({"name": "   "})
    payload = _decode(response)
    assert "error" in payload


# ── campaign tools ───────────────────────────────────────────────────────────


async def test_campaign_start_without_session_returns_error(
    session_factory, user_id: str,
) -> None:
    """No session_id bound at factory time → start_synthesis_campaign
    returns {error}, doesn't raise."""
    from api.agent.tools_campaign import build_campaign_tools
    tools = {
        t.name: t for t in build_campaign_tools(user_id, None, session_factory)
    }
    response = await tools["start_synthesis_campaign"].handler({})
    payload = _decode(response)
    assert "error" in payload
    assert "session_id" in payload["error"]


async def test_campaign_start_with_session_round_trip(
    session_factory, user_id: str,
) -> None:
    """Create a campaign and verify the handler returns a campaign_id +
    planning status."""
    from api.agent.tools_campaign import build_campaign_tools
    sid = f"s-{uuid.uuid4().hex[:8]}"
    tools = {
        t.name: t for t in build_campaign_tools(user_id, sid, session_factory)
    }
    response = await tools["start_synthesis_campaign"].handler({
        "target_smiles": "CC(=O)Oc1ccccc1C(=O)O",
    })
    payload = _decode(response)
    assert payload["status"] == "planning"
    assert payload["campaign_id"]


async def test_campaign_record_feedback_validates_score(
    session_factory, user_id: str,
) -> None:
    """`score` must be 1 or -1 — anything else surfaces {ok: False}."""
    from api.agent.tools_campaign import build_campaign_tools
    tools = {
        t.name: t for t in build_campaign_tools(user_id, "s-test", session_factory)
    }
    response = await tools["record_feedback"].handler({
        "turn_index": 0,
        "score": 5,  # invalid
    })
    payload = _decode(response)
    assert payload["ok"] is False
    assert "score" in payload["error"]


async def test_campaign_declare_parameter_space_validates_output_key(
    session_factory, user_id: str,
) -> None:
    """V1 only supports yield_pct as output key — other keys rejected."""
    from api.agent.tools_campaign import build_campaign_tools
    tools = {
        t.name: t for t in build_campaign_tools(user_id, "s-test", session_factory)
    }
    response = await tools["declare_campaign_parameter_space"].handler({
        "campaign_id": str(uuid.uuid4()),
        "parameter_spec": {
            "inputs": [
                {"key": "temp", "type": "continuous", "min": 20.0, "max": 100.0}
            ],
            "outputs": [{"key": "purity_pct", "direction": "maximize"}],
        },
    })
    payload = _decode(response)
    assert payload["ok"] is False
    assert "yield_pct" in payload["error"]


async def test_campaign_register_compound_property_requires_value(
    session_factory, user_id: str,
) -> None:
    """Neither value_num nor value_text given → {error}."""
    from api.agent.tools_campaign import build_campaign_tools
    tools = {
        t.name: t for t in build_campaign_tools(user_id, "s-test", session_factory)
    }
    response = await tools["register_compound_property"].handler({
        "compound_id": str(uuid.uuid4()),
        "name": "logP",
    })
    payload = _decode(response)
    assert "error" in payload
    assert "value_num" in payload["error"] or "value_text" in payload["error"]


async def test_campaign_record_predicted_conditions_validates_rxn_smiles(
    session_factory, user_id: str,
) -> None:
    """Missing '>>' separator in rxn_smiles surfaces {error}."""
    from api.agent.tools_campaign import build_campaign_tools
    tools = {
        t.name: t for t in build_campaign_tools(user_id, "s-test", session_factory)
    }
    response = await tools["record_predicted_conditions"].handler({
        "rxn_smiles": "CCO",  # no '>>'
        "conditions": {
            "catalysts": [], "solvents": [], "reagents": [], "temperature_c": None,
        },
        "model": "test-model",
        "source": "manual",
    })
    payload = _decode(response)
    assert "error" in payload
    assert ">>" in payload["error"]
