# Admin Panel (Next.js) — scaffold

Phase 6 scaffold: BFF proxy routes, Zod contract mirrors, and the
WeeklyRunWizard state-machine shell. See
`../../docs/frontend/admin-panel-architecture.md` for the full design.

## Bootstrap (once)

```bash
npx create-next-app@latest . --typescript --tailwind --app --src-dir=false
npx shadcn@latest init
npm install zod papaparse @tanstack/react-query @tanstack/react-table
```

Then drop the scaffolded `app/`, `lib/`, and `components/` files in (they
assume the `@/*` path alias from create-next-app's tsconfig).

## Environment (server-side only — never NEXT_PUBLIC)

| Variable | Purpose |
|---|---|
| `ADMIN_API_TOKEN` | Shared secret injected by the BFF proxy; fail-closed 503 when unset |
| `FPL_BACKEND_URL` | FastAPI base URL (default `http://127.0.0.1:8000`) |

## What exists vs. what's next

- ✅ `lib/api/bff.ts` + run/publish route handlers (token injection, verbatim
  400 pass-through, timeout → 504)
- ✅ `lib/validations/minutes.ts` — Zod mirrors of PlayerMinutesState /
  MinuteOverrideState + CSV column mapping + line-numbered preflight
- ✅ `components/admin/WeeklyRunWizard.tsx` — state machine + layout shell
- ⏭ next: PapaParse dropzone, TanStack review/preview tables, run history panel
