# Falkland V3 Stations Frontend

Falkland V3 stations front-end powered by Vite + React + TypeScript.

## Getting Started

```bash
cd projects/FalklandV3/frontend/stations
npm install    # or pnpm install / yarn
npm run dev    # launches Vite dev server at http://localhost:5173
# optional: configure API key for protected commands
# export VITE_FALKLAND_API_KEY=changeme
```

The dev server proxies API calls to `http://localhost:8000`, so run `falklandv3-serve` in parallel.

## Available Scripts

- `npm run dev` – start Vite in development mode
- `npm run build` – production build to `dist/`
- `npm run preview` – preview the built bundle
- `npm test` – run Vitest unit tests

Linting is not yet configured; `npm run lint` is a placeholder.

## Structure

- `src/App.tsx` drives the station console selector and renders NAV/RADAR/WPN panels. The NAV station now pages between **Overview**, **Fleet**, and **History** panes to stay within an 800 × 480 viewport.
- `src/api/client.ts` wraps calls to the backend `/api/status` endpoint.
- `src/styles.css` provides the compact glass cockpit skin and the shared pager styles used by NAV.

The goal is to evolve this workspace into modular station consoles that plug into the V3 backend schemas. Frontend tests live under `src/` and run via Vitest.

## Rebuild Log

- **2025-10-27** · NAV station rebuilt around the V2-inspired three-panel layout (Overview & Orders, Fleet Snapshot, Navigation History). The new pager keeps each pane no-scroll within 800 × 480 and shares state via `navPanel`. Styles were tightened to fit the Raspberry Pi touchscreen target and introduce reusable badge/table widgets for future stations.
