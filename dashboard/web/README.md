# Portfolio Dashboard (web)

Next.js + shadcn/ui frontend for the read-only portfolio dashboard. It reads
already-computed pipeline output from the FastAPI backend in `../api`; it never
talks to the database or the pipeline directly. See
`../../docs/architecture/dashboard_api.md` for the backend contract and the
visual→data map.

## Prerequisites

The backend must be running (it serves the data this app renders):

```powershell
# from the repo root
uv run uvicorn main:app --port 8000 --app-dir dashboard/api
```

## Configuration

`NEXT_PUBLIC_API_URL` is the backend base URL. It defaults to
`http://localhost:8000` in code, and `.env.local` (git-ignored) sets it
explicitly:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Server components fetch from this URL on the Next server (no CORS); the Stocks
price explorer fetches from the browser (the backend's CORS allowlist,
`DASHBOARD_CORS_ORIGINS`, must include this app's origin — it allows
`http://localhost:3000` by default).

## Development

```bash
npm install     # first time
npm run dev      # http://localhost:3000
npm run build    # production build + typecheck
```

## Structure

- `src/app/*` — routes (Overview `/`, Portfolio, Stocks, ETFs, Income, Data
  Quality, and the shared `/holdings/[symbol]` detail). One root `loading.tsx`
  and `error.tsx` cascade to all routes.
- `src/components/*` — shell (sidebar, header, theme) and page components
  (`charts/`, `tables/`, `stocks/`).
- `src/lib/*` — `api.ts` (typed fetchers), `types.ts` (API payload types),
  `format.ts` (display helpers), `derive.ts` (client-side math: blended MER,
  index-to-100 compare, group colors).
- `src/app/globals.css` — theme tokens, including the categorical chart palette
  (`--chart-1..5`) and semantic `--gain`/`--loss`.
