# Deferred work

Tier F items from BACKLOG.md are blocked on an external trigger
(customer demand, partner request, milestone, measurement). Each lives
in its own file with: trigger condition, prerequisite checklist, risk /
cost / value notes.

| Item                                       | Trigger                       |
|--------------------------------------------|-------------------------------|
| [Multi-tenant RLS](./multi-tenant-rls.md)  | tenants > 1                   |
| [ORD export](./ord-export.md)              | partner request               |
| [ML property predictions](./ml-property-predictions.md) | deterministic descriptors no longer sufficient |
| [Tool forging](./tool-forging.md)          | v3 milestone                  |
| [ELN fetch verification](./eln-fetch-verification.md) | real customer ELN connected |

Active work is in `BACKLOG.md`. Items here are decisions to NOT build,
captured so a future engineer can flip them on without re-discovering
the prerequisites.
