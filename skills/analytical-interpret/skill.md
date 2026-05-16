# analytical-interpret

Interpret analytical observations (NMR / MS / IR) and validate proposed structures against measured data.

## When to use
The user pastes peaks, fragments, or signals from an analytical run, asks "what does this spectrum mean," or asks the agent to verify a proposed structure.

## Workflow
1. Capture `technique` ∈ {NMR, MS, IR}, the `observations` text, and (optionally) a proposed structure SMILES.
2. If a SMILES is supplied, call `mcp-molfp.validate_smiles` then `mcp-molfp.compute_morgan_fp` to get `proposed_fingerprint_bits`.
3. Call `interpret_analytical_result` with technique, observations, optional structure, and fingerprint. The tool fetches nearest-neighbor compounds (Tanimoto ≥ 0.3) for context.
4. Use the returned `nearest_neighbors` to ground peak assignments. Cite each neighbor by name + CAS.
5. Call `wiki_lookup` for any neighbor name to bring in our internal knowledge.
6. State uncertainty explicitly when a peak doesn't fit the proposed structure; propose follow-up experiments.

## Output shape
- For NMR: chemical shift → assignment → fit-or-mismatch verdict per signal.
- For MS: parent ion + likely fragmentation paths + impurity hypotheses.
- For IR: characteristic bands → functional groups → consistency check.
