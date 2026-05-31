"""Tool-layer + query-layer tests for the four AI-Scientist quality gates.

  - Feature 1: critique_figure (+ attach_artifact_critique)
  - Feature 2: review_draft (+ draft_reviews queries + curator bucket)
  - Feature 3: check_citations
  - Feature 4: check_hypothesis_novelty (+ hypotheses.novelty round-trip)

LLM and embedding boundaries are monkeypatched; the DB is real. Tool
handlers are invoked through the SDK adapter envelope, matching
test_tool_handlers_e2e.py.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest


def _decode(response: dict[str, Any]) -> dict[str, Any]:
    assert response.get("is_error") is not True, response
    assert response["content"][0]["type"] == "text"
    return json.loads(response["content"][0]["text"])


async def _new_investigation(session_factory, user_id: str) -> str:
    from api.db.queries.investigations import create_investigation
    async with session_factory() as db:
        return await create_investigation(
            db, "T", "objective", created_by=user_id, session_id=None,
        )


# ── Feature 1: figure critique ──────────────────────────────────────────────

# base64("hello") — any valid base64 works; the VLM call is mocked.
_PNG_B64 = "aGVsbG8="


async def _exec_with_figure(session_factory, user_id: str) -> str:
    from api.db.queries.code_executions import insert_execution
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        return await insert_execution(
            db, code="plt.savefig('f.png')", stdout="", stderr="",
            exit_code=0, duration_ms=10, status="completed", created_by=user_id,
            session_id=sid,
            artifacts=[{"filename": "f.png", "mime": "image/png",
                        "size_bytes": 5, "b64": _PNG_B64}],
        )


async def test_critique_figure_persists_and_caches(
    session_factory, user_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.agent.llm_judge as judge
    calls = {"n": 0}

    async def fake_judge(prompt, *, provider, model, images=None, max_tokens=800):
        calls["n"] += 1
        assert images == [_PNG_B64]
        return {"ok": True, "severity": "minor",
                "issues": [{"kind": "missing_units", "detail": "y axis has no units"}]}, None

    monkeypatch.setattr(judge, "judge_json", fake_judge)
    eid = await _exec_with_figure(session_factory, user_id)

    from api.agent.tools_investigation import build_investigation_tools
    tools = {t.name: t for t in build_investigation_tools(user_id, "s", session_factory)}
    r1 = _decode(await tools["critique_figure"].handler(
        {"execution_id": eid, "filename": "f.png"}))
    assert r1["severity"] == "minor"
    assert r1["cached"] is False
    assert r1["issues"][0]["kind"] == "missing_units"

    # Second call hits the cached critique (same byte hash) — no new LLM call.
    r2 = _decode(await tools["critique_figure"].handler(
        {"execution_id": eid, "filename": "f.png"}))
    assert r2["cached"] is True
    assert r2["severity"] == "minor"
    assert calls["n"] == 1


async def test_critique_figure_fails_open(
    session_factory, user_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.agent.llm_judge as judge

    async def fake_judge(prompt, *, provider, model, images=None, max_tokens=800):
        return None, "LLM call failed"

    monkeypatch.setattr(judge, "judge_json", fake_judge)
    eid = await _exec_with_figure(session_factory, user_id)
    from api.agent.tools_investigation import build_investigation_tools
    tools = {t.name: t for t in build_investigation_tools(user_id, "s", session_factory)}
    r = _decode(await tools["critique_figure"].handler(
        {"execution_id": eid, "filename": "f.png"}))
    assert r["ok"] is True
    assert r["severity"] == "unknown"
    assert r["critique_error"] == "LLM call failed"


async def test_critique_figure_unknown_artifact(
    session_factory, user_id: str,
) -> None:
    eid = await _exec_with_figure(session_factory, user_id)
    from api.agent.tools_investigation import build_investigation_tools
    tools = {t.name: t for t in build_investigation_tools(user_id, "s", session_factory)}
    r = _decode(await tools["critique_figure"].handler(
        {"execution_id": eid, "filename": "missing.png"}))
    assert "error" in r


async def test_attach_artifact_critique_owner_scoped(
    session_factory, user_id: str,
) -> None:
    from api.db.queries.code_executions import attach_artifact_critique, get_execution
    eid = await _exec_with_figure(session_factory, user_id)
    async with session_factory() as db:
        ok = await attach_artifact_critique(
            db, eid, "someone-else", filename="f.png", art_hash="h",
            critique={"ok": True, "severity": "none", "issues": []},
        )
    assert ok is False  # not owned → no write
    async with session_factory() as db:
        ok = await attach_artifact_critique(
            db, eid, user_id, filename="f.png", art_hash="h",
            critique={"ok": True, "severity": "none", "issues": []},
        )
    assert ok is True
    async with session_factory() as db:
        row = await get_execution(db, eid, user_id)
    assert row is not None
    assert row["artifacts"][0]["critique"]["hash"] == "h"


# ── Feature 4: hypothesis novelty ────────────────────────────────────────────

async def test_hypothesis_novelty_roundtrips(session_factory, user_id: str) -> None:
    from api.db.queries.hypotheses import create_hypothesis, get_hypothesis, list_hypotheses
    iid = await _new_investigation(session_factory, user_id)
    novelty = {"label": "known", "closest_prior": "Smith 2020", "rationale": "restates"}
    async with session_factory() as db:
        hid = await create_hypothesis(
            db, iid, user_id, "X improves Y", novelty=novelty,
        )
    async with session_factory() as db:
        got = await get_hypothesis(db, hid, user_id)
        listed = await list_hypotheses(db, iid, user_id)
    assert got is not None
    assert got["novelty"]["label"] == "known"
    assert listed[0]["novelty"]["closest_prior"] == "Smith 2020"


async def test_propose_hypothesis_rejects_bad_novelty_label(
    session_factory, user_id: str,
) -> None:
    iid = await _new_investigation(session_factory, user_id)
    from api.agent.tools_investigation import build_investigation_tools
    tools = {t.name: t for t in build_investigation_tools(user_id, "s", session_factory)}
    # A novelty blob with a nonsense label is rejected before persistence.
    r = _decode(await tools["propose_hypothesis"].handler({
        "investigation_id": iid, "statement": "X improves Y",
        "novelty": {"label": "totally-made-up"},
    }))
    assert "error" in r
    assert "label" in r["error"]
    # A valid label is accepted and the hypothesis is created.
    ok = _decode(await tools["propose_hypothesis"].handler({
        "investigation_id": iid, "statement": "X improves Y",
        "novelty": {"label": "known", "closest_prior": "Smith", "rationale": "r"},
    }))
    assert "id" in ok


async def test_check_hypothesis_novelty_no_prior(
    session_factory, user_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No related prior work → 'novel' without calling the judge."""
    import api.db.queries.paper_chunks as pc
    import api.db.queries.wiki_read as wr
    import api.embeddings as emb

    async def fake_embed(texts):
        return [[0.1, 0.2, 0.3]]

    async def empty_chunks(db, q, embedding, limit=10, paper_id=None):
        return []

    async def empty_wiki(db, q, limit=20, include_archived=False):
        return []

    monkeypatch.setattr(emb, "embed_texts", fake_embed)
    monkeypatch.setattr(pc, "hybrid_search_paper_chunks", empty_chunks)
    monkeypatch.setattr(wr, "search_wiki_by_fts", empty_wiki)

    from api.agent.tools_investigation import build_investigation_tools
    tools = {t.name: t for t in build_investigation_tools(user_id, "s", session_factory)}
    r = _decode(await tools["check_hypothesis_novelty"].handler(
        {"statement": "A wholly new idea"}))
    assert r["label"] == "novel"
    assert r["related"] == []


async def test_check_hypothesis_novelty_judges_against_prior(
    session_factory, user_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.agent.llm_judge as judge
    import api.db.queries.paper_chunks as pc
    import api.db.queries.wiki_read as wr
    import api.embeddings as emb

    async def fake_embed(texts):
        return [[0.1, 0.2, 0.3]]

    async def empty_chunks(db, q, embedding, limit=10, paper_id=None):
        return []

    async def one_wiki(db, q, limit=20, include_archived=False):
        return [{"title": "Prior Work X", "content_text": "X improves Y by 10%"}]

    async def fake_judge(prompt, *, provider, model, images=None, max_tokens=800):
        return {"label": "known", "closest_prior": "Prior Work X",
                "rationale": "restates the same finding"}, None

    monkeypatch.setattr(emb, "embed_texts", fake_embed)
    monkeypatch.setattr(pc, "hybrid_search_paper_chunks", empty_chunks)
    monkeypatch.setattr(wr, "search_wiki_by_fts", one_wiki)
    monkeypatch.setattr(judge, "judge_json", fake_judge)

    from api.agent.tools_investigation import build_investigation_tools
    tools = {t.name: t for t in build_investigation_tools(user_id, "s", session_factory)}
    r = _decode(await tools["check_hypothesis_novelty"].handler(
        {"statement": "X improves Y"}))
    assert r["label"] == "known"
    assert "Prior Work X" in r["related"]


# ── Feature 3: citation guard ────────────────────────────────────────────────

async def test_check_citations_unresolved_source(
    session_factory, user_id: str,
) -> None:
    # A citation_id that isn't in external_facts → batched fact lookup
    # returns it absent → unresolved (no embedding/judge needed).
    from api.agent.tools_knowledge import build_knowledge_tools
    tools = {t.name: t for t in build_knowledge_tools(user_id, session_factory)}
    r = _decode(await tools["check_citations"].handler(
        {"claims": [{"claim": "Y is true", "citation_id": f"ext:gone-{uuid.uuid4().hex}"}]}))
    res = r["results"][0]
    assert res["source_status"] == "unresolved"
    assert res["verdict"] == "unresolved"


async def test_check_citations_batches_embedding(
    session_factory, user_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple claims → exactly one embed_texts call, results stay aligned
    with input order (and invalid entries are interleaved correctly)."""
    import api.agent.llm_judge as judge
    import api.db.queries.paper_chunks as pc
    import api.embeddings as emb

    embed_calls = {"n": 0}

    async def fake_embed(texts):
        embed_calls["n"] += 1
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def chunks(db, q, embedding, limit=10, paper_id=None):
        return [{"text": "supporting excerpt"}]

    async def fake_judge(prompt, *, provider, model, images=None, max_tokens=800):
        return {"supports": "yes", "confidence": 9, "rationale": "supports"}, None

    monkeypatch.setattr(emb, "embed_texts", fake_embed)
    monkeypatch.setattr(pc, "hybrid_search_paper_chunks", chunks)
    monkeypatch.setattr(judge, "judge_json", fake_judge)

    from api.agent.tools_knowledge import build_knowledge_tools
    tools = {t.name: t for t in build_knowledge_tools(user_id, session_factory)}
    r = _decode(await tools["check_citations"].handler({"claims": [
        {"claim": "first claim", "paper_id": str(uuid.uuid4())},
        "not-an-object",  # invalid; must keep its slot
        {"claim": "third claim", "paper_id": str(uuid.uuid4())},
    ]}))
    assert embed_calls["n"] == 1  # one batched call, not one per claim
    results = r["results"]
    assert len(results) == 3
    assert results[0]["verdict"] == "supported"
    assert results[1]["verdict"] == "invalid"
    assert results[2]["verdict"] == "supported"


async def test_get_external_facts_by_ids_roundtrip(session_factory, user_id: str) -> None:
    from api.db.queries.knowledge import get_external_facts_by_ids, upsert_external_fact
    sid = f"ext:fact-{uuid.uuid4().hex}"
    async with session_factory() as db:
        await upsert_external_fact(db, "doc", sid, {"k": "v"}, "body", fetched_by=user_id)
    async with session_factory() as db:
        facts = await get_external_facts_by_ids(db, [sid, "ext:missing"])
    assert sid in facts
    assert "ext:missing" not in facts
    # Empty input short-circuits without a query.
    async with session_factory() as db:
        assert await get_external_facts_by_ids(db, []) == {}


async def test_check_citations_unsupported_suggests_contradiction(
    session_factory, user_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.agent.llm_judge as judge
    import api.db.queries.paper_chunks as pc
    import api.embeddings as emb

    async def fake_embed(texts):
        return [[0.1, 0.2, 0.3]]

    async def chunks(db, q, embedding, limit=10, paper_id=None):
        return [{"text": "This paper is about an unrelated topic."}]

    async def fake_judge(prompt, *, provider, model, images=None, max_tokens=800):
        return {"supports": "no", "confidence": 8, "rationale": "off-topic"}, None

    monkeypatch.setattr(emb, "embed_texts", fake_embed)
    monkeypatch.setattr(pc, "hybrid_search_paper_chunks", chunks)
    monkeypatch.setattr(judge, "judge_json", fake_judge)

    from api.agent.tools_knowledge import build_knowledge_tools
    tools = {t.name: t for t in build_knowledge_tools(user_id, session_factory)}
    r = _decode(await tools["check_citations"].handler(
        {"claims": [{"claim": "Z cures cancer", "paper_id": str(uuid.uuid4())}]}))
    res = r["results"][0]
    assert res["supports"] == "no"
    assert res["verdict"] == "unsupported"
    assert res["suggest_record_contradiction"] is True


async def test_check_citations_validates_input(session_factory, user_id: str) -> None:
    from api.agent.tools_knowledge import build_knowledge_tools
    tools = {t.name: t for t in build_knowledge_tools(user_id, session_factory)}
    r = _decode(await tools["check_citations"].handler({"claims": []}))
    assert "error" in r


# ── Feature 2: automated reviewer ────────────────────────────────────────────

async def test_review_draft_persists_and_surfaces_in_inbox(
    session_factory, user_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.agent.reviewer as reviewer
    from api.agent.reviewer import MetaReview, ReviewerScore

    async def fake_ensemble(draft_text, *, kind, max_concurrency=5):
        meta = MetaReview(
            overall=4, decision="revise", summary="needs more citations",
            top_issues=["thin evidence"],
        )
        scores = [ReviewerScore(soundness=4, evidence_grounding=3, clarity=6, value=5)]
        return meta, scores

    monkeypatch.setattr(reviewer, "run_ensemble_review", fake_ensemble)

    from api.agent.tools_knowledge import build_knowledge_tools
    tools = {t.name: t for t in build_knowledge_tools(user_id, session_factory)}
    r = _decode(await tools["review_draft"].handler(
        {"draft_text": "Some report body.", "kind": "report"}))
    assert r["decision"] == "revise"
    assert r["overall"] == 4
    assert r["review_id"] is not None

    # The non-accept review surfaces in the caller's curator inbox.
    from api.db.queries.draft_reviews import list_draft_reviews_needing_attention
    async with session_factory() as db:
        pending = await list_draft_reviews_needing_attention(db, user_id, limit=50)
    assert any(p["id"] == r["review_id"] for p in pending)


# ── Regression: read-then-begin transaction collision ───────────────────────
# These tool happy-paths previously raised "A transaction is already begun on
# this Session" because an ownership SELECT auto-begins a tx that the
# subsequent write's `async with db.begin()` collided with. They were never
# caught because existing coverage only hit the early-return (not-found) path.

async def test_propose_hypothesis_happy_path(session_factory, user_id: str) -> None:
    iid = await _new_investigation(session_factory, user_id)
    from api.agent.tools_investigation import build_investigation_tools
    tools = {t.name: t for t in build_investigation_tools(user_id, "s", session_factory)}
    r = _decode(await tools["propose_hypothesis"].handler(
        {"investigation_id": iid, "statement": "X improves Y"}))
    assert "error" not in r
    assert r["id"]
    assert r["elo_rating"] == 1000.0


async def test_world_model_add_happy_path(session_factory, user_id: str) -> None:
    iid = await _new_investigation(session_factory, user_id)
    from api.agent.tools_investigation import build_investigation_tools
    tools = {t.name: t for t in build_investigation_tools(user_id, "s", session_factory)}
    r = _decode(await tools["world_model_add"].handler(
        {"investigation_id": iid, "kind": "fact", "content": "Pd catalysis works here"}))
    assert "error" not in r
    assert r["id"]
    assert r["kind"] == "fact"


async def test_record_contradiction_happy_path(session_factory, user_id: str) -> None:
    from api.db.queries.wiki_write import upsert_wiki_page
    from api.embeddings import EMBED_DIM

    async def _noop_embed(texts: list[str]) -> list[list[float]]:
        return [[0.0] * EMBED_DIM for _ in texts]

    slug = f"page-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        await upsert_wiki_page(
            db, slug=slug, title="P", content={"type": "doc", "content": []},
            content_text="Long enough body to chunk past fifty characters easily here.",
            created_by=user_id, citations=[], embed_fn=_noop_embed,
        )
    from api.agent.tools_knowledge import build_knowledge_tools
    tools = {t.name: t for t in build_knowledge_tools(user_id, session_factory)}
    r = _decode(await tools["record_contradiction"].handler({
        "page_slug": slug, "citation_a": "ext:a", "citation_b": "ext:b",
        "proposed_winner": "a", "reason": "a is fresher",
    }))
    assert "error" not in r
    assert r["id"]


async def test_review_draft_accept_not_in_inbox(
    session_factory, user_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.agent.reviewer as reviewer
    from api.agent.reviewer import MetaReview

    async def fake_ensemble(draft_text, *, kind, max_concurrency=5):
        return MetaReview(overall=8, decision="accept", summary="good", top_issues=[]), []

    monkeypatch.setattr(reviewer, "run_ensemble_review", fake_ensemble)
    from api.agent.tools_knowledge import build_knowledge_tools
    tools = {t.name: t for t in build_knowledge_tools(user_id, session_factory)}
    r = _decode(await tools["review_draft"].handler(
        {"draft_text": "Body", "kind": "wiki"}))
    assert r["decision"] == "accept"
    from api.db.queries.draft_reviews import list_draft_reviews_needing_attention
    async with session_factory() as db:
        pending = await list_draft_reviews_needing_attention(db, user_id, limit=50)
    assert all(p["id"] != r["review_id"] for p in pending)
