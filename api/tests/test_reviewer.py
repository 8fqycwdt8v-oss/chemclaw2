"""Unit tests for the ensemble reviewer (no DB, no real LLM calls).

`judge_json` is monkeypatched to return canned reviewer/meta payloads so
we exercise the fan-out + meta aggregation + fail-open branches.
"""
from __future__ import annotations

import pytest

_REVIEW = {
    "soundness": 7, "evidence_grounding": 6, "clarity": 8, "value": 7,
    "weaknesses": ["thin on prior art"], "questions": ["scale?"],
}
_META = {
    "overall": 7, "decision": "accept", "summary": "Solid.",
    "top_issues": [],
}


async def test_ensemble_returns_meta_and_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.agent.reviewer as mod

    calls = {"n": 0}

    async def fake_judge(prompt, *, provider, model, images=None, max_tokens=800):
        calls["n"] += 1
        # The meta prompt names the area-chair role; reviewers get the
        # per-axis rubric. Distinguish by a token in the prompt.
        if "area chair" in prompt:
            return dict(_META), None
        return dict(_REVIEW), None

    monkeypatch.setenv("REVIEWER_ENSEMBLE_SIZE", "5")
    monkeypatch.setattr(mod, "judge_json", fake_judge)
    meta, scores = await mod.run_ensemble_review("draft body", kind="report")
    assert meta.decision == "accept"
    assert meta.overall == 7
    assert len(scores) == 5
    assert calls["n"] == 6  # 5 reviews + 1 meta


async def test_ensemble_all_reviews_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.agent.reviewer as mod

    async def fake_judge(prompt, *, provider, model, images=None, max_tokens=800):
        return None, "LLM call failed"

    monkeypatch.setattr(mod, "judge_json", fake_judge)
    meta, scores = await mod.run_ensemble_review("draft", kind="wiki")
    assert meta.decision == "revise"
    assert scores == []
    assert "unavailable" in meta.summary.lower()


async def test_meta_failure_synthesizes_consensus(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.agent.reviewer as mod

    async def fake_judge(prompt, *, provider, model, images=None, max_tokens=800):
        if "area chair" in prompt:
            return None, "meta failed"
        return dict(_REVIEW), None

    monkeypatch.setenv("REVIEWER_ENSEMBLE_SIZE", "3")
    monkeypatch.setattr(mod, "judge_json", fake_judge)
    meta, scores = await mod.run_ensemble_review("draft", kind="report")
    # mean of (7+6+8+7)/4 = 7.0 → accept, synthesized from the 3 reviews.
    assert len(scores) == 3
    assert meta.decision == "accept"
    assert "synthesized" in meta.summary.lower()
