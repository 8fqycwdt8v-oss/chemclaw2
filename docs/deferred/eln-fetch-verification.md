# ELN fetch-path contract verification

## Status

Deferred. Trigger: real customer ELN connected.

## Context

`api/agent/tools.py` `eln_fetch_experiment` calls
`{ELN_API_BASE_URL}/api/eln/experiments/{id}` and expects a specific
response shape. The exact path + response is a guess until a real ELN
is wired — different ELN vendors (Benchling, LabArchives, etc.) have
different REST surfaces.

## Verification checklist (once a customer ELN is in scope)

1. Hit the live ELN with a known experiment id (test data).
2. Confirm the response shape matches the assumed schema in
   `tools_eln.py` / `eln_payload.py`.
3. Update the SSRF allowlist (`ALLOWED_DOMAINS`) to include the ELN's
   hostname.
4. End-to-end test: agent → ELN → outcome record → wiki update.
5. Confirm webhook signature scheme (`ELN_WEBHOOK_SECRET`) matches
   what the ELN actually signs.
