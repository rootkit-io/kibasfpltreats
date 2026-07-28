# Admin Panel — React/Next.js architecture (proposal)

Status: proposed (Phase 5 pivot). Backend contract is frozen by ADR-0001..0004;
this document designs against the API exactly as it exists in
`src/fpl_xpts/api.py`.

## Constraints inherited from the backend

1. `POST /api/v1/admin/projections/run` accepts **JSON states**
   (`manual_minutes`, `overrides`), not files. The weekly CSV is parsed
   **client-side** and submitted as JSON -- this is the in-memory route from
   ADR-0001 (zero disk I/O on the request path). We do not upload files.
2. Contract violations return **400 with field errors**
   (`{loc: ["manual_inputs", layer, row, field], msg, type}`) -- the UI maps
   `row` back to CSV line `row + 2` and highlights it.
3. Auth is the `X-Admin-Token` shared secret -- it must **never reach the
   browser**. All calls go through Next.js Route Handlers (BFF proxy) which
   attach the token from server-side env.
4. The API is synchronous **by decision** (no job queue until timeouts force
   it). The UI treats a run as one long request: preview defaults to
   `include_mc=false` (fast); MC + `save_as_draft=true` is the explicit
   "Run & Save Draft" action.

## Stack

- Next.js (App Router) + TypeScript
- **TanStack Query** for server state (mutations: run, publish; queries: run
  history when the endpoint exists)
- **TanStack Table** for the preview grids (sort/filter/virtualized -- the
  weekly table is ~700 rows, player_fixture more)
- **PapaParse** for client-side CSV parsing
- **zod** schema mirroring `PlayerMinutesState` for *pre-flight* validation
  (fast feedback; the server contract remains the source of truth)
- Types generated from FastAPI's OpenAPI via `openapi-typescript` -- the
  Pydantic contracts extend into the frontend type system
- shadcn/ui (or equivalent) for primitives

## Component architecture

```
app/
  (admin)/projections/page.tsx        server shell; reads nothing sensitive
  api/admin/[...proxy]/route.ts       BFF: forwards to FastAPI, injects
                                      X-Admin-Token from server env, never
                                      caches, maps 401/503 to login/ops states

components/admin/
  WeeklyRunWizard.tsx                 client; owns the workflow state machine
  ├── MinutesCsvDropzone.tsx          file drop → PapaParse → zod pre-check
  ├── MinutesReviewTable.tsx          parsed rows grid; edit-in-place for
  │                                   quick fixes; contract-error row
  │                                   highlighting (400 loc → CSV line)
  ├── RunControls.tsx                 season select (required for drafts),
  │                                   include_mc toggle, notes, buttons:
  │                                   [Preview] [Run & Save Draft]
  ├── ContractErrorPanel.tsx          renders 400 {message, errors[]} with
  │                                   loc→line mapping; links to grid rows
  ├── PreviewTabs.tsx
  │   ├── ProjectionsGrid.tsx         generic TanStack Table over
  │   │                               tables.weekly / tables.player_fixture
  │   │                               (expected_minutes, xPts, components;
  │   │                               DGW breakdown on the fixture tab)
  │   └── SimulationGrid.tsx          MC brackets/percentiles when include_mc
  └── PublishBar.tsx                  run_id chip + draft status + [Publish]
                                      with confirm dialog ("this flips the
                                      public dashboard"); handles 404/409

  RunHistoryPanel.tsx                 recent runs + statuses (needs the small
                                      GET endpoint -- see gaps)
```

## The workflow state machine (WeeklyRunWizard)

```
idle → parsed(rows, preErrors) → running → previewed(tables, run_id|null)
                     ↑                          │
                     └── contract_error(400) ←──┘
previewed(run_id) → publishing → published(published_at)
                        └── conflict(409) / not_found(404)
```

- CSV column mapping happens at parse time: `mins → likely_minutes`,
  `start → start_probability`, `GW → gameweek`, `chance_of_playing` passes
  through (percent normalisation is the contract's job, server-side).
- `save_as_draft=true` requires `season` -- the UI enforces it before the
  server 400s (fail fast on both sides).
- The preview tables live **in client memory** from the run response;
  a page refresh loses the preview but not the draft (run_id is in the URL:
  `?run=<id>` once saved).

## Deliberate non-goals (v1)

- No file upload endpoint, no server-side CSV storage -- the CSV is a local
  editing artifact; the contract states are the payload.
- No optimistic publish -- publishing flips the public dashboard; it is a
  confirmed, awaited mutation.
- No websockets/polling -- synchronous API by decision; revisit only when
  run duration breaks request timeouts (the pre-agreed async-queue trigger).

## Small backend additions the UI will want (not blockers for v1)

1. `GET /api/v1/admin/projections/runs` -- run history (repository
   `list_runs` already exists; needs a route).
2. `GET /api/v1/admin/projections/runs/{run_id}` -- run header + optionally
   persisted tables, so a refreshed page can re-hydrate a draft preview.
3. CORS or same-origin deployment decision for the FastAPI service (moot if
   all traffic rides the BFF proxy -- recommended).
