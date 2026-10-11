# Verification record — 2026-10-11

The implementation is **not yet complete against Scalpair_TZ_RU.md**. Earlier
syntax/static-server checks did not establish functional or visual acceptance.

## Executed checks

- Current backend suite: `python -m pytest -o addopts='' -q` in `backend/` passed (115 tests; 3 dependency/test-key warnings).
- Following source-label changes: `python -m pytest -q tests/test_instruments.py tests/test_api.py` passed.
- `node --test frontend/tests/data-integrity.test.cjs`: 35 tests passed.
- Offline frontend rendered in headless Chrome at 1600×1000.
- Chrome/CDP with the backend in explicit mock mode: 9 board tiles, DEMO
  label, no horizontal overflow, Focus opens and receives candles; zero runtime
  exceptions during the scripted check. The loaded-board screenshot capture
  was not usable, so visual acceptance of the populated layout remains open.
- Dependencies installed in `/tmp/scalpair-verify-venv`; missing system `pytest`
  is not a blocker. An existing `backend/.venv` also exists but was not used.

## Data integrity corrections

- Removed automatic frontend sample symbols and random candle fallback.
- Removed fabricated market cap, BTC dominance, notification/market counts and
  static order-book walls from the UI.
- Live status requires a snapshot, not just a WebSocket opening. Backend mock
  snapshots are explicitly marked DEMO. Disconnects/old books are marked stale.
- Candle caches are cleared when the feed changes.
- Density errors/empty responses no longer retain invented levels. Density
  position is relative to each instrument's own mid; only fresh returned L2
  levels within the displayed range and minimum notional are included.
- Source labels distinguish mock/unknown sources from real exchanges.

## Outstanding acceptance gaps (non-exhaustive)

- Backend state is keyed by symbol, not instrument. Simultaneous Binance/Bybit
  Spot adapters are now available for explicit `DATA_FEED=binance_spot` and `DATA_FEED=bybit_spot` runs, with `BI-S`/`BY-S` identity. Single-source transitions now discard old
  data and reject late callbacks/REST responses. Full instrument-keyed state
  is still required for simultaneous exchange support.
- Focus now requests native exchange timeframes through 1D. Multiple Focus
  windows (default 1m + 1D) and timestamp cursor synchronization now work.
  Drawings, range synchronization and persisted drawings remain unimplemented. Board still aggregates its short one-minute history.
- Color groups and hidden symbols work locally; account persistence still
  needs implementation. Hidden symbols remain accessible through global search. Manual order currently uses symbol keys
  and must migrate to instrument keys with the backend.
- Board zoom/pan still needs implementation. Crosshair and board overlays
  are implemented; Focus overlay controls and per-chart styles remain open.
  The server mock feed only accumulates history since startup; a sparse DEMO
  board must not be confused with a full historical dataset.
- Density layout still needs collision handling, age/category controls and the
  full specification's tracking semantics; showing returned L2 is not full
  market/depth coverage.
- Localization is incomplete; decorative controls, responsive layout and focus
  accessibility need an interaction audit.
- Telegram linking, rule delivery lifecycle and all specified alert conditions
  have not been verified against section 7.
- Render deployment has not been updated or verified. No production completion
  is claimed. Performance/load and full specification acceptance remain open.

## Board interaction verification

- Manual pause retains displayed ordering and current page while values update.
- Pointer/focus/wheel interactions suppress automatic reordering for 3 seconds.
- Manual drag/drop persists ordering to localStorage, including across filters;
  new listings are appended while paused. Missing/equal metrics sort stably.
- Card headers update without rebuilding their canvas while order is unchanged.
- Board candles render even with a single received bar, with price/time axes,
  volume bars and symbol watermark. Empty history has an explicit message.
- Chrome/CDP with explicit backend DEMO: actual AUTO click, 3-second stable
  ordering check, header values, HTML5 drag/drop events, saved order and hover
  pause all passed; zero JS exceptions during the scripted check.
- Loaded screenshot at 1600×1000 inspected after chart changes. Fixed overlapping
  time labels observed with very short history; 12 Node tests pass afterward.
- These checks do not prove real-exchange connectivity or full spec completion.

## Source transition corrections

- Removed hybrid injection of Bybit trades/candles into another venue's state.
  Partial streams are reported in snapshots, health and the UI; connector
  transport recovery remains active without cross-exchange data substitution.
- Recovery fetches the complete candidate universe before switching. Empty or
  failed recovery does not replace the current source.
- Source activation clears candles/books/levels and source-specific model state;
  generation-bound callbacks and backfill results reject late old-source data.
- Backfill markers are instance-owned, concurrent backfills are serialized, and
  the nonexistent `_start_backfill` recovery call was replaced with a real task.
- Browser source epochs prevent late candle responses from repopulating caches;
  Focus history and density overlays clear on a source generation change.
- Regression tests cover late callbacks, late REST backfill, empty transitions,
  instance isolation, degraded streams and frontend source-response races.
- Alert rules still lack instrument/source binding and source-aware crossing
  anchors; this remains an acceptance gap for real multi-source operation.

## Numeric filters and saved presets

- Backend exposes `vol24h` directly from each source's quote-volume ticker;
  regression test distinguishes it from one-minute turnover.
- Top-panel range filters: 24h volume in millions of USDT, NATR(14) at 1m,
  signed 5m change, signed 60s price speed, distance to cascade.
- Explicit units, min/max validation, reset, saved local presets; unknown values
  fail active numeric filters. Removed the implicit short-window activity gate
  so recently connected symbols are not silently excluded before warmup.
- Added 24h-volume/NATR/range sorting and an ascending/descending control;
  unknown values remain last in either direction.
- 18 frontend tests pass. Targeted backend API/source suite: 23 tests pass.
- Chrome/CDP form checks pass: apply, reset, save/select preset, reject inverted
  range, persistence after reload, no horizontal overflow at 1440×960, zero
  runtime exceptions. Screenshot of the open filter panel inspected.
- Browser form checks used explicitly injected DEMO fixtures with the API
  offline; this does not establish real exchange data coverage.
- Account-synced groups, hidden symbols, order, filters and watchlists are
  implemented through authenticated `/preferences` and `/watchlists` APIs.
  Market-cap data/filter, configurable metric periods and per-market
  simultaneous feeds remain outstanding specification work.

## Latest production checks

- Render `/health`, `/ready` and `/metrics` returned HTTP 200 with active
  Bybit data; a live sample recorded 82k+ ingested messages and no active feed
  errors.
- `/instruments?q=BTC` returned `BY-F:FUTURES:BTCUSDT` with live price and
  quote volume.
- `/markets/BTCUSDT/candles?limit=100&timeframe=1m` returned 100 sorted,
  unique timestamps after candle normalization.
- Chrome checks at 1440px and 390px verified sidebar tab switching, no runtime
  exceptions and no horizontal overflow. Alerts list, pause/resume and delete
  server rules from the UI.
- Docker smoke tests covered authenticated watchlist/preferences persistence
  and alert create/PATCH/DELETE. Telegram delivery still requires a configured
  bot token and chat handshake.

## Search independent of the board

- Search results merge the server's tracked catalog and unfiltered snapshots;
  board venue/numeric filters, hidden state and pagination do not constrain them.
- Normalized BTC/BTCUSDT/BTC-USDT/BTC/USDT lookup with exact matches first.
  Catalog entries include exchange/market; late old-source responses are ignored.
- Arrow keys/Enter select results; Escape returns from Focus and restores focus.
  AUTO ordering is held while Focus is open. Hide/show actions persist locally.
- Chrome/CDP: searched a filtered-out instrument, opened Focus using Enter,
  hid/restored it, returned using Escape, verified board filters unchanged and
  search results not covered by the filter toolbar; zero runtime exceptions.
- 22 frontend tests, 17 API tests and 7 source/catalog tests passed.
- This is the **tracked server catalog**, not full exchange discovery. Full asset
  names, untracked-symbol subscription/history and simultaneous market selection
  are still required to meet all search requirements. Account sync remains open.

## Color watchlists

- Named groups with five colors: create, rename/recolor, delete, board selection.
- Existing starred coins remain in the separate Favorites list; deleting a custom
  group does not remove favorites or market data.
- Add/remove membership from Focus, colored board/list markers, global search
  independent of selected group; guest groups and selection persist locally.
- 25 frontend tests pass. Chrome/CDP verifies create, empty group board, search
  and Focus membership, color marker, rename, reload persistence and deletion;
  zero runtime exceptions. Screenshot inspected at 1440×960.
- Group membership remains symbol-based until instrument identities are added;
  server/account synchronization and per-group independent saved orders remain
  incomplete. No production deployment or full specification acceptance claimed.

## Board levels, order-book overlays and crosshair

- Live snapshots now include cascade zones with explicit 1m timeframe/touches.
- The level loop excludes the currently forming candle and requires three
  independent touches by default, matching the specified confirmation policy.
- Board draws shaded cascade zones and bid/ask notional labels for fresh returned
  walls above the configured minimum; levels/walls can be toggled independently.
- Crosshair shows cursor price/time; controls persist locally. Cached pointer
  redraws also recheck freshness, and disconnect status removes overlays.
- 27 frontend tests and 14 source/level backend tests passed. Chrome/CDP with
  deterministic DEMO candle fixtures verified rendered overlays, toggles,
  persistence, pointer crosshair and hiding old L2; screenshot inspected.
- Levels are currently 1m only; their labels remain 1m when candles use another
  timeframe. Full per-instrument tick size, configurable cascade parameters,
  density age/category controls and depth guarantees still need implementation.

## Native timeframe history

- Binance, Bybit and OKX adapters accept requested history timeframe. Daily
  mappings are Binance `1d`, Bybit `D`, OKX `1Dutc` (UTC day boundaries).
- Candle API supports 1m/3m/5m/15m/30m/1h/4h/1D, reporting timeframe/source/venue
  and limited stream history explicitly. Native higher-timeframe results are
  not written into the live one-minute state. Source changes reject old results.
- Per-streamer history cache coalesces requests, expires after 15 seconds,
  caps cached entries at 128, concurrent fetches at 4 and pending keys at 32;
  shutdown cancels its tasks. Exchange errors surface as temporary API failures.
- Focus uses the selected server timeframe without reaggregation, displays
  source/bar count/history errors, and labels higher-timeframe axes with dates.
- 28 Node tests and 23 history/API backend tests passed. Existing API fixture
  now waits for first mock candles as well as tickers, removing a startup race.
- Chrome/CDP with mocked history responses verified a 300-bar daily request,
  selection back to 5m and no runtime exceptions; screenshot inspected.
- Real external exchange connectivity remains unverified.


## Multi-window Focus

- Focus opens with independent 1m + 1D panes; saved 1/2/4 layout and cursor-sync
  preference. Each pane has independent timeframe, data, pan and zoom state.
- Crosshair synchronization maps candle timestamps to the destination timeframe,
  not screen positions. Invisible timestamps do not invent destination candles.
- Latest-request guards prevent overlapping history requests from rolling a pane
  back to an older response. Source and timeframe guards remain in place.
- Chrome/CDP verified default panes, independent timeframe/zoom, cursor sync,
  switching 4/1 layouts, and zero runtime exceptions. Screenshot inspected;
  moved primary timeframe controls into its pane and aligned pane headers.
- Browser uses explicitly labeled DEMO history fixtures, not live exchange data.
- Current checks: 31 frontend tests and full 115-test backend suite pass; JS
  syntax and diff whitespace checks pass. Three backend warnings concern the
  test signing key and a dependency deprecation.
- Source selectors remain outstanding. Per-pane levels, order-book walls and volume overlays are now independently configurable and persisted locally. Focus range synchronization is now implemented and covered by frontend tests. Authenticated drawing persistence is now implemented and covered in the auth integration flow; production database migration remains to verify. Render has not been updated. Mobile Focus CSS was checked at 390px: document width stayed 390px with no horizontal overflow.


## Focus drawing prototype

- Added horizontal, trend, rectangle, Fibonacci, ruler and pencil tools with undo/clear controls. Drawings are stored as timestamp/price anchors in guest local storage and rendered independently of screen pixels, allowing the same anchors to be interpreted on other timeframes.
- Chrome/CDP with deterministic DEMO candles verified horizontal, Fibonacci and pencil gestures persist as drawing models; zero runtime exceptions. Account persistence API is implemented; end-to-end account-flow acceptance and cross-timeframe drawing synchronization remain outstanding.

- Focus range sync maps the origin pane's left timestamp into each visible pane when enabled; it remains off by default and leaves panes independent otherwise.

## Market-cap enrichment (local verification)

- Added an isolated CoinGecko market-cap refresher with a ten-minute cache,
  explicit provider and update timestamp, and best-effort failure handling.
- Snapshot, `/markets/symbols` and `/instruments` expose `asset_id`,
  `market_cap_usd`, source and update time. The frontend can filter/sort by
  market cap and keeps `N/A` values at the end.
- Docker smoke run with the mock feed returned a live BTC market cap with
  `marketCapSource=coingecko`; Python compilation, `git diff --check` and all
  35 frontend tests passed.
- After Render deployment `b5c113b`, `/api/v1/instruments?q=BTC` returned
  `market_cap_usd`, `market_cap_source=coingecko` and
  `market_cap_updated_at` with HTTP 200; `/api/v1/health` was HTTP 200 with
  an active Bybit feed.

## Server-side DND for alerts (local verification)

- Added authenticated `dndUntil` workspace preference and a settings control
  for 1 hour, 8 hours or 24 hours.
- Alert evaluation preserves the `AlertEvent` audit row while recording an
  explicit `in_app:muted` delivery and suppressing Telegram sends during DND.
- Docker auth smoke test persisted and returned `dndUntil` through
  `GET/PUT /api/v1/preferences`; frontend syntax/tests and whitespace checks
  pass.
- After Render deployment `1ca4f07`, backend health returned HTTP 200 with an
  active Bybit feed and the published `/en` document contains the DND control
  (`dnd-select`).

## Focus instrument identity guard

- Focus now sends the snapshot `instrument_id` with every candle request and
  shows venue/market in the Focus title. The backend rejects a stale source
  identity with HTTP 409 instead of silently mixing history across venues.
- Added a regression test for the mismatch path; Python/JavaScript syntax,
  frontend tests and diff checks pass. Docker smoke returned HTTP 200 for the
  matching `DEMO:FUTURES:BTCUSDT` and HTTP 409 for a stale `BY-F` identity.
- After Render deployment `de9e638`, the live candle endpoint returned HTTP
  200 with `instrument_id=BY-F:FUTURES:BTCUSDT`, `venue=BY-F` and
  `market=FUTURES`; a mismatched source is guarded by the same 409 contract.
- Focus now exposes a source selector and can request Binance, Bybit or OKX
  history explicitly. Alternate-source responses intentionally omit active
  venue walls/levels so overlays are never mixed with another exchange.
  Backend regression coverage includes the alternate-source path. After
  Render deployment `9f7b98d`, `/en` contains `focus-source`; the live Bybit
  source remains healthy. A Binance history request is surfaced as HTTP 503
  when that upstream is unavailable, rather than returning fabricated bars.

## L2 density coverage metadata

- Wall records now expose the calculated per-instrument BRP, Small/Medium/Large
  category and continuous-observation age. Density responses include received
  bid/ask level counts and `complete_percent_range=false` with an explicit
  top-N coverage note, so limited books are not presented as full depth.
- Analytics regression coverage verifies BRP/category and level counts; the
  existing stale-book behavior still clears walls on disconnect.
- After Render deployment `019b965`, `/api/v1/screeners/densities?limit=2`
  returned `BY-F` walls with `category=large`, a concrete `brp_usd`, 25/25
  received levels and `complete_percent_range=false` over HTTP 200.

## Native Screener timeframes

- Board timeframe control now supports 1m, 3m, 5m, 15m, 30m, 1h, 4h and 1D.
  Each card requests the selected native exchange timeframe with its current
  instrument identity; it no longer re-aggregates a short 1m cache for the
  board. Unknown or unavailable history remains explicitly empty/stale.
- All 35 frontend tests, JavaScript/Python syntax checks and whitespace checks
  pass. After Render deployment `5eafa6f`, the 4h BTC endpoint returned
  `timeframe=4h`, `historySource=exchange_rest`, `limitedHistory=false` and
  native bars from Bybit; the published `/en` markup contains the 1D option.

## Timeframe cache isolation

- Native board history caches are now keyed by symbol, timeframe and
  instrument. A failed 4h/1D request cannot reuse a 1m series; it remains
  empty/stale until the requested source responds.
- Regression suite now has 36 passing frontend tests. After Render deployment
  `bf07fc3`, `/api/v1/health` recovered to HTTP 200 with active Bybit data;
  the first check observed the documented transient free-tier 502 during
  service wake-up.

## Focus overlay provenance

- Focus history responses now include `overlaysAvailable` and
  `overlaysSource`. A selected alternate exchange explicitly reports that
  its L2 overlays are unavailable, and the pane status shows this to the
  trader instead of implying that active-feed walls belong to that history.
- Added API regression coverage; syntax, 36 frontend tests and whitespace
  checks pass. After Render deployment `7defd63`, the live Bybit candle
  response returned `overlaysAvailable=true` and
  `overlaysSource=active_live_feed` over HTTP 200. Alternate-source 503s
  remain explicit when an upstream cannot serve the requested history.

## Instrument-bound alert audit

- `AlertEvent` now stores the resolved `instrument_id` at trigger time, and
  `/notifications` returns it beside the ticker and delivery statuses.
- Existing SQLite databases receive a nullable column and index during startup;
  old events remain readable. The alert engine keeps the source binding for
  legacy symbol-only rules by resolving the active venue at trigger time.
- Python/JavaScript syntax, 36 frontend tests and whitespace checks pass;
  Docker startup smoke verifies the schema migration path.
- After Render deployment `55ef45b`, backend health returned HTTP 200 with an
  active Bybit feed and the service started cleanly against its persistent
  database. A full authenticated notification event remains dependent on a
  real user-triggered alert.

## Instrument-bound Focus drawings

- Authenticated drawings now accept an optional `instrument_id`; local and
  server keys include it, so BTC drawings on Binance and Bybit remain separate.
- Legacy symbol-only drawing rows continue to work when the query is omitted;
  existing SQLite databases receive a nullable column and index at startup.
- API regression coverage verifies two drawing sets for one ticker stay
  isolated. Docker startup, syntax checks, 36 frontend tests and whitespace
  checks pass. After Render deployment `4efd5fe`, backend health returned
  HTTP 200 with active Bybit data and the published `/en` includes the Focus
  source selector; database migration completed during startup.

## Density map visual metadata

- The sidebar now shows a compact Small/Medium/Large legend and live L2
  coverage text (`top N/side`, limited depth) sourced from the API response.
  Density pills retain their category styling, matching the reference map's
  visual hierarchy without implying unsupported full-book coverage.
- Frontend syntax, 36 tests and whitespace checks pass; production deployment
  pass. After Render deployment `1dd5ab4`, `/en` contains the coverage and
  category legend, while the live density API reports 25 received levels per
  side and `complete_percent_range=false`.

## Alternate-source history regression fix

- Direct history calls now accept plain `instrument_id` values as well as
  FastAPI query parsing, and alternate exchange tuple candles are normalized
  through the same canonical path as cached history.
- The focused backend suite (`test_api`, `test_alerts`, `test_analytics`,
  `test_history`) passes 56 tests; frontend suite passes 36 tests. The fix
  prevents source selection from failing only outside the HTTP adapter.
- After Render deployment `01ec24c`, health returned HTTP 200 and the live
  Bybit 4h endpoint returned `historySource=exchange_rest` with
  `overlaysAvailable=true`.

- Focus overlay controls were verified by frontend tests: levels, walls and volume can be toggled independently per pane.


## Server-side alert conditions

- Added authenticated alert rule types `cascade_distance` (nearest confirmed cascade in percent) and `density_appeared` (largest observed wall notional in USDT). They are evaluated by the background engine and delivered through the existing in-app/Telegram delivery lifecycle.
- Added deterministic engine tests for both conditions.
- Exchange-specific instrument binding and density age/category lifecycle semantics remain outstanding. Edge-triggered re-arm is covered for the new conditions.


## Density continuity

- Wall detection now preserves continuous observation age for an unchanged side/price level, exposes `age_s` through density and candle payloads, and resets age when the book disappears or reconnects.
- Regression coverage verifies age accumulation and reset after an empty book.


## Instrument identity metadata

- Snapshot, symbol catalog and candle payloads now include stable `instrument_id` values (`VENUE:MARKET:SYMBOL`) so consumers can distinguish source identity without relying on a ticker string.
- The current streamer still runs one active market source and needs true simultaneous Spot/Futures state before claiming multi-market coverage.


## Instrument-bound alerts

- Alert rules accept an optional `instrument_id`; the engine rejects a rule when its venue/market identity does not match the active source. Legacy symbol-only rules remain supported.
- Regression coverage verifies matching DEMO identity, wrong-source rejection and legacy compatibility.
- True simultaneous source state is still required before multiple venues can be active at once.

- Focus alert creation forwards the snapshot `instrument_id` when available, with the existing symbol-only fallback for legacy/guest data.


## Database compatibility

- Startup now checks existing SQLite `alert_rules` schemas and adds the nullable `instrument_id` column/index when upgrading a persistent Render database; fresh databases continue using SQLAlchemy metadata creation.

- Alert selector localization now retains the cascade-distance and density rule types in both RU and EN instead of dropping them during language refresh.

- Focus now exposes a fullscreen control with browser Fullscreen API and CSS fallback; closing Focus exits fullscreen cleanly.

- Focus drawing shortcuts are available: H/T/R/F/M/P/V select tools and Ctrl/Cmd+Z invokes undo; inputs and selectors are excluded from shortcut handling.


## Render route check

- Read-only check on 2026-10-10: the live backend health endpoint returned 200 and reported Bybit active; the live frontend root returned 200, while `/en` returned 404. Added Render Blueprint rewrites for `/en` and `/en/*`; this requires a Render redeploy to verify live.


## Live Render deployment

- Pushed commits `cb002df`, `c49f78e`, `a1744f2` and `9f6a59b` to `origin/main`.
- After the final static entry fallback, live checks returned HTTP 200 for both `https://scalpair-frontend.onrender.com/` and `/en`; backend health returned HTTP 200.
- The live backend currently reports Bybit active and zero ingested messages at the instant of the check, so market freshness still requires a later runtime check after cold-start warmup.
## Focus preference persistence

- Authenticated preferences now round-trip Focus layout (1/2/4 panes),
  selected venue, cursor/range synchronization, and per-pane overlay toggles.
- The browser writes these values when changed and restores them after login;
  the server allowlist drops unknown preference keys.
- API auth round-trip coverage and all frontend tests pass locally.

## Spot adapter coverage

- Added Binance Spot and Bybit Spot normalized adapters with USDT universe, ticker, kline, trade and order-book streams.
- API history routing validates that `BI-S`/`BY-S` identities use `SPOT` and keeps alternate-source overlays unavailable.
- The default deployment remains one active source; simultaneous four-source state still requires the planned instrument-keyed multi-feed store.

## Telegram deep-link handshake

- Added `POST /telegram/webhook` to consume private-chat `/start <token>` updates.
- Tokens are short-lived, single-use, cleared after binding, and never expose the user id in the URL.
- Optional `TELEGRAM_WEBHOOK_SECRET` validates Telegram's secret header before parsing updates.
- API coverage verifies successful binding and replay rejection.

## Source-labelled discovery catalog

- `GET /api/v1/instruments?all_sources=true` discovers Binance Futures/Spot, Bybit Futures/Spot and OKX Futures in parallel REST requests.
- Every row carries `VENUE:MARKET:SYMBOL`; non-active discovery rows are explicitly marked `live: false`.
- Search now uses this catalog, so identical tickers from Spot and Futures remain distinct when opening Focus.

## Isolated multi-source runtime

- Added opt-in `DATA_FEED=multi`: each reachable Binance/Bybit Spot/Futures or OKX source receives an isolated `MarketState`; snapshots merge only at serialization with stable `VENUE:MARKET:SYMBOL` identities.
- Secondary rows carry their own book, candles, density and metrics state; the existing `auto` path remains single-source failover.
- Regression coverage verifies that identical BTCUSDT rows retain `BY-F:FUTURES` and `BI-S:SPOT` identities.
- Multi mode is intentionally opt-in because it multiplies upstream WebSocket and REST backfill budgets.

## Latest production verification (2026-10-11)

- Commit `3a6ba40` is present on `main`; backend and frontend test suites pass (backend full suite, frontend 37 tests).
- Render backend health returned HTTP 200 with `status=ok`, Bybit WebSocket `connects=1`, zero errors and continuously increasing message count.
- `GET /api/v1/screeners/overview` returned HTTP 200 after repeated requests without restarting the worker. The payload is pre-encoded on the metrics tick to keep serialization off the request path.
- Frontend `/en` returned HTTP 200.
- The multi-source health counters now aggregate all source symbol lists (`8184d19`); the regression suite covers this contract.
- API smoke checks returned HTTP 200 for overview, source-labelled instrument discovery, candles, and density coverage endpoints with the live Bybit feed.
- Commit `924650f` keeps one price sample per second under high trade throughput and normalizes speed by the selected window; targeted and full backend tests pass.
- Commit `af05024` prevents secondary-market score alerts from borrowing the primary source's ticker-keyed score; the integration test confirms one source-bound event and exactly one in-app plus one Telegram delivery row.
- Final audit on 2026-10-11: 137 backend tests and 37 frontend tests pass; Render health, overview and frontend `/en` each returned HTTP 200 with a live Bybit stream.
- Local ASGI load test with `DATA_FEED=mock`, 20 REST requests/sec for 10 seconds and 16 concurrent WebSocket clients: 196 REST requests, 0 errors, p95 6.3 ms, all 16 clients delivered snapshots. This validates the 16-client target on the test machine only; live production scale remains open.
- The mock harness now scales deterministically to 200 instruments (`a107733`). Repeating the same test at `TOP_SYMBOLS=200` delivered 195 REST requests with 0 errors (p95 17.6 ms), all 16 WebSocket clients delivered snapshots, and `/health` reported 200 tracked symbols. Browser 16-chart lifecycle evidence is recorded below; live production profiling remains open.
- Commit `9c927d0` adds a short WebSocket preflight to `DATA_FEED=auto`, so REST-only probes cannot select a source whose streaming handshake is blocked.
- Commit `8d08c7d` extends that protection to stale explicit dashboard feed settings: the configured venue is tried first, then other live adapters are probed before mock fallback. Current Render health reports `configured_feed=bybit`, `status=ok`, one live WS connection and zero errors.
- Final rollout audit after `f881f0d`: 138 backend tests and 37 frontend tests pass; Render reports `configured_feed=bybit`, `status=ok`, `connects=1`, `errors=0`, and overview/frontend both return HTTP 200.

## Browser scale and Focus lifecycle acceptance

- `scripts/check_focus_browser.py` was run against the current frontend in
  isolated headless Chrome with deterministic synthetic responses.
- The check loaded 200 instruments, rendered a 4×4 board (16 canvases), opened
  Focus with 300 candles in the active 1m pane and completed 100 open/close
  round trips.
- p95 round-trip time was 17.4 ms. DOM nodes changed from 10,645 to 10,677;
  JavaScript event listeners stayed at 118; heap usage rose from 2.07 MB to
  2.18 MB after garbage collection; one fixture socket remained active and no
  runtime exceptions occurred.
- This closes the browser-scale lifecycle gap for the synthetic fixture. It does
  not prove equivalent performance with live exchange payloads or Render's
  free-tier resource limits.

## Source-specific favorites, groups and hidden instruments

- Favorites, group membership, color markers and hidden state now use instrument
  identity in board, ticker list, search and Focus. Spot/Futures sharing a ticker
  can be managed independently.
- Watchlist API stores instrument identity separately from the legacy symbol,
  validates venue/market combinations and preserves symbol-only records.
- Legacy bare ticker membership retains its all-source interpretation until
  edited; editing expands it to currently known catalog/snapshot instruments.
- 138 backend and 38 frontend tests pass, including API identity round trips,
  invalid-input preservation and independent Spot/Futures workspace state.
- An existing SQLite watchlist was upgraded twice without losing its stored
  symbol. Production rollout of this change remains to be verified.

## Focus source switching

- Focus source changes now replace the complete instrument identity
  (`VENUE:MARKET:SYMBOL`) before requesting history, clear old-source candles,
  walls, levels and drawing state, and invalidate in-flight pane requests.
- A delayed server drawing response is ignored after switching instruments;
  source-specific alerts and preference keys continue using the selected
  identity.
- Two regression tests cover selected-source request parameters, overlay
  clearing and stale drawing responses. All 40 frontend tests pass.
- Commit `1a6cd58` is pushed to `main`; Render backend health after the push
  remains HTTP 200 with an active Bybit feed and 124,891 ingested messages at
  verification time. Frontend rollout still needs its static deploy check.

## PostgreSQL runtime compatibility

- `DATABASE_URL` now accepts both `postgres://` and `postgresql://` platform
  URLs and normalizes them to `postgresql+asyncpg://` for SQLAlchemy's async
  engine. SQLite remains the local default with its existing WAL migration
  path.
- Added the `asyncpg` runtime dependency and a regression test for both
  PostgreSQL URL forms. Full suite: 139 backend tests and 40 frontend tests.
- This makes the service compatible with an externally provisioned PostgreSQL
  database; the current Render service still uses its configured SQLite
  default until a database resource is attached.
- After commit `18cb9c6`, Render briefly returned 502 while rebuilding, then
  recovered to HTTP 200 with a fresh Bybit process, 3,639+ ingested messages,
  one connection and zero feed errors. The transient restart is consistent
  with the current free-tier deployment behavior.

## Alert source visibility

- The in-app alert rule list now displays the bound instrument identity for
  source-specific rules (`VENUE:MARKET:SYMBOL`) and explicitly labels legacy
  symbol-only rules as applying to all sources. This prevents a BTC Spot rule
  from being mistaken for a BTC Futures rule while editing the rule list.
- JavaScript syntax and all 40 frontend tests pass.

## Source-aware Telegram delivery

- Telegram alert text now includes the resolved instrument identity, for
  example `[BI-S:SPOT:BTCUSDT]`, so browser-closed notifications distinguish
  duplicate Spot and Futures tickers.
- The integration test now drives a source-bound crossing through evaluation,
  verifies one durable event and both delivery rows, then runs the Telegram
  delivery path with a deterministic sender and verifies both rows become
  `sent`.
- Alert test module: 9 passed; API test module: 20 passed.

## Multi-source runtime smoke test and diagnostics

- A local ASGI run with `DATA_FEED=multi TOP_SYMBOLS=5` started all five
  adapters; connection logs confirm five WebSocket handshakes. The symbols API
  returned 25 instruments with BI-F, BI-S, BY-F, BY-S and OK-F identities.
- The old health response reported only primary-source ingestion and errors;
  that evidence does not establish freshness or zero errors for all sources.
- Health now exposes per-source connection/error counters and ingest age,
  aggregates ingestion counts and reports degraded when a secondary source
  has no connection after warmup, no ingestion or stale messages.
- Regression test covers healthy primary plus disconnected secondary, then
  secondary recovery; all 140 backend tests pass.
- Production remains on its current feed configuration. Multi-source Render
  load and per-source live freshness still require validation before changing
  the deployment default.

## Multi-source scale smoke test (`TOP_SYMBOLS=10`)

- Local ASGI run with the Render baseline (`DATA_FEED=multi`, ten instruments
  per source) activated Binance Futures, Bybit Futures, Binance Spot, Bybit
  Spot and OKX Futures simultaneously.
- Health reported 50 source-specific instruments, all five sources `ok`, one
  connection per source and zero feed errors. Ingest counts were non-zero for
  every source (3,788 / 18,280 / 1,556 / 6,787 / 10,843 at the sample).
- Warm overview responses were 58 KB and completed in 0.5–0.7 ms locally;
  this is a local baseline, not a Render free-tier SLO guarantee.
- `render.yaml` now defaults to this multi-source baseline. The existing
  Render dashboard environment must be synchronized if it still overrides
  `DATA_FEED=bybit`.

## Multi-source cascade calculation

- Multi startup now launches the level engine, previously absent from its task
  list. The shared loop calculates confirmed pivots/cascades using each
  source's own tracked symbols and closed 1m candles, isolating failures per
  source/instrument.
- Regression test uses different BTC price histories on Bybit Futures and
  Binance Spot, checks independent confirmed zones and level-distance metrics,
  then verifies both source-labelled snapshot rows contain their own levels.
- All 141 backend tests pass. This verifies local calculation and serialization;
  Render's multi-source rollout and live scale acceptance remain outstanding.

## Liquidity-map depth and instrument navigation

- Liquidity Map now offers selectable 1%, 3%, 5% and 10% distance windows,
  persists the choice in workspace preferences and scales wall placement to
  the selected window.
- Density responses carry `instrument_id`; clicking a Spot/Futures wall opens
  Focus for that exact source instead of resolving the ticker through the
  active market.
- The UI retains the explicit limited-depth coverage label, so a top-N book is
  not presented as complete 10% market coverage. Backend and frontend suites
  pass: 142 and 42 tests respectively.

## Multi-source market-cap enrichment

- The market-cap refresher now receives the union of symbols from every active
  source in multi mode, deduplicated by ticker before the CoinGecko request.
  A Spot-only or Futures-only listing no longer depends on the primary source
  universe to receive asset-level capitalization.
- Regression coverage verifies deterministic union ordering and de-duplication;
  the backend suite now has 142 passing tests.

## Frontend source-isolated price history

- Snapshot price history is now keyed by complete `instrument_id`, preventing
  duplicate Spot/Futures tickers from sharing or overwriting one another's
  one-second chart history.
- Regression coverage feeds two BTCUSDT rows with different identities and
  verifies separate histories. Frontend suite: 42 passing tests.

## WebSocket source freshness indicator

- Live snapshots now include per-source transport and freshness diagnostics;
  the frontend marks the whole feed as incomplete when any multi-source adapter
  reports a degraded status. This keeps stale secondary markets visible while
  the user remains on the screener.
- The implementation tolerates lightweight test/demo connectors without feed
  stats. Frontend suite: 43 tests; backend suite: 142 tests.
