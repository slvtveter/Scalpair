import pathlib
ok = []

# ---- F1: OKX taker side casing (lowercase) ----
p = pathlib.Path("backend/app/feeds/okx.py")
src = p.read_text()
old = """                # OKX side = taker side: "Buy" => taker bought => buyer not maker
                await self.callbacks.on_trade(
                    sym, float(tr["ts"]), float(tr["px"]), float(tr["sz"]), tr.get("side") != "Buy"
                )"""
assert old in src, "F1"
src = src.replace(old, """                # OKX v5 sends lowercase taker side ("buy"/"sell")
                taker_buy = tr.get("side", "").lower() == "buy"
                await self.callbacks.on_trade(
                    sym, float(tr["ts"]), float(tr["px"]), float(tr["sz"]), not taker_buy
                )""")
# F3: kline backfill via REST
old = "    async def fetch_universe(self, n: int) -> list[dict[str, Any]]:"
assert old in src, "F3 anchor"
src = src.replace(old, """    async def fetch_klines(self, symbol: str, limit: int = 300) -> list[tuple]:
        \"\"\"Backfill 1m OHLCV: /api/v5/market/candles bar=1m -> [(ts,o,h,l,c,vol)].\"\"\"
        url = f"{REST_BASE}/api/v5/market/candles"
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(
                url,
                params={"instId": symbol_to_inst(symbol), "bar": "1m", "limit": min(limit, 300)},
            ) as resp:
                resp.raise_for_status()
                body = await resp.json()
        out = []
        for r in body.get("data") or []:  # newest-first: [ts, o, h, l, c, vol, ...]
            try:
                out.append((int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
            except (IndexError, ValueError, TypeError):
                continue
        out.reverse()
        return out

    async def fetch_universe(self, n: int) -> list[dict[str, Any]]:""")
# F4: books continuity via seqId
old = """    async def _on_books(self, sym: str, data: Any) -> None:
        if not isinstance(data, list) or not data:
            return
        book = self._books.setdefault(sym, {"bids": {}, "asks": {}})
        ts = 0.0
        for entry in data:"""
assert old in src, "F4 anchor"
src = src.replace(old, """    async def _on_books(self, sym: str, data: Any) -> None:
        if not isinstance(data, list) or not data:
            return
        book = self._books.setdefault(sym, {"bids": {}, "asks": {}})
        # continuity: update entries carry prevSeqId/seqId; a gap invalidates the
        # maintained book until the next snapshot (OKX sends one on resubscribe)
        if not self._resync_pending.discard if False else True:
            pass
        for entry in data:
            if isinstance(entry, dict) and entry.get("action") == "update":
                try:
                    prev_id, seq_id = int(entry.get("prevSeqId", 0)), int(entry.get("seqId", 0))
                except (TypeError, ValueError):
                    continue
                last = self._book_seq.get(sym)
                if last is not None and prev_id != last:
                    log.warning("okx books seq gap for %s (%d -> %d) — book stale until snapshot",
                                sym, last, seq_id)
                    book["bids"] = {}
                    book["asks"] = {}
                    self._book_seq.pop(sym, None)
                    if self._ws is not None:
                        with contextlib.suppress(Exception):
                            await self._ws.send(json.dumps(
                                {"op": "subscribe", "args": [{"channel": "books", "instId": symbol_to_inst(sym)}]}))
                    return  # drop the corrupted pass; snapshot will rebuild
                self._book_seq[sym] = seq_id
        ts = 0.0
        for entry in data:""")
src = src.replace(
    "    def __init__(self, *args: Any, **kwargs: Any) -> None:\n        super().__init__(*args, **kwargs)\n        self._books: dict[str, dict[str, dict[float, float]]] = {}",
    "    def __init__(self, *args: Any, **kwargs: Any) -> None:\n        super().__init__(*args, **kwargs)\n        self._books: dict[str, dict[str, dict[float, float]]] = {}\n        self._book_seq: dict[str, int] = {}",
)
if "import contextlib" not in src:
    src = src.replace("import logging", "import contextlib\nimport json\nimport logging")
p.write_text(src)
ok.append("F1/F3/F4 okx")

# ---- F2: config Literal + okx in DATA_FEED docs ----
p = pathlib.Path("backend/app/config.py")
src = p.read_text()
old = 'data_feed: Literal["auto", "binance", "bybit", "mock"] = "auto"'
assert old in src, "F2"
src = src.replace(old, 'data_feed: Literal["auto", "binance", "bybit", "okx", "mock"] = "auto"')
p.write_text(src)
ok.append("F2 literal")

# ---- F5: pong frames silently skipped in base reader ----
p = pathlib.Path("backend/app/feeds/base.py")
src = p.read_text()
old = """                msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                await self.handle_message(msg)"""
assert old in src, "F5"
src = src.replace(old, """                if isinstance(raw, (str, bytes)) and str(raw).strip().lower() in ("pong", "ping"):
                    continue  # venue keepalive reply (OKX text pong)
                msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                await self.handle_message(msg)""")
p.write_text(src)
ok.append("F5 pong")

# ---- F9: relaunch degradation watch after recovery; drop misplaced log ----
p = pathlib.Path("backend/app/services/streamer.py")
src = p.read_text()
old = """                self._seeded_symbols.clear()
                self._start_backfill()
                self._tasks.append(asyncio.create_task(self._live_recovery_loop(), name="live-recovery-recovered"))
                return"""
assert old in src, "F9 recovery anchor"
src = src.replace(old, """                self._seeded_symbols.clear()
                self._start_backfill()
                self._tasks.append(asyncio.create_task(self._degradation_watch(), name="degradation-watch-recovered"))
                self._tasks.append(asyncio.create_task(self._live_recovery_loop(), name="live-recovery-recovered"))
                return""")
old = '            self._tasks.append(asyncio.create_task(candidate.run(), name=f"{candidate.name}-run"))\n            log.info("streamer started: feed=%s symbols=%d", self.feed.name, len(self.top_symbols))'
if old in src:
    src = src.replace(old, '            self._tasks.append(asyncio.create_task(candidate.run(), name=f"{candidate.name}-run"))')
p.write_text(src)
ok.append("F9 recovery watch")

# ---- frontend: burger i18n, dead code purge, spread column, dead rows, min-width ----
p = pathlib.Path("frontend/js/app.js")
src = p.read_text()

# i18n keys for burger chrome
anchor = '    close: "Закрыть", soundOn: "Звук вкл", soundOff: "Звук выкл",\n    thresholdNeeded: "укажите порог", logout: "Выйти",'
assert anchor in src, "ru keys"
src = src.replace(anchor, anchor + '\n    alertsBtn: "Алерты", feed: "Фид", soundLabel: "Звук (скор ≥ 85)", langLabel: "Язык / Language",\n    minScoreLabel: "Мин. скоринг", minWallLabel: "Мин. стена", tzLabel: "Часовой пояс", reconnecting: "переподключение", connecting: "подключение", symbolsCount: "симв.",')
anchor = '    close: "Close", soundOn: "Sound on", soundOff: "Sound off",\n    thresholdNeeded: "set a threshold", logout: "Log out",'
assert anchor in src, "en keys"
src = src.replace(anchor, anchor + '\n    alertsBtn: "Alerts", feed: "Feed", soundLabel: "Sound (score ≥ 85)", langLabel: "Language",\n    minScoreLabel: "Min score", minWallLabel: "Min wall", tzLabel: "Timezone", reconnecting: "reconnecting", connecting: "connecting", symbolsCount: "symbols",')

# feed status strings localized
src = src.replace('b.textContent = `· переподключение`; b.style.color = "var(--warn)";', 'b.textContent = `· ${t("reconnecting")}`; b.style.color = "var(--warn)";')
src = src.replace('b.textContent = "· подключение"; b.style.color = "var(--text-3)";', 'b.textContent = `· ${t("connecting")}`; b.style.color = "var(--text-3)";')
src = src.replace('$("set-feed").textContent = S.feed !== "—" ? S.feed : "—";',
                  '$("set-feed").textContent = S.feed !== "—" ? `${S.feed} · ${S.stats.tracked ?? 0} ${t("symbolsCount")}` : "—";')

# dead watch-only + S.hidden purge
src = src.replace('  if ($("watch-only")?.checked) rows = rows.filter((r) => S.watch.has(r.symbol));\n', '')
src = src.replace('  let rows = S.symbols.filter((r) => !S.hidden.has(r.symbol));',
                  '  let rows = S.symbols.filter((r) => r.vol5m > 0 || r.tps > 0.1); // skip dead listings')
src = src.replace('  hidden: new Set(LS.get("hidden", [])),\n', '')

# spread column (scalper request): add Спред after Стакан
old = 'const TH_KEYS = ["symbol", "price", null, "change5m", "surge", null, "level", "tag"];'
assert old in src, "TH_KEYS"
src = src.replace(old, 'const TH_KEYS = ["symbol", "price", null, "change5m", "surge", null, "spread", "level", "tag"];')
old = """      `<td class="col-imb"><span class="imb-val mono">${imb >= 0 ? "+" : ""}${imb.toFixed(2)}</span><div class="imb-meter"><div class="imb-fill ${imb >= 0 ? "bid" : "ask"}" style="width:${imbW}%"></div></div></td>`,
      `<td class="mono ${r.levelDist === 0 ? "up" : "dim"}">${levelTxt}</td>`,"""
assert old in src, "spread cells"
src = src.replace(old, """      `<td class="col-imb"><span class="imb-val mono">${imb >= 0 ? "+" : ""}${imb.toFixed(2)}</span><div class="imb-meter"><div class="imb-fill ${imb >= 0 ? "bid" : "ask"}" style="width:${imbW}%"></div></div></td>`,
      `<td class="mono ${r.spreadBps > 15 ? "down" : "dim"}">${r.spreadBps != null ? r.spreadBps.toFixed(1) : "—"}</td>`,
      `<td class="mono ${r.levelDist === 0 ? "up" : "dim"}">${levelTxt}</td>`,""")

# applyI18n: burger labels + bell + spread header
old = """  $("h2-scanner").textContent = t("scanner");
  $("h2-board").textContent = t("board");
  $("h2-topsetups").textContent = t("topsetups");"""
assert old in src, "i18n headings"
src = src.replace(old, """  $("h2-scanner").textContent = t("scanner");
  $("h2-board").textContent = t("board");
  $("h2-topsetups").textContent = t("topsetups");
  $("bell").childNodes.forEach((n) => { if (n.nodeType === 3) n.textContent = t("alertsBtn"); });
  const setMap = { "set-feed-label": "feed", "set-sound-label": "soundLabel", "set-lang-label": "langLabel",
                   "set-score-label": "minScoreLabel", "set-wall-label": "minWallLabel", "set-tz-label": "tzLabel" };
  for (const [id, key] of Object.entries(setMap)) {
    const el = $(id);
    if (el) el.textContent = t(key);
  }""")
old = 'const names = ["ticker", "price", "trend", "ch5m", "surge", "imb", "level", "setup"];'
assert old in src, "names"
src = src.replace(old, 'const names = ["ticker", "price", "trend", "ch5m", "surge", "imb", "spread", "level", "setup"];')

p.write_text(src)
ok.append("frontend burger/spread/dead")

# index.html: burger labels with ids, spread header, remove feed-hint dead el
p = pathlib.Path("frontend/index.html")
src = p.read_text()
src = src.replace('<span>Фид</span>', '<span id="set-feed-label">Фид</span>')
src = src.replace('<span>Звук (скор ≥ 85)</span>', '<span id="set-sound-label">Звук (скор ≥ 85)</span>')
src = src.replace('<span>Язык / Language</span>', '<span id="set-lang-label">Язык / Language</span>')
src = src.replace('<span>Мин. скоринг</span>', '<span id="set-score-label">Мин. скоринг</span>')
src = src.replace('<span>Мин. стена</span>', '<span id="set-wall-label">Мин. стена</span>')
src = src.replace('<span>Часовой пояс</span>', '<span id="set-tz-label">Часовой пояс</span>')
src = src.replace('<span class="panel-hint mono" id="feed-hint">—</span>', '<span class="panel-hint mono" id="feed-hint">Binance · Bybit · OKX</span>')
src = src.replace(
    '''              <th class="col-imb">Стакан</th>
              <th class="num">Уровень</th>''',
    '''              <th class="col-imb">Стакан</th>
              <th class="num">Спред</th>
              <th class="num">Уровень</th>''')
p.write_text(src)
ok.append("index labels")

# css: drop min-width, purge dead blocks
p = pathlib.Path("frontend/css/styles.css")
src = p.read_text()
src = src.replace(".term-table {\n  width: 100%;\n  min-width: 1020px;", ".term-table {\n  width: 100%;\n  min-width: 640px;")
src = src.replace("#view-toggle.active { color: var(--accent); border-color: var(--accent); }\n", "")
p.write_text(src)
ok.append("css minwidth")
print("APPLIED:", ", ".join(ok))
