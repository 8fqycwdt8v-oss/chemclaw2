---
name: chemistry-search
description: Find compounds and reactions in the registry by structural similarity or substructure. Use when the user pastes a SMILES, reaction SMILES, or SMARTS pattern, asks whether anything similar has been seen before, asks what scaffolds are in the library, or wants compound or reaction neighbors before designing a new analog.
---

# chemistry-search

Find compounds and reactions in the registry by structural similarity or substructure pattern.

## When to use
The user pastes a SMILES, reaction SMILES, or SMARTS pattern; asks "have we seen anything like this," "what scaffolds are in our library," or wants compound/reaction neighbors before designing a new analog.

## Workflow
### Similarity (compound or reaction)
1. For a compound query: call `mcp-molfp.compute_morgan_fp` to get the 2048-bit fingerprint string.
2. For a reaction query: call `mcp-rxnfp.compute_drfp` instead.
3. Pass the fingerprint to `compound_similarity_search` (compounds) or `find_similar_reactions` (reactions). Optional filters: `min_similarity` (default 0.4), `limit`.
4. Apply metadata filters (`created_after`, `has_cas`, `project`) when the user scopes their question to a project or timeframe.
5. Return ranked hits with Tanimoto scores; cite by name + CAS or reaction ID.

### Substructure
1. Validate the SMARTS — if uncertain, call `mcp-molfp.substructure_match` against a test compound first to confirm the pattern is valid.
2. Call `list_substructure_candidates` (max 5000) to get the candidate set.
3. Iterate `mcp-molfp.substructure_match(smiles, smarts)` per candidate until you reach the user's requested limit or exhaust candidates.

## Performance hints
- Similarity search uses HNSW pre-filter + exact Tanimoto re-rank — both stages are fast and deterministic.
- Substructure search is O(N) over the candidate set. For datasets > ~1k compounds, narrow with similarity first if possible.
