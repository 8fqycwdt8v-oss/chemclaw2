"""Document-upload enrichment: DOI extraction, CrossRef metadata, slug helpers.

Called from `api/routes/integrations.py` after the PDF has been parsed to
text. Each helper is independently testable — the route stitches them
together but the unit logic lives here.

CrossRef calls go through the existing SSRF-pinned `_fetch_validated`
helper (`crossref.org` is on `ALLOWED_DOMAINS`). No new outbound paths,
no new dependencies — `pypdf` is the only addition vs. before.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# DOI grammar per the official spec (https://www.doi.org/doi_handbook/2_Numbering.html):
# - Always starts with "10."
# - Followed by 4+ digits (the registrant code)
# - Then "/" and a "suffix" of any characters except whitespace.
# In practice the DOI suffix is restricted to printable ASCII and rarely
# contains spaces, so we stop at the first whitespace or angle-bracket /
# parenthesis (commonly used as field delimiters in PDF metadata).
_DOI_RE = re.compile(r'\b10\.\d{4,9}/[^\s<>()"\'\[\]]+', re.IGNORECASE)


def extract_doi(text: str) -> str | None:
    """Return the first DOI found in `text`, or None.

    Strips trailing punctuation that's often glued to a DOI in PDFs
    (e.g. "...10.1234/xyz.5678."). Common false-positive guard: a DOI
    must contain at least one alphanumeric in its suffix.
    """
    if not text:
        return None
    match = _DOI_RE.search(text)
    if not match:
        return None
    doi = match.group(0)
    # Strip up to 3 trailing punctuation chars that tend to glue to DOIs.
    doi = doi.rstrip(".,;:")
    if not re.search(r"[A-Za-z0-9]", doi.split("/", 1)[1] if "/" in doi else ""):
        return None
    return doi


def slugify_doi(doi: str) -> str:
    """Convert a DOI to a wiki-slug-safe form (lowercase, alnum + hyphens).

    DOIs contain `/` and `.` and case-mixed alpha that the wiki slug regex
    (`^[a-z0-9][a-z0-9-]*[a-z0-9]$`) doesn't allow. We map everything to
    lowercase, replace runs of non-alnum with a single hyphen, and trim
    leading/trailing hyphens.
    """
    s = doi.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def first_nonempty_line(text: str, max_chars: int = 200) -> str | None:
    """Take the first non-blank line of `text`, capped at max_chars."""
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:max_chars]
    return None


def _crossref_strip_jats(value: object) -> str | None:
    """CrossRef returns JATS-marked-up abstracts; strip the inline tags."""
    if not value:
        return None
    if isinstance(value, list):
        value = " ".join(str(x) for x in value)
    if not isinstance(value, str):
        return None
    # Remove <jats:*> tags but keep their inner text.
    clean = re.sub(r"<[^>]+>", "", value)
    return " ".join(clean.split()) or None


def normalize_crossref_response(body: dict[str, Any]) -> dict[str, Any]:
    """Pick the fields we care about out of a CrossRef /works/{doi} response.

    Returns a flat dict with: title, abstract, authors, container_title,
    published_year, doi. All optional — any of them may be absent.
    """
    msg = body.get("message") or {}
    title_list = msg.get("title") or []
    title = title_list[0] if title_list else None
    abstract = _crossref_strip_jats(msg.get("abstract"))
    authors_raw = msg.get("author") or []
    authors = [
        " ".join(filter(None, [a.get("given"), a.get("family")]))
        for a in authors_raw
        if isinstance(a, dict)
    ]
    container_list = msg.get("container-title") or []
    container = container_list[0] if container_list else None
    issued = msg.get("issued") or {}
    parts = issued.get("date-parts") or []
    year = parts[0][0] if parts and parts[0] else None
    return {
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "container_title": container,
        "published_year": year,
        "doi": msg.get("DOI"),
    }


async def resolve_compound_name_to_smiles(name: str) -> str | None:
    """Look up a compound name on PubChem and return its canonical SMILES.

    Uses the PubChem PUG REST API (already on `ALLOWED_DOMAINS`) via the
    SSRF-pinned `_fetch_validated` helper. Returns None on any failure —
    a name that PubChem can't resolve is common (typos, internal codenames,
    novel compounds) and shouldn't bubble up as an error.

    Names get URL-encoded; the caller is responsible for not passing
    empty strings.
    """
    from urllib.parse import quote

    from api.agent.tool_helpers import _fetch_validated, _SSRFError

    name = name.strip()
    if not name or len(name) > 200:
        return None

    encoded = quote(name, safe="")
    url = (
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
        f"{encoded}/property/CanonicalSMILES/JSON"
    )
    try:
        response = await _fetch_validated(
            url,
            enforce_domain_allowlist=True,
            timeout=8.0,
            headers={"Accept": "application/json"},
        )
    except _SSRFError as e:
        logger.warning("pubchem_ssrf_rejected name=%s err=%s", name, e)
        return None
    except Exception:
        logger.warning("pubchem_fetch_failed name=%s", name, exc_info=True)
        return None
    if not response.is_success:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    props = (
        body.get("PropertyTable", {}).get("Properties", [])
        if isinstance(body, dict)
        else []
    )
    if not props or not isinstance(props[0], dict):
        return None
    smiles = props[0].get("CanonicalSMILES") or props[0].get("SMILES")
    return smiles if isinstance(smiles, str) and smiles else None


# ── LLM-driven entity extraction ─────────────────────────────────────────────

_EXTRACTION_SYSTEM_PROMPT = """\
You extract chemistry-relevant entities from a document excerpt for a \
pharma R&D knowledge base. Be conservative — only emit entities the text \
explicitly mentions or directly implies.

Compounds: every distinct chemical compound (drug name, common name, \
IUPAC, code like "GSK1234") mentioned in the text. Include a one-sentence \
context snippet from the source so the curator can verify the mention.

Citations: every DOI mentioned other than the paper's own DOI (which the \
caller already extracted). PubMed IDs are acceptable too — emit them as \
"PMID:12345678".

Be brief: at most 20 compounds and 20 citations. Use canonical names where \
the text gives them; otherwise the literal string the text uses."""


_EXTRACTION_TOOL_SCHEMA: dict[str, Any] = {
    "name": "extract_entities",
    "description": "Emit the structured entity list for this document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "compounds": {
                "type": "array",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "maxLength": 200},
                        "context": {"type": "string", "maxLength": 400},
                    },
                    "required": ["name", "context"],
                },
            },
            "citations": {
                "type": "array",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "identifier": {"type": "string", "maxLength": 200},
                        "context": {"type": "string", "maxLength": 400},
                    },
                    "required": ["identifier", "context"],
                },
            },
        },
        "required": ["compounds", "citations"],
    },
}


# Default to Haiku 4.5 — entity extraction is a low-difficulty structured-
# output task; the cost/latency on Haiku is much better than Sonnet/Opus
# for this workload. Override via ENTITY_EXTRACTION_MODEL env if needed.
_DEFAULT_EXTRACTION_MODEL = "claude-haiku-4-5-20251001"


async def extract_entities_from_text(
    text: str,
    *,
    max_chars: int = 8000,
    timeout_sec: float = 20.0,
) -> dict[str, Any]:
    """Ask Claude to pull compounds + citations out of a doc excerpt.

    Returns ``{"compounds": [...], "citations": [...]}`` or
    ``{"compounds": [], "citations": [], "error": str}`` on any failure.
    Best-effort: the upload path should never raise out of this call.

    Input is truncated to ``max_chars`` (default 8000) to keep tokens
    bounded; entity extraction off the first ~2000 words of a paper is
    typically sufficient for the abstract + intro + first results.

    Imports `anthropic` lazily so the broader app can import this module
    even when the SDK isn't available (e.g. unit tests).
    """
    import asyncio
    import os

    if not text or not text.strip():
        return {"compounds": [], "citations": []}

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        logger.warning("entity_extraction_anthropic_sdk_missing")
        return {"compounds": [], "citations": [], "error": "anthropic SDK missing"}

    snippet = text[:max_chars]
    model = os.environ.get("ENTITY_EXTRACTION_MODEL") or _DEFAULT_EXTRACTION_MODEL
    client = AsyncAnthropic()

    try:
        # The Anthropic SDK's overloaded TypedDicts don't accept our
        # plain-dict tool/tool_choice/messages shapes without a cast;
        # the runtime API accepts them fine.
        response = await asyncio.wait_for(
            client.messages.create(  # type: ignore[call-overload]
                model=model,
                max_tokens=2000,
                system=_EXTRACTION_SYSTEM_PROMPT,
                tools=[_EXTRACTION_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "extract_entities"},
                messages=[{"role": "user", "content": snippet}],
            ),
            timeout=timeout_sec,
        )
    except TimeoutError:
        logger.warning("entity_extraction_timed_out chars=%d", len(snippet))
        return {"compounds": [], "citations": [], "error": "timeout"}
    except Exception:
        logger.exception("entity_extraction_failed chars=%d", len(snippet))
        return {"compounds": [], "citations": [], "error": "entity extraction failed"}

    # The response.content is a list of blocks; pull the tool_use block.
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "extract_entities":
            tool_input = getattr(block, "input", {}) or {}
            if isinstance(tool_input, dict):
                return {
                    "compounds": tool_input.get("compounds") or [],
                    "citations": tool_input.get("citations") or [],
                }
    logger.warning("entity_extraction_no_tool_block model=%s", model)
    return {"compounds": [], "citations": [], "error": "no tool block"}


async def fetch_crossref_metadata(doi: str) -> dict[str, Any] | None:
    """Fetch + normalize CrossRef metadata for `doi`.

    Returns None on any failure (network, 404, JSON parse). Logs the
    failure so it's visible in metrics; the caller falls back to the
    first-line-of-text title heuristic.

    Imports the SSRF helper lazily so unit tests of the regex /
    normalization paths above don't drag in httpx.
    """
    from api.agent.tool_helpers import _fetch_validated, _SSRFError

    url = f"https://api.crossref.org/works/{doi}"
    try:
        response = await _fetch_validated(
            url,
            enforce_domain_allowlist=True,
            timeout=10.0,
            headers={"Accept": "application/json"},
        )
    except _SSRFError as e:
        logger.warning("crossref_ssrf_rejected doi=%s err=%s", doi, e)
        return None
    except Exception:
        logger.warning("crossref_fetch_failed doi=%s", doi, exc_info=True)
        return None
    if not response.is_success:
        logger.info("crossref_not_found doi=%s status=%d", doi, response.status_code)
        return None
    try:
        body = response.json()
    except ValueError:
        logger.warning("crossref_invalid_json doi=%s", doi)
        return None
    return normalize_crossref_response(body)
