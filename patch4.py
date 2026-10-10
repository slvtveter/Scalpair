import pathlib

ok = []

# ---------- backend/app/api/routes.py: M1 rename ----------
p = pathlib.Path("backend/app/api/routes.py")
src = p.read_text()
old = '@router.get("/alerts")\nasync def recent_alerts'
assert old in src, "M1 anchor missing"
src = src.replace(old, '@router.get("/screeners/score-alerts")\nasync def recent_alerts')
p.write_text(src)
ok.append("M1 rename")

# ---------- backend/app/alerts.py: M2 pct_move over real window ----------
p = pathlib.Path("backend/app/alerts.py")
src = p.read_text()
old = """            # pct_move over window: anchor = last_value captured each cycle
            if prev:
                moved = abs(price / prev - 1) * 100.0
                return moved >= rule.threshold, moved
            return False, None"""
assert old in src, "M2 anchor missing"
new = """            # pct_move over the configured window: anchor = price window_s ago
            sym_state = self.state.symbols.get(rule.symbol.upper())
            hist = list(sym_state.price_history) if sym_state else []
            target_ms = time.time() * 1000 - rule.window_s * 1000
            base = None
            for ts, p in hist:
                if ts / 1000 <= target_ms:
                    base = p
                else:
                    break
            if base is None or base <= 0:
                return False, None
            moved = abs(price / base - 1) * 100.0
            return moved >= rule.threshold, moved"""
src = src.replace(old, new)
# pct_move must not consume the crossing anchor
old2 = """                    if rule.rule_type in ("price_cross_above", "price_cross_below", "pct_move") and price is not None:"""
assert old2 in src, "M2 anchor-update line missing"
src = src.replace(old2, """                    if rule.rule_type in ("price_cross_above", "price_cross_below") and price is not None:""")
p.write_text(src)
ok.append("M2 window")

# ---------- backend Dockerfile: M4 proxy headers ----------
p = pathlib.Path("backend/Dockerfile")
src = p.read_text()
old = 'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]'
assert old in src, "M4 CMD anchor missing"
src = src.replace(old, 'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*", "--log-level", "info"]')
p.write_text(src)
ok.append("M4 proxy-headers")

# ---------- alerts_api: L3 min cooldown ----------
p = pathlib.Path("backend/app/api/alerts_api.py")
src = p.read_text()
old = "cooldown_s: int = Field(default=300, ge=0, le=86400)"
assert old in src, "L3 anchor missing"
src = src.replace(old, "cooldown_s: int = Field(default=300, ge=30, le=86400)")
p.write_text(src)
ok.append("L3 cooldown>=30")

# ---------- nginx: M3 duplicate headers into header-defining locations ----------
p = pathlib.Path("frontend/nginx.conf")
src = p.read_text()
old = """    location ~* \\.(css|js|png|svg|ico)$ {
        add_header Cache-Control "no-cache, must-revalidate" always;
        try_files $uri =404;
    }"""
assert old in src, "M3 static location anchor missing"
src = src.replace(old, """    location ~* \\.(css|js|png|svg|ico)$ {
        add_header Cache-Control "no-cache, must-revalidate" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "no-referrer" always;
        try_files $uri =404;
    }""")
old2 = """    location / {
        add_header Cache-Control "no-cache, must-revalidate" always;
        try_files $uri $uri/ /index.html;
    }"""
assert old2 in src, "M3 spa location anchor missing"
src = src.replace(old2, """    location / {
        add_header Cache-Control "no-cache, must-revalidate" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "no-referrer" always;
        try_files $uri $uri/ /index.html;
    }""")
p.write_text(src)
ok.append("M3 nginx headers")

# ---------- frontend/js/app.js ----------
p = pathlib.Path("frontend/js/app.js")
src = p.read_text()

# L5: WS JWT
old = 'ws.onopen = () => { wsAttempt = 0; setBadge("live"); ws.send(JSON.stringify({ type: "hello" })); };'
assert old in src, "L5 anchor missing"
src = src.replace(old, 'ws.onopen = () => {\n    wsAttempt = 0;\n    setBadge("live");\n    ws.send(JSON.stringify({ type: "hello", token: S.jwt || undefined }));\n  };')
ok.append("L5 ws jwt")

# i18n keys present?
need = {
    '    close: "Закрыть", soundOn: "Звук вкл", soundOff: "Звук выкл",\n    thresholdNeeded: "укажите порог", logout: "Выйти",': "ru",
    '    close: "Close", soundOn: "Sound on", soundOff: "Sound off",\n    thresholdNeeded: "set a threshold", logout: "Log out",': "en",
}
if need['    close: "Закрыть", soundOn: "Звук вкл", soundOff: "Звук выкл",\n    thresholdNeeded: "укажите порог", logout: "Выйти",'] not in src:
    anchor = '    loginError: "Ошибка входа/регистрации", searchPh: "Поиск…",'
    assert anchor in src, "i18n ru anchor missing"
    src = src.replace(anchor, anchor + '\n    close: "Закрыть", soundOn: "Звук вкл", soundOff: "Звук выкл",\n    thresholdNeeded: "укажите порог", logout: "Выйти",')
    ok.append("i18n ru keys")
if '    close: "Close", soundOn: "Sound on", soundOff: "Sound off",\n    thresholdNeeded: "set a threshold", logout: "Log out",' not in src:
    anchor = '    loginError: "Login/registration failed", searchPh: "Search…",'
    assert anchor in src, "i18n en anchor missing"
    src = src.replace(anchor, anchor + '\n    close: "Close", soundOn: "Sound on", soundOff: "Sound off",\n    thresholdNeeded: "set a threshold", logout: "Log out",')
    ok.append("i18n en keys")

# speed/score disambiguation
old = 'natr: "NATR", speed: "Скор.",'
assert old in src, "speed ru anchor missing"
src = src.replace(old, 'natr: "NATR", speed: "Скорость",')
src = src.replace('level: "Уровень", score: "Скор",', 'level: "Уровень", score: "AI Скор",')
ok.append("speed/score ru")

# click handler hardcoded sound text -> i18n
old = 'e.target.textContent = S.soundOn ? "Sound on" : "Sound off";'
assert old in src, "sound click anchor missing"
src = src.replace(old, 'e.target.textContent = S.soundOn ? t("soundOn") : t("soundOff");')
old = '$("sound-toggle").textContent = S.soundOn ? "Sound on" : "Sound off";'
assert old in src, "sound boot anchor missing"
src = src.replace(old, '$("sound-toggle").textContent = S.soundOn ? t("soundOn") : t("soundOff");')
ok.append("sound i18n click/boot")

# (e) boardPages -> visibleRows
old = "function boardPages() {\n  const rows = S.symbols.filter((r) => !S.hidden.has(r.symbol) && (r.score ?? 0) >= S.minScore);"
assert old in src, "board anchor missing"
src = src.replace(old, "function boardPages() {\n  const rows = visibleRows(); // same filters as the table: search, watch-only, score, hidden")
ok.append("board filters")

# (f) stale chip clearing in board
old = """    const sym = tile.dataset.sym;
    const r = S.bySym.get(sym);
    if (r && isStale(r) && !tile.querySelector(".tile-stale")) {
      const chip = document.createElement("span");
      chip.className = "tile-stale";
      chip.textContent = t("stale");
      tile.appendChild(chip);
    }"""
assert old in src, "stale chip anchor missing"
src = src.replace(old, """    const sym = tile.dataset.sym;
    const r = S.bySym.get(sym);
    const chip = tile.querySelector(".tile-stale");
    if (r && isStale(r)) {
      if (!chip) {
        const el = document.createElement("span");
        el.className = "tile-stale";
        el.textContent = t("stale");
        tile.appendChild(el);
      }
    } else if (chip) {
      chip.remove();
    }""")
ok.append("stale chip clear")

# emoji in dynamic strings
src = src.replace('toast(`\u26a1 ${r.symbol} — ${r.tag} (${r.score.toFixed(0)})`);',
                  'toast(`${r.symbol} — ${r.tag} (${r.score.toFixed(0)})`);')
src = src.replace('toast(`\U0001F514 ${n.message}`, "warn");', 'toast(n.message, "warn");')
src = src.replace('chip.textContent = `\u26a0 ${t("stale")}`;', 'chip.textContent = t("stale");')
src = src.replace('const staleChip = isStale(r) ? ` <span class="tile-stale">\u26a0 ${t("stale")}</span>` : "";',
                  'const staleChip = isStale(r) ? ` <span class="tile-stale">${t("stale")}</span>` : "";')
ok.append("emoji cleanup")

# view toggle re-label on language switch
old = """  $("search").placeholder = t("searchPh");
  $("table-empty").textContent = S.symbols.length ? t("noMatch") : t("waiting");"""
assert old in src, "applyI18n anchor missing"
src = src.replace(old, """  $("search").placeholder = t("searchPh");
  $("table-empty").textContent = S.symbols.length ? t("noMatch") : t("waiting");
  $("view-toggle").textContent = S.view === "board" ? t("table") : t("board");""")
ok.append("view-toggle i18n")
p.write_text(src)

# ---------- levels: soften over-pruning (scalper caveat) ----------
p = pathlib.Path("backend/app/levels.py")
src = p.read_text()
old = """    kept: list[Cascade] = []
    for z in sorted(zones, key=lambda z: z.touches, reverse=True):
        if any(
            z.kind != k and z.low <= o.high and o.low <= z.high
            for k, o in ((x.kind, x) for x in kept)
            if k != z.kind
        ):
            continue
        kept.append(z)"""
assert old in src, "levels anchor missing"
new = """    kept: list[Cascade] = []
    for z in sorted(zones, key=lambda z: z.touches, reverse=True):
        conflict = False
        for k, o in ((x.kind, x) for x in kept):
            if k == z.kind:
                continue
            overlap = min(z.high, o.high) - max(z.low, o.low)
            smaller = min(z.high - z.low, o.high - o.low)
            # drop the weaker zone only when it is mostly swallowed by the opposite one
            if overlap > 0 and overlap / smaller > 0.6:
                conflict = True
                break
        if conflict:
            continue
        kept.append(z)"""
src = src.replace(old, new)
p.write_text(src)
ok.append("levels overlap 60%")

print("APPLIED:", ", ".join(ok))
