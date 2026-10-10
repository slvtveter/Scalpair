# Verification record — 2026-10-10

The implementation is **not yet complete against Scalpair_TZ_RU.md**. Earlier
syntax/static-server checks did not establish functional or visual acceptance.

## Executed checks

- Current backend suite: `python -m pytest -o addopts='' -q` in `backend/` passed (115 tests; 3 dependency/test-key warnings).
- Following source-label changes: `python -m pytest -q tests/test_instruments.py tests/test_api.py` passed.
- `node --test frontend/tests/data-integrity.test.cjs`: 31 tests passed.
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
  Spot/Futures are not implemented. Single-source transitions now discard old
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
- Account-synced presets, market-cap data/filter, configurable metric periods
  and per-market simultaneous feeds remain outstanding specification work.

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

- Focus overlay controls were verified by frontend tests: levels, walls and volume can be toggled independently per pane.


## Server-side alert conditions

- Added authenticated alert rule types `cascade_distance` (nearest confirmed cascade in percent) and `density_appeared` (largest observed wall notional in USDT). They are evaluated by the background engine and delivered through the existing in-app/Telegram delivery lifecycle.
- Added deterministic engine tests for both conditions.
- Exchange-specific instrument binding and density age/category lifecycle semantics remain outstanding. Edge-triggered re-arm is covered for the new conditions.


## Density continuity

- Wall detection now preserves continuous observation age for an unchanged side/price level, exposes `age_s` through density and candle payloads, and resets age when the book disappears or reconnects.
- Regression coverage verifies age accumulation and reset after an empty book.


## Instrument identity metadata

- Snapshot, symbol catalog and candle payloads now include stable `instrument_id` values (`VENUE:FUTURES:SYMBOL`) so consumers can distinguish source identity without relying on a ticker string.
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
