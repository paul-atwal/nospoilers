# C1 contract: API client and request lifecycle

## Scope

C1 replaces frontend transport and refresh ownership without changing the rendered card/header design. It may change `types.ts`, add a small read-API client and request-lifecycle module under `services/`, change `App.tsx` only as needed to own bootstrap/selection/view request state, update `utils/scheduleWeek.ts` for catalogue navigation, and add focused frontend tests. `Header.tsx`, `GameCard.tsx`, local logo assets, and final presentation mapping belong to C2.

## Current ownership

- `App.tsx` calls `fetchCurrentWeek`, `fetchSchedule`, and `fetchGameExcitement`, clears usable data before every request, and owns an uncancelled per-game rating queue.
- `services/espnSchedule.ts` calls ESPN directly, derives current week, odds, schedule records, and future-week record adjustments from source payloads.
- `services/excitementApi.ts` calls the legacy per-game API and represents transport/missing data as `-1`.
- `utils/scheduleWeek.ts` invents adjacent weeks from fixed counts instead of using the API catalogue (and treats 2020 like later seasons).

## Target ownership and real interactions

- `services/readApi.ts` owns typed decoding and exactly three GET paths: `fetchBootstrap`, `fetchWeekSnapshot`, and `fetchSeasonSnapshot`. `VITE_API_URL` is an origin/base prefix only: empty means same origin; a configured value has trailing slashes removed; callers append `/api/v1/...`. A configured value ending in `/api` or `/api/v1` is rejected so segments cannot be duplicated. Development uses `VITE_API_URL=http://127.0.0.1:8001`; production has no localhost fallback.
- The client accepts `AbortSignal`, sends a cached endpoint-specific `If-None-Match`, retains the last body and polling advice on `304`, and retries unconditionally if `304` arrives without a cached body.
- A small request owner in `App.tsx` (or a narrowly named hook/service used by it) owns one active request and timer per resource. A monotonically increasing generation plus `AbortController` prevents stale commits. Selection/view changes and unmount abort both requests and timers.
- Bootstrap owns `sourceCurrentWeek`, `activeSeason`, and ordered `knownWeeks`. `selectedWeek` is initialized from `sourceCurrentWeek` but remains independent afterward. Bootstrap rollover refresh updates catalogue/source-current data without moving a deliberate selection.
- Weekly view requests one exact week endpoint. Season view requests `/seasons/{activeSeason}` independently of the selected historical week. Endpoint-specific bodies and ETags never cross selections.
- A nonempty retained body follows its own `pollAfterSeconds` in every state. `null` cancels timer polling only; explicit retry and one focus/visibility return refresh remain available. A focus plus visibility event burst coalesces into one refresh.
- `429`, `503`, and network failures retry with a bounded delay, honoring valid `Retry-After` without a tight loop. Other failures require manual retry. A temporary failure preserves a usable body and exposes stale/error state; an initial failure exposes an actionable retry instead of a permanent loader.

## Invariants

1. No ESPN or per-game rating request exists in the active frontend request path.
2. Weekly server order is retained; no status or rating sort is applied.
3. Each endpoint/selection has isolated `{etag, body, pollAfterSeconds}` state.
4. Only the latest non-aborted generation may commit data or schedule its next timer.
5. `304` never clears a body or changes its polling advice; cacheless `304` causes one unconditional recovery request.
6. Transport freshness/error is separate from domain `rating.state`.
7. Navigation uses only the ordered `knownWeeks`, including 2020's five preseason and 17 regular-season weeks; catalogue ends disable navigation.
8. Page-return refresh does not restart overlapping loops or move the selected week.

## Failure cases and recovery

- Bootstrap initial failure: show an error state with manual retry; bounded automatic retry may run for retryable failures.
- Week/season refresh failure with data: keep data, mark it stale, announce a concise error, and retry only per bounded advice/manual action.
- `429`/`503`: parse delta-seconds or HTTP-date `Retry-After`, clamp to a safe bounded delay, and fall back to a bounded client delay.
- Network error: preserve any body and use bounded backoff; aborts are silent.
- Malformed success body: treat as a request error, retain prior usable body, and do not invent empty games/ratings.
- Navigation/view race: abort the old request and reject late completion by generation even if abort is ignored by a test double.

## C1 checks

- Focused client tests: base URL semantics, paths, ETag/304 retention, cacheless-304 recovery, Retry-After, body validation, and endpoint cache isolation.
- Focused lifecycle tests: bootstrap retry, catalogue navigation/ends, selection independence during rollover, polling from retained envelopes, `null` timer behavior, stale response suppression, view cancellation, and coalesced page-return refresh.
- `npm test -- --run` (or project equivalent), `npm run typecheck`, and `npm run build` after C1.
