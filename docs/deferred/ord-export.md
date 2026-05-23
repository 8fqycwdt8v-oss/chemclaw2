# ORD export

## Status

Deferred. Trigger: partner request for Open Reaction Database interchange.

## Sketch

Admin-only endpoint at `/api/admin/reactions/export-ord` that streams
the reactions table as ORD-format JSON (one reaction per line). Owner-
filter dropped — ORD export is necessarily cross-user (it's a research
artifact, not a per-user resource).

ORD schema: https://github.com/open-reaction-database/ord-schema.
Mapping from chemclaw2's `reactions` + `reaction_conditions` +
`reaction_outcomes` should be mechanical once the partner confirms the
schema version they expect.

## Prerequisites

- Confirm ORD schema version with the partner.
- Decide on streaming format (JSONL is easiest; protobuf if the partner
  needs ord-schema's native).
- Rate-limit + audit-log the export (it's a big read against multiple
  tables).
