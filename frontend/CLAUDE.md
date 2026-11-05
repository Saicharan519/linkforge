# Frontend rules (React + TypeScript)

## Layout
```
frontend/
  src/
    main.tsx  App.tsx  routes.tsx
    api/         # typed fetch client (credentials: 'include', adds X-CSRF-Token), one file per resource
    hooks/       # TanStack Query hooks: useLinks, useLink, useAnalytics, useOverview, useSession...
    pages/       # Landing, Login, Register, Dashboard, Links, LinkAnalytics, Settings
    components/  # ui/ (shadcn), layout/, charts/, links/ (LinkForm, LinksTable...)
    lib/         # utils, formatters
  vite.config.ts   # dev proxy: /api -> http://localhost:8000
  nginx.conf       # prod-like: serve SPA, proxy /api -> api:8000 (compose only; in K8s ingress does routing)
  Dockerfile (multi-stage, nginx-unprivileged)  .dockerignore
```

## Rules
- TypeScript `strict: true`. No `any` (use `unknown` + narrowing). No unused vars.
- Server state = TanStack Query only. No Redux. Local UI state = useState/useReducer.
- Auth is cookie-session based: never store tokens in localStorage. All requests `credentials: 'include'`. For POST/PATCH/DELETE read the `lf_csrf` cookie and send it as `X-CSRF-Token`.
- 401 from API => clear user cache and redirect to `/login` (except on `/auth/me` at boot).
- Same-origin only: the browser calls relative `/api/v1/...`. No CORS config, no absolute API URLs.
- Every data view must have loading, empty, and error states. Forms: inline validation errors from the API's 422 body.
- Charts: Recharts. **Two kinds of dates:** (1) timestamps (`created_at`, `last_seen`, `expires_at`, `clicked_at`) are UTC ISO — render in the user's local time; (2) daily analytics buckets are `YYYY-MM-DD` UTC calendar-date strings — render them AS-IS, never `new Date(str)` + local conversion (it shifts the day). Chart axis labels use the string directly. Footer note on analytics: "Days are UTC. Counts may undercount during outages."
- Accessibility basics: labels on inputs, buttons are `<button>`, focus visible, colour is never the only signal.
- Copy button for short URL uses the `short_url` field returned by the API (never rebuild it client-side).
- Screens in scope: Landing (polished), Register, Login, Dashboard, Links (search, status filter, pagination, create/edit/expire/delete, copy), Link analytics (7/30/90d), Settings (API keys, change password). Nothing else.
- This is a portfolio piece: it must look genuinely good and consistent. Build a deliberate design system (type scale, spacing, one accent colour, dark mode), a polished Landing, tasteful Recharts charts, subtle motion, good empty/loading states. Use the frontend-design skill. Restrained, not busy; no pricing page, no marketing fluff.

## Tests
- Vitest + React Testing Library. Mock the network at the fetch boundary (MSW) — this is the ONE place mocking is OK.
- Must cover: login/register forms, protected-route redirect, create-link flow, links table filters/pagination, analytics page renders chart data.
- `npm run lint && npm run typecheck && npm test && npm run build` must all pass before a phase is done.
