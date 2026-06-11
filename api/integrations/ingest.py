"""Shared document-ingestion pipeline.

`ingest_document` is the single path that turns raw document bytes into the
knowledge surfaces: an `external_facts` cache row, a `papers` row, and a
`needs_review` wiki draft (plus, in `extract='full'` mode, LLM-extracted
compounds + citations resolved through PubChem). Both the HTTP upload route
(`api/routes/integrations.py`) and the SharePoint sync worker (a later slice)
call it, so the extraction/persistence logic lives here once rather than being
duplicated per caller.

HTTP concerns — auth, rate limiting, streaming size enforcement, magic-byte
validation, and mapping errors to status codes — stay in the route. This
function takes already-read bytes and raises `UnsupportedContentType` /
`ExtractionError` (from `extractors`) on bad input; callers translate.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from api.db.queries.hypotheses import create_hypothesis
from api.db.queries.knowledge import upsert_external_fact, upsert_paper
from api.db.queries.wiki_write import upsert_wiki_page
from api.db.queries.world_model import add_world_model_entry
from api.embeddings import embed_texts
from api.integrations.document_enrichment import (
    extract_doi,
    extract_entities_from_text,
    fetch_crossref_metadata,
    first_nonempty_line,
    resolve_compound_name_to_smiles,
    slugify_doi,
)
from api.integrations.extractors import extract_text
from api.integrations.kg_extraction import extract_world_model

logger = logging.getLogger(__name__)

# Body text persisted / sent for enrichment is capped to keep rows and token
# budgets bounded. CONTENT_CAP bounds what we store; ENTITY_CAP bounds the
# entity-extraction LLM input. ENTITY_CAP is far above the previous inline
# 8 KB so long documents are actually analysed, but still tunable.
_CONTENT_CAP = 500_000
_ENTITY_CAP = int(os.environ.get("ENTITY_EXTRACTION_MAX_CHARS", "40000"))


async def ingest_document(
    db: AsyncSession,
    *,
    content: bytes,
    filename: str | None,
    content_type: str,
    user_id: str,
    extract: Literal["basic", "full"] = "basic",
    extract_kg: bool = False,
    investigation_id: str | None = None,
) -> dict[str, Any]:
    """Extract text from `content` and persist it across the knowledge surfaces.

    When `extract_kg` is set and an `investigation_id` is given, also run the
    LLM knowledge-graph pass and write the resulting facts/evidence (with
    confidence + source provenance) and hypotheses into that investigation —
    the "generate knowledge" step. Best-effort: a KG failure never fails the
    ingest.

    Returns a summary dict (fact id, title, doi, wiki slug, extracted entities,
    kg counts). Raises `UnsupportedContentType` / `ExtractionError` from the
    extractor if the bytes can't be parsed.
    """
    text = extract_text(content, content_type)

    source_id = hashlib.sha256(content).hexdigest()
    doi = extract_doi(text)

    # Enrich with CrossRef metadata when a DOI is present. Network call is
    # best-effort: a miss/timeout falls back to the first-non-empty-line
    # heuristic — ingestion still succeeds.
    metadata: dict[str, Any] = {}
    if doi:
        crossref = await fetch_crossref_metadata(doi)
        if crossref:
            metadata = crossref

    title = metadata.get("title") or first_nonempty_line(text)
    abstract = metadata.get("abstract")

    # Optional LLM entity extraction. Best-effort: any failure is logged inside
    # extract_entities_from_text and returned as an empty result.
    entities: dict[str, Any] = {"compounds": [], "citations": []}
    resolved_smiles: list[dict[str, Any]] = []
    if extract == "full":
        entities = await extract_entities_from_text(text, max_chars=_ENTITY_CAP)
        compound_names = [
            c.get("name", "").strip()
            for c in (entities.get("compounds") or [])
            if isinstance(c, dict) and c.get("name")
        ][:20]
        if compound_names:
            results = await asyncio.gather(
                *(resolve_compound_name_to_smiles(n) for n in compound_names),
                return_exceptions=True,
            )
            for name, result in zip(compound_names, results, strict=False):
                if isinstance(result, str):
                    resolved_smiles.append({"name": name, "smiles": result})
                # Failures (None/exception) are dropped — the name still
                # appears in entities.compounds for curator review.

    fact_id, _ = await upsert_external_fact(
        db,
        source_type="document",
        source_id=source_id,
        payload={
            "filename": filename,
            "content_type": content_type,
            "doi": doi,
            "title": title,
            "abstract": abstract,
            "authors": metadata.get("authors") or [],
            "container_title": metadata.get("container_title"),
            "published_year": metadata.get("published_year"),
            "extracted_compounds": entities.get("compounds") or [],
            "extracted_citations": entities.get("citations") or [],
            "resolved_smiles": resolved_smiles,
        },
        content_text=text[:_CONTENT_CAP],
        fetched_by=user_id,
    )

    if title:
        try:
            await upsert_paper(
                db,
                url=(f"https://doi.org/{doi}" if doi else f"upload:{source_id}"),
                title=title,
                doi=doi,
                abstract=abstract,
                content_text=text[:_CONTENT_CAP] if not abstract else None,
                created_by=user_id,
            )
        except Exception:
            logger.exception("ingest_upsert_paper_failed source_id=%s", source_id)

    wiki_slug = await _build_wiki_draft(
        db,
        source_id=source_id,
        filename=filename,
        title=title,
        doi=doi,
        abstract=abstract,
        text=text,
        metadata=metadata,
        entities=entities,
        resolved_smiles=resolved_smiles,
        user_id=user_id,
    )

    kg = {"facts": 0, "hypotheses": 0}
    if extract_kg and investigation_id:
        kg = await _populate_kg(
            db,
            investigation_id=investigation_id,
            user_id=user_id,
            text=text,
            source_id=source_id,
            wiki_slug=wiki_slug,
            title=title,
            doi=doi,
        )

    return {
        "fact_id": fact_id,
        "chars": len(text),
        "title": title,
        "doi": doi,
        "wiki_slug": wiki_slug,
        "abstract": abstract,
        "extracted_compounds": entities.get("compounds") or [],
        "extracted_citations": entities.get("citations") or [],
        "resolved_smiles": resolved_smiles,
        "kg": kg,
    }


async def _populate_kg(
    db: AsyncSession,
    *,
    investigation_id: str,
    user_id: str,
    text: str,
    source_id: str,
    wiki_slug: str | None,
    title: str | None,
    doi: str | None,
) -> dict[str, int]:
    """Run the KG extraction LLM pass and persist facts + hypotheses.

    Each world-model entry carries source provenance (the doc's wiki slug +
    content hash) in its payload so the knowledge is auditable back to the
    document. Every write is individually guarded — one malformed entry can't
    abort the rest — and the whole step is best-effort. Returns counts persisted.
    """
    try:
        extracted = await extract_world_model(text, max_chars=_ENTITY_CAP)
    except Exception:
        logger.exception("ingest_kg_extract_failed source_id=%s", source_id)
        return {"facts": 0, "hypotheses": 0}

    source = {
        "type": "document",
        "source_id": source_id,
        "wiki_slug": wiki_slug,
        "title": title,
        "doi": doi,
    }

    facts_added = 0
    for f in extracted.get("facts", []):
        try:
            await add_world_model_entry(
                db,
                investigation_id,
                user_id,
                kind=f["kind"],
                content=f["content"],
                payload={"source": source, "context": f.get("context", "")},
                confidence=f.get("confidence"),
            )
            facts_added += 1
        except Exception:
            logger.exception("ingest_kg_fact_failed source_id=%s", source_id)

    provenance = f"[source: {title or source_id}"
    provenance += f" ({wiki_slug})]" if wiki_slug else "]"
    hyps_added = 0
    for h in extracted.get("hypotheses", []):
        statement = (h.get("statement") or "").strip()
        if not statement:
            continue
        rationale = (h.get("rationale") or "").strip()
        rationale = f"{rationale}\n\n{provenance}" if rationale else provenance
        try:
            await create_hypothesis(
                db, investigation_id, user_id, statement=statement, rationale=rationale
            )
            hyps_added += 1
        except Exception:
            logger.exception("ingest_kg_hypothesis_failed source_id=%s", source_id)

    if facts_added or hyps_added:
        logger.info(
            "ingest_kg_populated source_id=%s facts=%d hypotheses=%d",
            source_id, facts_added, hyps_added,
        )
    return {"facts": facts_added, "hypotheses": hyps_added}


async def _build_wiki_draft(
    db: AsyncSession,
    *,
    source_id: str,
    filename: str | None,
    title: str | None,
    doi: str | None,
    abstract: str | None,
    text: str,
    metadata: dict[str, Any],
    entities: dict[str, Any],
    resolved_smiles: list[dict[str, Any]],
    user_id: str,
) -> str | None:
    """Upsert a needs_review wiki draft for an ingested document.

    Idempotent: a stable slug per (doi or content hash) means re-ingesting the
    same document updates rather than duplicates. Returns the slug, or None if
    there's no title to anchor a page or the upsert fails.
    """
    if not title:
        return None
    slug_base = slugify_doi(doi) if doi else f"doc-{source_id[:12]}"
    wiki_slug = slug_base[:80].rstrip("-") or f"doc-{source_id[:12]}"
    try:
        authors_str = ", ".join(metadata.get("authors") or [])
        container = metadata.get("container_title")
        year = metadata.get("published_year")
        body_lines = [
            f"# {title}",
            "",
            f"**Source:** {filename or 'uploaded document'}",
        ]
        if doi:
            body_lines.append(f"**DOI:** [{doi}](https://doi.org/{doi})")
        if authors_str:
            body_lines.append(f"**Authors:** {authors_str}")
        if container:
            body_lines.append(f"**Journal:** {container}")
        if year:
            body_lines.append(f"**Year:** {year}")
        if abstract:
            body_lines.extend(["", "## Abstract", "", abstract])
        if resolved_smiles:
            body_lines.extend(["", "## Compounds (auto-extracted)", ""])
            body_lines.extend(f"- **{cs['name']}** — `{cs['smiles']}`" for cs in resolved_smiles)
        unresolved = [
            c for c in (entities.get("compounds") or [])
            if isinstance(c, dict)
            and c.get("name")
            and not any(r["name"] == c["name"] for r in resolved_smiles)
        ]
        if unresolved:
            body_lines.extend(["", "## Compound mentions (unresolved)", ""])
            body_lines.extend(f"- {c['name']} — _{c.get('context', '')[:120]}_" for c in unresolved[:10])
        extracted_citations = entities.get("citations") or []
        if extracted_citations:
            body_lines.extend(["", "## Citations (auto-extracted)", ""])
            for cit in extracted_citations[:10]:
                if not isinstance(cit, dict):
                    continue
                ident = cit.get("identifier", "").strip()
                if not ident:
                    continue
                body_lines.append(f"- `{ident}` — _{cit.get('context', '')[:120]}_")
        body_lines.extend(["", "## Extracted text (excerpt)", "", text[:5000]])
        wiki_text = "\n".join(body_lines)
        await upsert_wiki_page(
            db,
            slug=wiki_slug,
            title=title,
            content={"type": "doc", "content": []},
            content_text=wiki_text,
            created_by=user_id,
            citations=[],
            embed_fn=embed_texts,
            project="papers",
            needs_review=True,
        )
        return wiki_slug
    except Exception:
        logger.exception(
            "ingest_upsert_wiki_failed source_id=%s slug=%s", source_id, wiki_slug
        )
        return None
