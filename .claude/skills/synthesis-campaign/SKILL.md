---
name: synthesis-campaign
description: Plan and execute a multi-step synthesis campaign for a target molecule. Use when the user asks to design or run a multi-step synthesis route, prepare a campaign for a target, or kick off an experimental sequence.
---

# synthesis-campaign

Plan and execute a multi-step synthesis campaign for a target molecule.

## When to use
The user asks to design or run a multi-step synthesis route, prepare a campaign for a target, or kick off an experimental sequence.

## Workflow
1. Call `start_synthesis_campaign` with the current `session_id` and `target_smiles` to get a `campaign_id` and `status='planning'`.
2. Use `compound_similarity_search` and `reaction_similarity_search` to ground the proposed route in prior work — cite the matched compounds and reactions.
3. For each candidate step, predict conditions in this order:
   a. Compute the DRFP via `mcp-rxnfp.compute_drfp(reaction_smiles)`.
   b. Call `suggest_conditions_from_neighbors` with the DRFP bits. If ≥ 3 neighbors return at similarity ≥ 0.5, ground conditions in those neighbors and cite the reaction ids.
   c. If neighbor coverage is sparse, call `mcp-rxn-conditions.predict_conditions(reaction_smiles)`. On any error from the predictor, fall back to whatever neighbors you do have — never block the plan on a predictor outage.
   d. Call `record_predicted_conditions` to cache the chosen conditions (model = `rxn4chemistry:<version>` or `neighbor-aggregation:v1`, source = `rxn4chemistry` or `neighbor_aggregation`). The cache hit on the next turn pays for itself.
4. Call `confirm_synthesis_plan` with the plan (steps array with `reaction_smiles` + human-readable `conditions`). The plan moves to `awaiting_input`.
5. **Ask the user for explicit confirmation** before kicking off execution. Summarize the plan, total step count, and any safety concerns.
6. Call `kickoff_campaign` with `approval: 'per_step'` for high-risk routes, otherwise `all_at_once`.
7. Per-step results land in `campaign_steps.result` with the linked `prediction_id` so you can compare predicted vs. actual on follow-up turns.
8. On completion, the worker auto-creates a wiki page at `/wiki/campaign-<id>`.

## Safety
- Always run `lookup_hazard` on any unusual reagent before recommending it — the RXN4Chemistry predictor will happily suggest pyrophorics or controlled reagents.
- The scheduled-substance gate is enforced before the chat turn; do not attempt synthesis plans for controlled substances.
