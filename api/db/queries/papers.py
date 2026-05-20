"""Paper RAG queries — chunking, retrieval, and PaperQA2-style RCS reranking.

Mirrors the wiki_chunks pattern in api/db/queries/wiki_read.py:
- search_paper_chunks_fts: lexical retrieval
- semantic_search_paper_chunks: pgvector cosine
- hybrid_search_paper_chunks: Reciprocal Rank Fusion over both

The Re-ranking + Contextual Summarization (RCS) step is in
`score_chunks_with_llm`, which asks the model to rate each chunk's
relevance 1-10 and produce a ≤300-word query-conditioned summary.
That mirrors the PaperQA2 architecture (arXiv:2409.13740) — see the
opportunity-map analysis in /root/.claude/plans/.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.embeddings import EMBED_DIM

logger = logging.getLogger(__name__)


# ── chunking ──────────────────────────────────────────────────────────────────

def chunk_paper_text(
    body: str,
    chunk_size: int = 1500,
    overlap: int = 200,
) -> list[tuple[int, str | None, str]]:
    """Split a plain-text paper body into (chunk_idx, section, text) tuples.

    Section is the nearest heading detected via a simple markdown-/all-caps
    heuristic. Chunk size is in characters; overlap preserves context across
    chunk boundaries (the standard sliding-window approach used by
    LangChain / LlamaIndex / PaperQA2).

    Returns an empty list if the body is empty after stripping.
    """
    body = (body or "").strip()
    if not body:
        return []

    # Identify heading positions. Two heuristics:
    #   - markdown-style: lines starting with #, ##, ### at col 0
    #   - all-caps short lines (≤60 chars), letters + spaces only — common
    #     in journal-article plain-text dumps where headings get capitalised
    headings: list[tuple[int, str]] = []  # (char_offset, heading_text)
    cursor = 0
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            is_md = bool(re.match(r"^#{1,6}\s+\S", line))
            is_allcaps = (
                len(stripped) <= 60
                and re.fullmatch(r"[A-Z0-9][A-Z0-9 \-:]*", stripped) is not None
                and any(c.isalpha() for c in stripped)
            )
            if is_md or is_allcaps:
                headings.append((cursor, stripped.lstrip("# ").strip()))
        cursor += len(line) + 1  # +1 for the newline

    def section_for(offset: int) -> str | None:
        # Binary search would be faster but the heading list is tiny in practice.
        current: str | None = None
        for h_off, h_text in headings:
            if h_off <= offset:
                current = h_text
            else:
                break
        return current

    if chunk_size < 200:
        chunk_size = 200
    if overlap < 0:
        overlap = 0
    if overlap >= chunk_size:
        overlap = chunk_size // 4

    chunks: list[tuple[int, str | None, str]] = []
    pos = 0
    idx = 0
    while pos < len(body):
        end = min(pos + chunk_size, len(body))
        # Try to break on a sentence boundary near `end` to keep chunks
        # semantically coherent. Look back up to 200 chars for ". " or "\n".
        if end < len(body):
            window = body[max(pos + chunk_size - 200, pos): end]
            match = list(re.finditer(r"(?:[.!?]\s)|\n\n", window))
            if match:
                cut = match[-1].end()
                end = max(pos + chunk_size - 200, pos) + cut
        chunk_text = body[pos:end].strip()
        if chunk_text:
            chunks.append((idx, section_for(pos), chunk_text))
            idx += 1
        if end >= len(body):
            break
        pos = max(end - overlap, pos + 1)
    return chunks


# ── insert ────────────────────────────────────────────────────────────────────

async def insert_paper_chunks(
    db: AsyncSession,
    paper_id: str,
    chunks: list[dict[str, Any]],
) -> int:
    """Bulk-insert chunks for a paper. Caller manages the transaction.

    Each chunk dict must have keys: chunk_idx (int), text (str),
    embedding (list[float] of length EMBED_DIM | None), section (str | None),
    page (int | None). Existing chunks for (paper_id, chunk_idx) are
    overwritten so re-ingest is idempotent.

    Returns the number of chunks written.
    """
    if not chunks:
        return 0
    n = 0
    for c in chunks:
        emb = c.get("embedding")
        if emb is not None and len(emb) != EMBED_DIM:
            raise ValueError(f"embedding for chunk {c.get('chunk_idx')} has wrong dim: {len(emb)}")
        vec_str = ("[" + ",".join(map(str, emb)) + "]") if emb is not None else None
        await db.execute(
            text(f"""
                INSERT INTO paper_chunks (paper_id, chunk_idx, section, page, text, embedding)
                VALUES (CAST(:pid AS uuid), :idx, :sec, :pg, :txt,
                        {'CAST(:vec AS vector(' + str(EMBED_DIM) + '))' if vec_str else 'NULL'})
                ON CONFLICT (paper_id, chunk_idx) DO UPDATE
                    SET section   = EXCLUDED.section,
                        page      = EXCLUDED.page,
                        text      = EXCLUDED.text,
                        embedding = EXCLUDED.embedding
            """),
            {
                "pid": paper_id,
                "idx": int(c["chunk_idx"]),
                "sec": c.get("section"),
                "pg": c.get("page"),
                "txt": c["text"],
                **({"vec": vec_str} if vec_str else {}),
            },
        )
        n += 1
    return n


# ── retrieve ──────────────────────────────────────────────────────────────────

async def search_paper_chunks_fts(
    db: AsyncSession,
    query: str,
    limit: int = 20,
    paper_id: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": query, "lim": min(limit, 200)}
    paper_clause = ""
    if paper_id is not None:
        paper_clause = "AND c.paper_id = CAST(:pid AS uuid)"
        params["pid"] = paper_id
    result = await db.execute(
        text(f"""
            SELECT c.id::text, c.paper_id::text, c.chunk_idx, c.section, c.page,
                   c.text, p.title, p.doi, p.url,
                   ts_rank(to_tsvector('english', c.text),
                           plainto_tsquery('english', :q)) AS rank
            FROM paper_chunks c
            JOIN papers p ON p.id = c.paper_id
            WHERE to_tsvector('english', c.text)
                  @@ plainto_tsquery('english', :q)
                  {paper_clause}
            ORDER BY rank DESC
            LIMIT :lim
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


async def semantic_search_paper_chunks(
    db: AsyncSession,
    embedding: list[float],
    limit: int = 20,
    max_distance: float = 0.6,
    paper_id: str | None = None,
) -> list[dict[str, Any]]:
    if len(embedding) != EMBED_DIM:
        raise ValueError(f"embedding must have {EMBED_DIM} dimensions, got {len(embedding)}")
    safe_limit = min(max(1, limit), 100)
    vec_str = "[" + ",".join(map(str, embedding)) + "]"
    params: dict[str, Any] = {
        "vec": vec_str,
        "max_dist": max_distance,
        "lim": safe_limit,
    }
    paper_clause = ""
    if paper_id is not None:
        paper_clause = "AND c.paper_id = CAST(:pid AS uuid)"
        params["pid"] = paper_id
    result = await db.execute(
        text(f"""
            SELECT c.id::text, c.paper_id::text, c.chunk_idx, c.section, c.page,
                   c.text, p.title, p.doi, p.url,
                   (c.embedding <=> CAST(:vec AS vector({EMBED_DIM}))) AS distance
            FROM paper_chunks c
            JOIN papers p ON p.id = c.paper_id
            WHERE c.embedding IS NOT NULL
              AND (c.embedding <=> CAST(:vec AS vector({EMBED_DIM}))) < :max_dist
              {paper_clause}
            ORDER BY distance
            LIMIT :lim
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


async def hybrid_search_paper_chunks(
    db: AsyncSession,
    query: str,
    embedding: list[float],
    limit: int = 10,
    paper_id: str | None = None,
) -> list[dict[str, Any]]:
    """RRF over FTS + semantic. Same recipe as hybrid_search_wiki.

    Returns rows keyed by chunk id with a `score` field used downstream by
    the RCS reranker.
    """
    safe_limit = min(max(1, limit), 50)
    leg_limit = safe_limit * 3
    fts_rows, sem_rows = await asyncio.gather(
        search_paper_chunks_fts(db, query, limit=leg_limit, paper_id=paper_id),
        semantic_search_paper_chunks(db, embedding, limit=leg_limit, paper_id=paper_id),
    )

    K = 60  # RRF damping constant — matches wiki hybrid search.
    merged: dict[str, dict[str, Any]] = {}
    for rank, row in enumerate(fts_rows, start=1):
        cid = row["id"]
        entry = merged.setdefault(cid, {"row": row, "fts_rank": None, "sem_rank": None})
        if entry["fts_rank"] is None:
            entry["fts_rank"] = rank
    for rank, row in enumerate(sem_rows, start=1):
        cid = row["id"]
        entry = merged.setdefault(cid, {"row": row, "fts_rank": None, "sem_rank": None})
        if entry["sem_rank"] is None:
            entry["sem_rank"] = rank

    scored: list[dict[str, Any]] = []
    for cid, entry in merged.items():
        s = 0.0
        if entry["fts_rank"] is not None:
            s += 1.0 / (K + entry["fts_rank"])
        if entry["sem_rank"] is not None:
            s += 1.0 / (K + entry["sem_rank"])
        scored.append({
            **entry["row"],
            "score": s,
            "fts_rank": entry["fts_rank"],
            "sem_rank": entry["sem_rank"],
        })
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:safe_limit]


async def get_chunk_count(db: AsyncSession, paper_id: str) -> int:
    result = await db.execute(
        text("SELECT count(*) FROM paper_chunks WHERE paper_id = CAST(:pid AS uuid)"),
        {"pid": paper_id},
    )
    return int(result.scalar_one())


# ── RCS reranking ─────────────────────────────────────────────────────────────

RCS_PROMPT = """You are scoring the relevance of a paper excerpt to a research query.

Query: {query}

Paper: {title}
DOI: {doi}
Section: {section}
Excerpt:
\"\"\"
{excerpt}
\"\"\"

Reply with a single JSON object on one line:
{{"score": <integer 1-10>, "summary": "<≤300 word summary of what the excerpt says about the query>"}}

Score guide:
- 1-3: off-topic or contradicts the query
- 4-6: tangentially related; provides context but no direct answer
- 7-8: addresses the query partially or with caveats
- 9-10: directly and substantively answers the query"""


async def score_chunks_with_llm(
    chunks: list[dict[str, Any]],
    query: str,
    max_concurrency: int = 4,
) -> list[dict[str, Any]]:
    """Apply PaperQA2-style RCS to each chunk.

    For each chunk, prompt the LLM with (chunk, query) to produce:
      - relevance_score: integer 1-10
      - summary: ≤300 word query-conditioned synopsis

    Failures (LLM error, malformed JSON) attach `rcs_error` and leave the
    chunk's other fields intact. Caller can filter on `relevance_score`
    presence to drop those.
    """
    if not chunks:
        return []
    # Anthropic client is the easiest path — keys already provisioned. Falls
    # back gracefully when the lib isn't installed (returns chunks unscored).
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        logger.warning("anthropic_unavailable for RCS — returning chunks unscored")
        return [{**c, "rcs_error": "anthropic SDK not installed"} for c in chunks]
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY missing — returning chunks unscored")
        return [{**c, "rcs_error": "ANTHROPIC_API_KEY not configured"} for c in chunks]
    model = os.environ.get("ANTHROPIC_RCS_MODEL", "claude-haiku-4-5-20251001")
    client = AsyncAnthropic(api_key=api_key)
    sem = asyncio.Semaphore(max_concurrency)

    async def score_one(c: dict[str, Any]) -> dict[str, Any]:
        prompt = RCS_PROMPT.format(
            query=query,
            title=c.get("title") or "(unknown)",
            doi=c.get("doi") or "(no DOI)",
            section=c.get("section") or "(no section)",
            excerpt=c["text"][:4000],
        )
        async with sem:
            try:
                resp = await client.messages.create(
                    model=model,
                    max_tokens=600,
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as e:  # network, auth, rate-limit
                logger.warning("rcs_llm_call_failed chunk=%s err=%s", c.get("id"), e)
                return {**c, "rcs_error": "LLM call failed"}
        text_body = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_body += getattr(block, "text", "")
        # Extract the first {...} JSON object in the response, tolerant of
        # surrounding prose / fenced code blocks.
        match = re.search(r"\{[\s\S]*?\}", text_body)
        if not match:
            return {**c, "rcs_error": "no JSON in LLM response"}
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return {**c, "rcs_error": "JSON parse failed"}
        score_val = parsed.get("score")
        summary = parsed.get("summary")
        if not isinstance(score_val, (int, float)):
            return {**c, "rcs_error": "score missing or non-numeric"}
        score_int = max(1, min(10, int(round(score_val))))
        return {
            **c,
            "relevance_score": score_int,
            "summary": str(summary or "")[:1500],
        }

    return await asyncio.gather(*(score_one(c) for c in chunks))
