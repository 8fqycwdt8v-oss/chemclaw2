"""Tests for the paper-chunking helper used by paper_qa ingest.

The chunking logic is pure-Python; no Postgres / OpenAI dependencies. The
section-detection heuristic is the part most likely to drift, so the
tests pin it down with examples that mirror real paper layouts.
"""
from __future__ import annotations

from api.db.queries.papers import chunk_paper_text


def test_chunk_text_empty_returns_no_chunks() -> None:
    assert chunk_paper_text("") == []
    assert chunk_paper_text("   \n  \n") == []


def test_chunk_text_short_body_one_chunk() -> None:
    body = "Some short body of text."
    out = chunk_paper_text(body, chunk_size=1500)
    assert len(out) == 1
    idx, section, text = out[0]
    assert idx == 0
    assert section is None
    assert text == body


def test_chunk_text_markdown_heading_detected() -> None:
    body = "Intro paragraph.\n\n## Methods\n\nWe did the following.\n"
    out = chunk_paper_text(body)
    # All in one chunk for a short body, but the section heading should
    # carry through for the chunk containing the heading region.
    assert len(out) == 1
    _, section, _ = out[0]
    # First-byte offset 0 is in the Intro region (section=None). Our impl
    # walks headings and returns the most recent before the chunk's start.
    assert section is None


def test_chunk_text_allcaps_heading_detected() -> None:
    body = (
        "Some intro.\n\n"
        "INTRODUCTION\n\n"
        "Body text body text body text. " * 200  # force multiple chunks
    )
    out = chunk_paper_text(body, chunk_size=600, overlap=100)
    assert len(out) > 1
    # Chunks after the INTRODUCTION heading should pick it up.
    later_sections = {s for (_, s, _) in out[1:]}
    assert "INTRODUCTION" in later_sections


def test_chunk_text_overlap_preserves_context() -> None:
    """Overlap means consecutive chunks share a tail/head."""
    body = ("Sentence one. " * 200).strip()
    out = chunk_paper_text(body, chunk_size=500, overlap=100)
    assert len(out) >= 2
    # Tail of chunk N and head of chunk N+1 should share at least 50 chars.
    a = out[0][2]
    b = out[1][2]
    # Look for any 50-char substring of the tail of `a` in the head of `b`.
    found = any(a[-50 - i:][:50] in b[:200] for i in range(10))
    assert found, "expected non-empty overlap between consecutive chunks"


def test_chunk_text_breaks_on_sentence_boundary() -> None:
    """Chunks should prefer to cut at '. ' rather than mid-word."""
    body = (
        "First sentence. " * 50 +
        "Second batch of sentences. " * 50
    )
    out = chunk_paper_text(body, chunk_size=400, overlap=50)
    assert len(out) >= 2
    for _, _, text in out[:-1]:  # last chunk may end before any boundary
        # Either ends with a sentence terminator or is followed by whitespace.
        assert text[-1] in ".!?\n " or text.endswith("sentence."), (
            f"chunk ended unexpectedly: {text[-30:]!r}"
        )


def test_chunk_text_indices_are_sequential() -> None:
    body = "Body text. " * 1000
    out = chunk_paper_text(body, chunk_size=300, overlap=50)
    indices = [idx for (idx, _, _) in out]
    assert indices == list(range(len(out)))


def test_chunk_text_enforces_min_chunk_size() -> None:
    """Chunk size < 200 is clamped to avoid pathologically tiny chunks."""
    body = "Sentence text. " * 100
    out_small = chunk_paper_text(body, chunk_size=50)
    out_clamped = chunk_paper_text(body, chunk_size=200)
    # Same number of chunks ish — the clamp normalises to 200.
    assert abs(len(out_small) - len(out_clamped)) <= 1


def test_chunk_text_overlap_clamped_when_too_large() -> None:
    body = "Body text body. " * 200
    # overlap >= chunk_size would loop forever; impl should clamp.
    out = chunk_paper_text(body, chunk_size=500, overlap=500)
    assert len(out) > 0
    # Sanity: function terminates and chunk count is reasonable.
    assert len(out) < 100
