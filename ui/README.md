# decider-lab Studio (frontend)

React 18 + TypeScript + Vite + Tailwind, built into `src/decider_lab/ui/static/` and served by
`decider-lab ui`. Contracts: [docs/ui/DESIGN.md](../docs/ui/DESIGN.md) (what people see) and
[docs/ui/API.md](../docs/ui/API.md) (wire shapes; `src/api/types.ts` transcribes it).

```bash
cd ui && npm ci                     # once
npm run dev                         # Vite on :5173, /api proxied to BACKEND_PORT (default 7861)
npm run typecheck                   # tsc -b
npm run build                       # writes ../src/decider_lab/ui/static (commit it)
npm run test:e2e                    # build + Playwright (desktop 1440x900 and mobile 390x844)
npm run test:e2e:nobuild -- e2e/labs.spec.ts --project=desktop   # one spec, no rebuild
```

Dev loop with a seeded backend: `E2E_PORT=7861 ../.venv/bin/python ../scripts/e2e_server.py`, then
`npm run dev` and open `http://localhost:5173/?token=e2e-token-0123456789`.

## Layout and ownership

| path | what | owner |
|---|---|---|
| `src/app/` | providers, router composition, AppShell (topbar, sidebar, drawer, banners), nav, default palette commands | foundation |
| `src/components/` | design-system components (`@/components`) | foundation; extend, do not fork |
| `src/charts/` | ChartFrame (title, caption, aria, table toggle), TimeSeriesChart and BarSeriesChart (Recharts), Sparkline, CIBar, palette | foundation; Results/Jobs add their charts here |
| `src/palette/` | command registry (`useRegisterCommands`), CommandPalette, ShortcutHelp | foundation |
| `src/hooks/` | `useMeta`, `useHotkeys`, `useEventSource`, `useMediaQuery`, `useLocalStorage` | foundation |
| `src/lib/api.ts` | typed fetch client (`api.get/post/put/del`, `ApiError`, `openEventSource`, `fileUrl`, `seg`) | foundation |
| `src/lib/format.ts` | number/time/size formatting per DESIGN 2.3 | foundation |
| `src/theme/` | `tokens.css` (all colors as CSS variables), ThemeProvider | foundation |
| `src/api/types.ts` | API.md types | shared: change with API.md |
| `src/api/<area>.ts` | each area's client functions and query keys | the area |
| `src/pages/<Area>/` | `index.tsx` exports the area's `routes`; pages are lazy-loaded | the area |
| `e2e/<area>.spec.ts` | Playwright specs; import `{ test, expect }` from `./fixtures` | the area |

Rules: semantic tokens only (Tailwind `bg-surface-1`, `text-muted`, `text-accent`, …; never raw
hex); every control a test clicks has the `data-testid` from DESIGN.md section 12; every page has
exactly one `h1` (use `PageHeader`); errors from loads use `ErrorState`, from actions an inline
`Callout tone="danger"`; toasts only for success.

## e2e

`playwright.config.ts` starts `scripts/e2e_server.py`: a fresh seeded workspace (first-lab with a
fake System One server and baselines, already run on smoke and synthetic:per_kind=40 with +cal;
second-lab never run; an invalid broken-lab; `data/eval.jsonl`), `DECIDER_LAB_FAKE_CLOUD=1`, a temp
cache, token `e2e-token-0123456789`, port `E2E_PORT` (7871). One server serves all specs, so
`workers: 1`; a test that changes state creates its own objects. Fixture `studio` gives
`open(path)`, `nav(id)`, `api(path)` and `axe()`.
