# ML property predictions

## Status

Deferred. Trigger: deterministic descriptors stop being sufficient
(operator asks for yield / tox / hazard predictions on novel structures).

## Context

`compute_descriptors` (mcp_molfp) ships every deterministic RDKit
property — logP, MW, TPSA, HBA/HBD, rotatable bonds, aromatic rings,
heavy atoms, Lipinski. These are exact, fast, and reproducible.

ML-based predictors (yield, tox, hazards) are harder: they require a
training pipeline, model versioning, calibration scoring, and an
operator-visible confidence interval. Per CLAUDE.md operating
principles, they're off-the-shelf or they don't get built.

## Off-the-shelf options

- Chemprop (uncertainty-aware yield) — pretrained on USPTO; reasonable
  baseline.
- Tx-LSTM / RxnFP-based downstream regression — needs training data.
- IBM RXN API — already wired via `RXN_*` env vars.

## Triggers

- A campaign worker run requests "predict yield" and the response is
  generic ("not implemented"). Capture that signal in audit-log.
- An operator file (Slack/issue) explicitly asks.
