# Plan: SharePoint/OneDrive drive sync → index → KG/wiki, on a bi-daily schedule

**Status:** proposed (planning only — no implementation code yet)
**Author:** automated planning pass, 2026-05-29
**Decisions locked with the requester:**

1. **Transport:** SharePoint/OneDrive via Microsoft Graph (app-only OAuth). *Not* raw
   SMB/CIFS — the app runs on Fly.io in ephemeral cloud containers behind an SSRF
   fail-closed egress guard (`_assert_not_private`), and a private file-server share
   would be both unreachable and actively blocked. Graph is reached over public HTTPS,
   so no VPN/tunnel/SSRF-exception is required.
2. **Knowledge depth:** auto-populate the **knowledge graph (world model)** per
   document, in addition to the searchable chunks + needs-review wiki draft that the
   existing upload flow already produces.
3. **Entra app registration:** does **not** exist yet. This plan documents the exact
   setup, and the connector is built against **mocked Graph responses** until real
   credentials are provisioned.

---

## 1. Why this shape

The destination machinery already exists and is strong. Once a document's text is in
hand, chemclaw2 already does: sliding-window chunking (`api/db/queries/paper_chunks.py`),
OpenAI embeddings into pgvector (`api/embeddings.py`), hybrid FTS+vector search with RRF
fusion (`api/db/queries/wiki_read.py`), PaperQA2-style retrieval + LLM reranking
(`api/db/queries/paper_rcs.py`, exposed as the `paper_qa` agent tool), a versioned/
bitemporal wiki with citations and contradiction tracking, deep-research sub-agents
(`subagent_type='deep-research'`), and a curator inbox for `needs_review` pages.

What is missing is **the front of the pipe and the clock**:

- No connector to any external file store (no SMB, SharePoint, S3, fsspec).
- No recursive corpus walk / change detection — the only ingest path is a human POSTing
  one file at a time to `POST /api/integrations/documents`
  (`api/routes/integrations.py:108`), MIME-capped to PDF/txt/md, 10 MB.
- No scheduler. The two workers (`fp_worker`, `campaign_worker`) are interval poll-loops
  with modulo-gated periodic passes — there is no wall-clock cadence and no sync cursor.
- The KG (`world_model_entries`, `hypotheses`) is populated **only** by the agent during
  a live chat; documents never write to it.

So the work is: **source connector + change-tracking + scheduling + a document→KG
extraction step**, reusing the existing index/search/wiki machinery wholesale.

### Existing facts the plan leans on

- `external_facts.source_id` is already `sha256(content)` (`integrations.py:168`), giving
  free content-hash idempotency/de-dup — an unchanged file re-reported by Graph no-ops at
  upsert.
- `external_facts` already carries `first_seen`/`last_seen` with a 24h re-fetch cache — a
  natural precedent for a sync cursor.
- `papers` + `paper_chunks` + wiki upsert are all reachable from a plain async function;
  the upload route is the only current caller.
- Workers mount in the FastAPI lifespan behind `RUN_WORKER_IN_PROCESS=1`
  (`api/main.py:36-60`) and follow a fixed pattern: poll loop, module-level `_in_flight`
  reentrancy guard, per-row Postgres advisory locks, startup/heartbeat/shutdown logs.

---

## 2. Architecture

```
                 Microsoft Graph (graph.microsoft.com, public HTTPS)
                 /sites/{site}/drives/{drive}/root/delta
                              │  (delta query → only items changed since last token)
                              ▼
   ┌──────────────────────────────────────────────────────────────┐
   │ sync_worker.py  (wall-clock gated ~every 12h via last_synced_at)│
   │   1. load delta_token from drive_sync_state                     │
   │   2. page through /delta → list of changed/new/deleted items    │
   │   3. for each changed file: download bytes → ingest_document()  │
   │   4. persist new delta_token + last_synced_at                   │
   └───────────────────────────────┬────────────────────────────────┘
                                   │
                                   ▼
   ┌──────────────────────────────────────────────────────────────┐
   │ ingest_document(content, filename, content_type, user_id, db)   │
   │   (refactored OUT of the upload route — single shared path)     │
   │   • extract text  (pdf / docx / pptx / xlsx / txt / md / html)  │
   │   • sha256 de-dup  • DOI → CrossRef  • LLM entity extraction     │
   │   • upsert_paper + chunk + embed  → paper_chunks                │
   │   • upsert_wiki_page (needs_review=True)  → curator inbox       │
   │   • NEW: extract_to_world_model(...) → KG facts + hypotheses    │
   └───────────────────────────────┬────────────────────────────────┘
                                   │
        ┌──────────────────────────┼───────────────────────────┐
        ▼                          ▼                            ▼
   paper_chunks               wiki (+citations)         world_model_entries
   (hybrid search,            (curator inbox,           + hypotheses
    paper_qa)                  versioned)               (per-drive corpus
                                                         investigation)
        └──────────────────────────┬───────────────────────────┘
                                   ▼
            Already-built consumers light up for free:
            chat agent tools (wiki_lookup, paper_qa, lookup_knowledge),
            hybrid /api/search, deep-research + literature/wiki explorer sub-agents
```

---

## 3. Work breakdown (sequential PRs off `main`)

Per CLAUDE.md: single worktree, branch sequentially, ship each slice through CI to
`main`. Each slice below is one PR.

### Slice 1 — Graph connector + sync state (build against mocks)

**New module `api/integrations/sharepoint/graph_client.py`**
- App-only OAuth via `msal.ConfidentialClientApplication` (client-credentials grant),
  token cached in-process with expiry.
- `async def delta(drive_id, token) -> (items, next_token)` — pages through
  `/drives/{drive_id}/root/delta`, following `@odata.nextLink` / `@odata.deltaLink`.
- `async def download(item) -> bytes` — fetches `@microsoft.graph.downloadUrl`.
- All outbound calls go through the existing SSRF-pinned `_fetch_validated` helper
  (`api/agent/tool_helpers.py`) with `graph.microsoft.com` + the login/token host added
  to the allowlist. Resolve-once / bind-resolved-IP per CLAUDE.md security rule 5.
- **Env vars** (read inside the factory, never at module import — CLAUDE.md rule):
  `MSGRAPH_TENANT_ID`, `MSGRAPH_CLIENT_ID`, `MSGRAPH_CLIENT_SECRET`, `MSGRAPH_DRIVE_ID`.
  Add to `.env.example` under a new "SharePoint / Microsoft Graph (optional)" block.
- **New dep:** `msal>=1.31`.

**New migration `migrations/0044_drive_sync_state.sql`**
> Before writing: `git fetch origin main && ls migrations/` and confirm 0044 is still
> the lowest free slot (highest on main today is 0043).
```sql
CREATE TABLE drive_sync_state (
    drive_id        text PRIMARY KEY,
    delta_token     text,
    last_synced_at  timestamptz,
    last_status     text,                 -- 'ok' | 'error'
    last_error      text
);
```
Query functions in **`api/db/queries/drive_sync.py`** (only the queries layer touches
SQLAlchemy primitives — CLAUDE.md): `get_sync_state`, `save_sync_state`. Wrap the
token+timestamp write in `async with session.begin()`.

**Tests:** unit-test `delta()` paging and `download()` against recorded/mocked Graph
JSON (no live tenant). Test the sync-state round-trip.

### Slice 2 — extract `ingest_document` + broaden formats

- Refactor the body of `upload_document` (`api/routes/integrations.py:108-329`) into
  `api/integrations/ingest.py::ingest_document(...)`. Both the HTTP route and the worker
  call it (Extract-on-third-copy / DRY — there will be two callers immediately, a third
  is the test harness).
- Add extractors: `.docx` (`python-docx`), `.pptx` (`python-pptx`),
  `.xlsx` (`openpyxl`), `.html` (stdlib + `selectolax` or `BeautifulSoup`). New deps,
  imported inside the function for graceful degradation (matches the existing `pypdf`
  pattern at `integrations.py:156`).
- Lift the 8 KB truncation on LLM entity extraction
  (`document_enrichment.py` input bound) for long documents — chunk the extraction or
  raise the cap, with a token-budget guard.
- The 10 MB cap and MIME allowlist move into `ingest_document` and widen to the new
  formats.

**Tests:** round-trip a sample of each format → assert text extracted, paper + chunks +
wiki draft created, re-ingesting the same bytes no-ops (sha256 de-dup).

### Slice 3 — document → KG (the new capability)

- **New `api/integrations/kg_extraction.py::extract_to_world_model(text, source_ref, investigation_id, db)`**:
  one LLM pass (structured tool-use, like `extract_entities_from_text`) emitting
  candidate `world_model_entries` (kind ∈ {`fact`, `evidence`}, each with `confidence`
  0–1 and a `payload` carrying the source span) and optional `hypotheses`.
- Each entry/hypothesis carries a `WikiCitation` back to the document's wiki page so the
  KG is auditable and the agent can cite it. FK-referenced ids verified before insert
  (CLAUDE.md security rule 6); writes go through the existing `world_model` /
  `hypotheses` query functions, wrapped in a transaction.
- **Per-drive "corpus" investigation:** on first sync, `start_investigation` for the
  drive (e.g. "SharePoint corpus: {drive}") and anchor all extracted entries to it, so
  chat / deep-research can query the whole ingested corpus as one world model.
- Best-effort + observable: failures return `{"ok": bool, "error": ...}` and are logged,
  never silently swallowed (CLAUDE.md observability rules).

**Tests:** feed a known doc, assert world-model entries + citations created and linked to
the corpus investigation; assert a low-confidence extraction is stored with its
confidence, not dropped.

### Slice 4 — scheduled sync worker

- **New `api/workers/sync_worker.py`** following the `campaign_worker` template: poll
  loop, module-level `_in_flight` guard cleared in `finally`, Postgres advisory lock so
  only one instance syncs a drive, startup/heartbeat/shutdown logs.
- **Wall-clock cadence:** poll every ~5 min, but only *run a sync* when
  `now() - last_synced_at >= SYNC_INTERVAL_HOURS` (default 12 → true bi-daily). This is
  wall-clock, not interval-relative, so restarts don't reset the cadence.
- Mounted in `api/main.py` lifespan behind the existing `RUN_WORKER_IN_PROCESS=1`, plus a
  `RUN_SYNC_WORKER` gate so the drive sync can be toggled independently of fp/campaign.
- **Admin trigger:** `POST /api/admin/drive-sync/run` (admin-auth, per CLAUDE.md security
  rule 3 — anything that drives ingestion is admin-write) to force a sync on demand /
  for the first backfill. Health endpoint reflects `drive_sync_state.last_status`.

**Tests:** monkeypatch `SYNC_INTERVAL_HOURS` low (as the existing worker tests do with
`POLL_INTERVAL_SECONDS`); assert a due drive syncs, a not-yet-due drive is skipped, and a
Graph error sets `last_status='error'` without crashing the loop.

---

## 4. Microsoft Entra (Azure AD) app registration — setup steps

Provide these to whoever administers the M365 tenant. The connector cannot reach a real
drive until this is done; Slices 1–3 are built and tested against mocks in the meantime.

1. **Entra admin center → App registrations → New registration.** Name it
   (e.g. "chemclaw2-drive-sync"), single-tenant, no redirect URI (app-only / daemon).
2. **Certificates & secrets → New client secret.** Record the secret *value* (shown
   once) → `MSGRAPH_CLIENT_SECRET`. Record **Application (client) ID** →
   `MSGRAPH_CLIENT_ID` and **Directory (tenant) ID** → `MSGRAPH_TENANT_ID`.
3. **API permissions → Add → Microsoft Graph → Application permissions** (not delegated):
   `Sites.Read.All` and `Files.Read.All` (read-only — we never write to the drive).
   Then **Grant admin consent** for the tenant.
4. **Find the drive id:** call `GET /sites/{hostname}:/sites/{site-path}` to get the site
   id, then `GET /sites/{site-id}/drives` to list drives → `MSGRAPH_DRIVE_ID`. Document
   the exact site/library being indexed.
5. Set the four vars via `fly secrets set` in prod and in `.env` for local dev.

**Least privilege note:** `Sites.Read.All` is tenant-wide read. If the tenant admin wants
to scope to a single site, use **Sites.Selected** + an explicit per-site grant instead —
worth offering them the choice.

---

## 5. Risks / tradeoffs to surface

- **`Sites.Read.All` is broad.** Tenant-wide application read. Flag Sites.Selected as the
  least-privilege alternative; let the tenant admin decide.
- **Deletions.** Graph delta reports removed items. Decide policy: mark the wiki page
  archived / tombstone the `external_facts` row vs. leave stale. Proposal: mark the wiki
  page `needs_review` with a "source removed" note rather than hard-delete, so a curator
  decides. (Open question — confirm before Slice 2.)
- **KG extraction cost.** A per-document LLM pass on every changed file adds token cost
  that scales with corpus churn. Mitigate with the existing budget caps
  (`project_budgets`) and only re-extract when content hash changes.
- **Large libraries / first backfill.** The initial sync of a big library is a burst of
  embedding + LLM calls. Throttle: bounded concurrency per cycle (mirror fp_worker's
  batch-of-50), and let the 12h cadence + delta token spread steady-state load.
- **OCR / scanned PDFs.** `pypdf` extracts no text from image-only PDFs. Out of scope for
  v1; log to `BACKLOG.md`.
- **Entra creds absent at first merge.** The worker no-ops (logs a clear "Graph not
  configured" warning, like `eln_webhook_not_configured`) until the four env vars are
  set — slices 1–4 ship and stay dormant until provisioned.

---

## 6. Success criteria

- A document placed in the SharePoint library appears, within one sync cycle, as: a
  searchable `paper` + `paper_chunks` (findable via `/api/search` and `paper_qa`), a
  `needs_review` wiki page in the curator inbox with citations, and ≥1 `world_model_entry`
  linked to the per-drive corpus investigation with a citation back to the page.
- Re-running the sync with no drive changes does zero embedding/LLM work (delta token +
  sha256 de-dup both no-op).
- The chat agent and deep-research sub-agents retrieve and cite the ingested content with
  no changes to their code.
- Sync runs unattended on the ~12h cadence; `drive_sync_state.last_status` and the health
  endpoint reflect the last run.

---

## 7. Out of scope (log to BACKLOG.md when starting)

- OCR for scanned/image PDFs.
- Writing back to SharePoint (read-only by design).
- Non-Graph transports (raw SMB/CIFS) — explicitly rejected above.
- Real-time push (Graph change-notification webhooks) — the 12h pull cadence is the
  requirement; webhooks are a future efficiency upgrade.
