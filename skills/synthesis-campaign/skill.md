# synthesis-campaign

Plan and execute a multi-step synthesis campaign for a target molecule.

## When to use
The user asks to design or run a multi-step synthesis route, prepare a campaign for a target, or kick off an experimental sequence.

## Workflow
1. Call `start_synthesis_campaign` with the current `session_id` and `target_smiles` to get a `campaign_id` and `status='planning'`.
2. Use `compound_similarity_search` and `find_similar_reactions` to ground the proposed route in prior work — cite the matched compounds and reactions.
3. Call `confirm_synthesis_plan` with the plan (steps array with `reaction_smiles` + `conditions`). The plan moves to `awaiting_input`.
4. **Ask the user for explicit confirmation** before kicking off execution. Summarize the plan, total step count, and any safety concerns.
5. Call `kickoff_campaign` with `approval: 'per_step'` for high-risk routes, otherwise `all_at_once`.
6. Per-step results land in `campaign_steps.result` with nearest-reaction neighbors from `find_similar_reactions`. The agent should read these when investigating outcomes.
7. On completion, the worker auto-creates a wiki page at `/wiki/campaign-<id>`.

## Safety
- Always run `lookup_hazard` on any unusual reagent before recommending it.
- The scheduled-substance gate is enforced before the chat turn; do not attempt synthesis plans for controlled substances.
