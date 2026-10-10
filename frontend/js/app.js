/* ============================================================
   SCALPAIR TERMINAL — vanilla JS, no build step
   WS client · scanner table · trend sparklines · chart board 3×3
   interactive candle chart (pan/zoom/TF) · hotlist · density radar
   server alerts (bell + toasts) · watchlists · search · RU/EN
   ============================================================ */
"use strict";

const $ = (id) => document.getElementById(id);

const WS_PATH = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/ws/live-feed`;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;
const STALE_BOOK_MS = 15000;

// ------------------------- i18n (RU default per spec) -------------------------
const I18N = {
  ru: {
    scanner: "Сканер", hotlist: "AI Подборка", radar: "Радар плотностей", alerts: "Алерты",
    ticker: "Тикер", price: "Цена", trend: "Тренд", ch5m: "5м %", natr: "NATR", speed: "Скорость",
    vol1m: "Объём 1м", surge: "Всплеск", imb: "Стакан", wall: "Стена", dist: "Дист.",
    fund: "Фанд.", level: "Уровень", score: "AI Скор", setup: "Сетап",
    waiting: "ожидание данных…", noMatch: "нет монет под фильтр", warmup: "движок считает…",
    noSetups: "нет сетапов ≥ {n}", noAlerts: "алертов ещё нет", notifEmpty: "уведомлений нет",
    signIn: "Войти", signUp: "Регистрация", email: "email", password: "пароль",
    createAlert: "Создать алерт", alertCreated: "Алертовое правило создано",
    authNeeded: "Войдите (колокольчик), чтобы создавать алерты",
    board: "Доска", table: "Таблица", stale: "stale",
    priceCrossAbove: "цена выше", priceCrossBelow: "цена ниже", pctMove: "движение %",
    volumeSurge: "всплеск объёма", scoreAbove: "скоринг ≥",
    loginError: "Ошибка входа/регистрации", searchPh: "Поиск…",
    close: "Закрыть", soundOn: "Звук вкл", soundOff: "Звук выкл",
    thresholdNeeded: "укажите порог", logout: "Выйти",
    resistance: "сопротивление", support: "поддержка", inside: "внутри зоны",
  },
  en: {
    scanner: "Scanner", hotlist: "AI Hotlist", radar: "Density Radar", alerts: "Alerts",
    ticker: "Ticker", price: "Price", trend: "Trend", ch5m: "5m %", natr: "NATR", speed: "Speed",
    vol1m: "1m Vol", surge: "Surge", imb: "Book Imb", wall: "Wall", dist: "Dist.",
    fund: "Fund", level: "Level", score: "Score", setup: "Setup",
    waiting: "waiting for market data…", noMatch: "no symbols match filter", warmup: "scoring engine warming up…",
    noSetups: "no setups ≥ {n}", noAlerts: "no alerts fired yet", notifEmpty: "no notifications yet",
    signIn: "Sign in", signUp: "Register", email: "email", password: "password",
    createAlert: "Create alert", alertCreated: "Alert rule created",
    authNeeded: "Sign in (bell icon) to create alerts",
    board: "Board", table: "Table", stale: "stale",
    priceCrossAbove: "price above", priceCrossBelow: "price below", pctMove: "move %",
    volumeSurge: "volume surge", scoreAbove: "score ≥",
    loginError: "Login/registration failed", searchPh: "Search…",
    close: "Close", soundOn: "Sound on", soundOff: "Sound off",
    thresholdNeeded: "set a threshold", logout: "Log out",
    resistance: "resistance", support: "support", inside: "inside zone",
  },
};
const t = (k, vars) => {
  let s = (I18N[S.lang] || I18N.ru)[k] ?? I18N.ru[k] ?? k;
  if (vars) for (const [v, val] of Object.entries(vars)) s = s.replace(`{${v}}`, val);
  return s;
};

// ------------------------- state -------------------------
const LS = {
  get(k, d) { try { const v = localStorage.getItem("scalpair." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("scalpair." + k, JSON.stringify(v)); } catch {} },
};

const S = {
  symbols: [],
  bySym: new Map(),
  picks: [],
  alerts: [],
  stats: {},
  feed: "—",
  priceHistory: new Map(),
  maxHistory: 900,
  soundOn: LS.get("sound", false),
  minScore: LS.get("minScore", 0),
  wallMin: 250000,
  sortKey: "score",
  tickCounter: 0,
  msgTimes: [],
  lastAlertAt: new Map(),
  lang: LS.get("lang", "ru"),
  view: LS.get("view", "table"),
  gridSize: LS.get("grid", 3),
  page: 0,
  search: "",
  watch: new Set(LS.get("watch", [])),
  hidden: new Set(LS.get("hidden", [])),
  jwt: LS.get("jwt", null),
  candleCache: new Map(),
  notifLastId: LS.get("notifLastId", 0),
};

// ------------------------- ws client -------------------------
let ws = null;
let wsAttempt = 0;

function setBadge(status) {
  const b = $("feed-badge");
  b.className = "badge";
  if (status === "live") { b.classList.add("badge-live"); b.textContent = `Live · ${S.feed.replace("-trades", "").toUpperCase()}`; }
  else if (status === "reconnecting") { b.classList.add("badge-reconn"); b.textContent = "Reconnecting"; }
  else { b.classList.add("badge-off"); b.textContent = "Connecting"; }
}

function connect() {
  setBadge(wsAttempt === 0 ? "connecting" : "reconnecting");
  try { ws = new WebSocket(WS_PATH); } catch { scheduleReconnect(); return; }
  ws.onopen = () => {
    wsAttempt = 0;
    setBadge("live");
    ws.send(JSON.stringify({ type: "hello", token: S.jwt || undefined }));
  };
  ws.onmessage = (ev) => { S.msgTimes.push(performance.now()); try { onSnapshot(JSON.parse(ev.data)); } catch {} };
  ws.onclose = () => scheduleReconnect();
  ws.onerror = () => { try { ws.close(); } catch {} };
}

function scheduleReconnect() {
  setBadge("reconnecting");
  const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** Math.min(wsAttempt, 4));
  wsAttempt += 1;
  setTimeout(connect, delay + Math.random() * 400);
}

function onSnapshot(snap) {
  if (snap.type !== "snapshot") return;
  S.feed = snap.feed || S.feed;
  S.symbols = snap.symbols || [];
  S.picks = snap.picks || [];
  S.alerts = snap.alerts || S.alerts;
  S.stats = snap.stats || {};
  S.tickCounter += 1;
  for (const r of S.symbols) {
    if (r.price > 0) {
      let hist = S.priceHistory.get(r.symbol);
      if (!hist) { hist = []; S.priceHistory.set(r.symbol, hist); }
      const last = hist[hist.length - 1];
      if (!last || Date.now() - last.t > 1000) {
        hist.push({ t: Date.now(), p: r.price });
        if (hist.length > S.maxHistory) hist.shift();
      }
    }
    S.bySym.set(r.symbol, r);
  }
  checkAlerts();
}

function checkAlerts() {
  if (!S.soundOn) return;
  for (const r of S.symbols) {
    if ((r.score ?? 0) < 85) continue;
    const last = S.lastAlertAt.get(r.symbol) || 0;
    if (Date.now() - last < 5 * 60 * 1000) continue;
    S.lastAlertAt.set(r.symbol, Date.now());
    ping();
    toast(`${r.symbol} — ${r.tag} (${r.score.toFixed(0)})`);
  }
}

function ping() {
  try {
    const ctx = ping.ctx || (ping.ctx = new (window.AudioContext || window.webkitAudioContext)());
    const t0 = ctx.currentTime;
    [880, 1320].forEach((f, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = f;
      gain.gain.setValueAtTime(0.0001, t0 + i * 0.15);
      gain.gain.exponentialRampToValueAtTime(0.12, t0 + i * 0.15 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + i * 0.15 + 0.22);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t0 + i * 0.15);
      osc.stop(t0 + i * 0.15 + 0.25);
    });
  } catch {}
}

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

// ------------------------- formatters -------------------------
function fmtPrice(v) {
  if (!v) return "—";
  if (v >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 1 });
  if (v >= 1) return v.toFixed(3);
  return v.toPrecision(4);
}
function fmtUsd(v) {
  if (!v) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}
function fmtPct(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}
function fmtNum(v, digits = 2) { return v === null || v === undefined ? "—" : v.toFixed(digits); }
function cls(v) { return v > 0 ? "up" : v < 0 ? "down" : "dim"; }
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function tagClass(tag) {
  if (!tag) return "tag-quiet";
  if (tag.includes("Breakout")) return "tag-breakout";
  if (tag.includes("Absorption")) return "tag-absorption";
  if (tag.includes("Distribution")) return "tag-distribution";
  if (tag.includes("Wall")) return "tag-wall";
  if (tag.includes("Volume") || tag.includes("Momentum") || tag.includes("Funding")) return "tag-volume";
  return "tag-quiet";
}
function scoreClass(score) {
  if (score == null) return "sc-ice";
  if (score >= 85) return "sc-blaze";
  if (score >= 65) return "sc-hot";
  if (score >= 40) return "sc-warm";
  return "sc-ice";
}
function isStale(r) { return r.bookTs && Date.now() - r.bookTs > STALE_BOOK_MS; }

// ------------------------- main table -------------------------
const tbody = $("table-body");
const rowMap = new Map();

const TH_KEYS = ["symbol", "price", null, "change5m", "natr", "speed", "vol1m", "surge", null, "wall", null, null, "level", "score", "tag"];

function updateSortIndicator() {
  document.querySelectorAll("#main-table thead th").forEach((th, i) => {
    const active = TH_KEYS[i] === S.sortKey;
    th.classList.toggle("th-sort-active", active);
    if (active) th.setAttribute("aria-sort", "descending");
    else th.removeAttribute("aria-sort");
    th.style.cursor = TH_KEYS[i] ? "pointer" : "default";
  });
}

function visibleRows() {
  let rows = S.symbols.filter((r) => !S.hidden.has(r.symbol));
  if (S.search) rows = rows.filter((r) => r.symbol.includes(S.search.toUpperCase()));
  if ($("watch-only")?.checked) rows = rows.filter((r) => S.watch.has(r.symbol));
  rows = rows.filter((r) => (r.score ?? 0) >= S.minScore);
  rows = [...rows].sort((a, b) => {
    switch (S.sortKey) {
      case "symbol": return a.symbol.localeCompare(b.symbol);
      case "price": return (b.price ?? 0) - (a.price ?? 0);
      case "change5m": return (b.change5m ?? 0) - (a.change5m ?? 0);
      case "vol1m": return (b.vol1m ?? 0) - (a.vol1m ?? 0);
      case "surge": return (b.surge ?? 0) - (a.surge ?? 0);
      case "natr": case "speed": case "level": {
        const key = S.sortKey === "level" ? "levelDist" : S.sortKey;
        const av = a[key], bv = b[key];
        return (av ?? Infinity) - (bv ?? Infinity);
      }
      case "wall": return (b.walls?.[0]?.notional ?? 0) - (a.walls?.[0]?.notional ?? 0);
      default: return (b.score ?? -1) - (a.score ?? -1);
    }
  });
  return rows;
}

function bestWall(r) {
  if (!r.walls || !r.walls.length) return null;
  return r.walls.find((w) => w.notional >= S.wallMin) || r.walls[0] || null;
}

function renderTable() {
  if (S.view !== "table") return;
  const rows = visibleRows();
  $("table-empty").style.display = rows.length ? "none" : "";
  $("table-empty").textContent = rows.length ? "" : (S.symbols.length ? t("noMatch") : t("waiting"));

  const seen = new Set();
  for (const r of rows) {
    seen.add(r.symbol);
    let tr = rowMap.get(r.symbol);
    if (!tr) {
      tr = document.createElement("tr");
      tr.tabIndex = 0;
      tr.setAttribute("role", "button");
      tr.setAttribute("aria-label", `SCALP view: ${r.symbol}`);
      tr.onclick = () => openChart(r.symbol);
      tr.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openChart(r.symbol); } };
      rowMap.set(r.symbol, tr);
      tbody.appendChild(tr);
    }
    tr.className = (r.score ?? 0) >= 85 ? "row-hot" : "";

    const wall = bestWall(r);
    const imb = r.imbalance ?? 0;
    const imbW = Math.min(Math.abs(imb) * 50, 50);
    const sc = scoreClass(r.score);
    const star = S.watch.has(r.symbol) ? "★" : "☆";
    const staleChip = isStale(r) ? ` <span class="tile-stale">${t("stale")}</span>` : "";
    const cells = [
      `<td class="col-sym sym-cell"><span class="star" data-star="${esc(r.symbol)}" title="watchlist">${star}</span> ${esc(r.symbol)}<small>${r.tps != null ? `${r.tps.toFixed(1)} tps` : ""}${staleChip}</small></td>`,
      `<td class="mono">${fmtPrice(r.price)}</td>`,
      `<td class="col-trend spark-cell"><canvas class="spark" width="110" height="26"></canvas></td>`,
      `<td class="mono ${cls(r.change5m)}">${fmtPct(r.change5m)}</td>`,
      `<td class="mono">${fmtNum(r.natr)}</td>`,
      `<td class="mono ${cls(r.speed)}">${fmtNum(r.speed)}</td>`,
      `<td class="mono dim">${fmtUsd(r.vol1m)}</td>`,
      `<td class="mono ${cls(r.surge - 1)}">${r.surge != null ? `${r.surge.toFixed(1)}x` : "—"}</td>`,
      `<td class="col-imb"><span class="imb-val mono">${imb >= 0 ? "+" : ""}${imb.toFixed(2)}</span><div class="imb-meter"><div class="imb-fill ${imb >= 0 ? "bid" : "ask"}" style="width:${imbW}%"></div></div></td>`,
      `<td class="mono ${wall ? (wall.side === "bid" ? "up" : "down") : "dim"}">${wall ? fmtUsd(wall.notional) : "—"}</td>`,
      `<td class="mono dim">${wall ? `${Math.abs(wall.distance).toFixed(2)}% ${wall.side === "bid" ? "↓" : "↑"}` : "—"}</td>`,
      `<td class="mono ${cls((r.funding ?? 0) * -1)}">${r.funding != null ? `${r.funding > 0 ? "+" : ""}${(r.funding * 100).toFixed(1)}bp` : "—"}</td>`,
      `<td class="mono ${r.levelDist === 0 ? "up" : "dim"}">${r.levelDist != null ? `${r.levelDist.toFixed(2)}% ${r.levelKind === "resistance" ? "↑" : r.levelKind === "support" ? "↓" : "◎"}` : "—"}</td>`,
      `<td class="score-cell"><span class="score-num ${sc}">${r.score != null ? r.score.toFixed(0) : "—"}</span><span class="score-bar"><i class="${sc}" style="width:${r.score ?? 0}%"></i></span></td>`,
      `<td class="col-tag"><span class="tag-cell ${tagClass(r.tag)}">${esc(r.tag ?? "—")}</span></td>`,
    ];
    if (tr._sig !== cells.join("|")) { tr.innerHTML = cells.join(""); tr._sig = cells.join("|"); }
  }
  for (const [sym, tr] of rowMap) {
    if (!seen.has(sym)) { tr.remove(); rowMap.delete(sym); }
  }
}

document.querySelectorAll("#main-table thead th").forEach((th, i) => {
  th.addEventListener("click", () => { if (TH_KEYS[i]) { S.sortKey = TH_KEYS[i]; renderTable(); updateSortIndicator(); } });
});
tbody.addEventListener("click", (e) => {
  const star = e.target.closest("[data-star]");
  if (!star) return;
  e.stopPropagation();
  const sym = star.dataset.star;
  if (S.watch.has(sym)) S.watch.delete(sym); else S.watch.add(sym);
  LS.set("watch", [...S.watch]);
  renderTable();
});

// ------------------------- trend sparklines -------------------------
function drawSparks() {
  for (const [sym, tr] of rowMap) {
    const cv = tr.querySelector("canvas.spark");
    if (!cv) continue;
    const pts = S.priceHistory.get(sym) || [];
    if (pts.length < 10) continue;
    const ps = pts.slice(-600).map((p) => p.p);
    const dpr = window.devicePixelRatio || 1;
    const W = 110, H = 26;
    if (cv.width !== Math.round(W * dpr)) {
      cv.width = Math.round(W * dpr);
      cv.height = Math.round(H * dpr);
      cv.style.width = W + "px";
      cv.style.height = H + "px";
    }
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    let min = Math.min(...ps), max = Math.max(...ps);
    if (max - min < min * 0.0002) max = min + min * 0.0002;
    const up = ps[ps.length - 1] >= ps[0];
    const col = up ? "#31976b" : "#c95d63";
    const x = (i) => (i / (ps.length - 1)) * (W - 4) + 2;
    const y = (p) => H - 3 - ((p - min) / (max - min)) * (H - 6);
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, up ? "rgba(49,151,107,0.22)" : "rgba(201,93,99,0.22)");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    ctx.beginPath();
    ps.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p)) : ctx.moveTo(x(0), y(p))));
    ctx.strokeStyle = col;
    ctx.lineWidth = 1.3;
    ctx.lineJoin = "round";
    ctx.stroke();
    ctx.lineTo(x(ps.length - 1), H); ctx.lineTo(x(0), H); ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x(ps.length - 1), y(ps[ps.length - 1]), 1.7, 0, Math.PI * 2);
    ctx.fillStyle = col;
    ctx.fill();
  }
}

// ------------------------- hotlist -------------------------
let _hotlistSig = "";
function renderHotlist() {
  const el = $("hotlist-body");
  const picks = S.picks.filter((p) => p.score >= S.minScore);
  $("hotlist-mode").textContent = picks.length ? `TOP ${picks.length}` : "TOP 5";
  if (!S.picks.length) { setIfChanged(el, `<div class="empty-note">${t("warmup")}</div>`); return; }
  if (!picks.length) { setIfChanged(el, `<div class="empty-note">${t("noSetups", { n: S.minScore })}</div>`); return; }
  const html = picks.map((p) => `
    <div class="pick" data-sym="${esc(p.symbol)}" tabindex="0" role="button" aria-label="SCALP view: ${esc(p.symbol)}">
      <div class="pick-top">
        <span class="pick-sym">${esc(p.symbol)}<span class="price mono">${fmtPrice(p.price)}</span></span>
        <span class="score-num ${scoreClass(p.score)}">${p.score.toFixed(0)}</span>
      </div>
      <div class="pick-meta">
        <span class="tag-cell ${tagClass(p.tag)}">${esc(p.tag)}</span>
        <span>anomaly ${p.anomaly?.toFixed(0) ?? "—"} · heuristic ${p.heuristic?.toFixed(0) ?? "—"}</span>
      </div>
      <div class="pick-thesis">${esc(p.thesis)}</div>
      <div class="pick-scorebar"><div style="width:${p.score}%"></div></div>
    </div>`).join("");
  if (html !== _hotlistSig) { _hotlistSig = html; el.innerHTML = html; }
}
function setIfChanged(el, html) {
  if (el._sig !== html) { el._sig = html; el.innerHTML = html; }
}
$("hotlist-body").addEventListener("keydown", (e) => {
  const card = e.target.closest(".pick");
  if (card && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openChart(card.dataset.sym); }
});

// ------------------------- alerts feed panel -------------------------
let _alertsSig = "";
function renderAlerts() {
  const el = $("alerts-body");
  $("alert-count").textContent = S.alerts.length;
  const html = S.alerts.length
    ? [...S.alerts].reverse().map((a) => `
      <div class="alert-item"><b>${esc(a.symbol)}</b> ${a.score?.toFixed(0)} · ${esc(a.tag)}<br/>
      <span class="dim">${new Date(a.ts).toLocaleTimeString()} — ${esc(a.thesis ?? "")}</span></div>`).join("")
    : `<div class="empty-note">${t("noAlerts")}</div>`;
  setIfChanged(el, html);
}

// ------------------------- ticker strip -------------------------
let _tickerSig = "";
function renderTicker() {
  const items = S.symbols.slice(0, 18);
  if (!items.length) return;
  const html = items.map((r) => `
    <span class="tk"><b>${esc(r.symbol.replace("USDT", ""))}</b>
    <span class="dim">${fmtPrice(r.price)}</span>
    <span class="${cls(r.change24h)}">${fmtPct(r.change24h, 1)}</span></span>`).join("");
  setIfChanged($("ticker-track"), html + html);
}

// ------------------------- density radar -------------------------
const radar = $("radar-canvas");

function fitCanvas(cv, logicalH) {
  const dpr = window.devicePixelRatio || 1;
  const cssW = Math.max(200, cv.clientWidth || cv.width / dpr);
  const bw = Math.round(cssW * dpr), bh = Math.round(logicalH * dpr);
  if (cv.width !== bw || cv.height !== bh) { cv.width = bw; cv.height = bh; }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, W: cssW, H: logicalH };
}

function drawRadar() {
  const { ctx: rctx, W, H } = fitCanvas(radar, 300);
  rctx.clearRect(0, 0, W, H);
  rctx.fillStyle = "#13161a";
  rctx.fillRect(0, 0, W, H);
  rctx.strokeStyle = "#1e232a";
  rctx.lineWidth = 1;
  rctx.font = "9px 'JetBrains Mono', monospace";
  rctx.fillStyle = "#626b77";
  const distRange = 2.0;
  for (let d = -2; d <= 2; d += 0.5) {
    const x = ((d + distRange) / (2 * distRange)) * (W - 40) + 30;
    rctx.beginPath();
    rctx.moveTo(x, 10); rctx.lineTo(x, H - 24);
    rctx.stroke();
    if (Number.isInteger(d * 2)) rctx.fillText(`${d > 0 ? "+" : ""}${d}%`, x - 10, H - 10);
  }
  for (const m of [0.25, 1, 4, 16]) {
    const y = H - 34 - (Math.log10(m * 1e6) / Math.log10(5e7)) * (H - 60);
    if (y < 10 || y > H - 24) continue;
    rctx.beginPath(); rctx.moveTo(30, y); rctx.lineTo(W - 10, y); rctx.stroke();
    rctx.fillText(`$${m}M`, 2, y + 3);
  }
  const x0 = 30 + 0.5 * (W - 40);
  rctx.strokeStyle = "#262d36";
  rctx.beginPath(); rctx.moveTo(x0, 10); rctx.lineTo(x0, H - 24); rctx.stroke();
  for (const r of S.symbols) {
    for (const w of r.walls || []) {
      if (w.notional < S.wallMin) continue;
      const dist = Math.max(-distRange, Math.min(distRange, w.distance));
      const x = ((dist + distRange) / (2 * distRange)) * (W - 40) + 30;
      const nlog = Math.max(0, Math.min(1, Math.log10(Math.max(w.notional, 1e5)) / Math.log10(5e7)));
      const y = H - 34 - nlog * (H - 60);
      const rad = 2 + 3.5 * nlog;
      rctx.beginPath();
      rctx.arc(x, y, rad, 0, Math.PI * 2);
      rctx.fillStyle = w.side === "bid" ? "#31976b" : "#c95d63";
      rctx.globalAlpha = 0.85;
      rctx.fill();
      rctx.globalAlpha = 1;
      rctx.fillStyle = w.side === "bid" ? "#31976b" : "#c95d63";
      rctx.font = "8px 'JetBrains Mono', monospace";
      rctx.fillText(r.symbol.replace("USDT", ""), x + rad + 2, y + 3);
    }
  }
}

// ------------------------- chart board (grid mode) -------------------------
async function getCandles(sym) {
  const cached = S.candleCache.get(sym);
  if (cached && Date.now() - cached.ts < 60000) return cached.candles;
  try {
    const res = await fetch(`/api/v1/markets/${encodeURIComponent(sym)}/candles?limit=300`, { cache: "no-store" });
    if (!res.ok) return cached?.candles || [];
    const body = await res.json();
    S.candleCache.set(sym, { ts: Date.now(), candles: body.candles || [] });
    return body.candles || [];
  } catch { return cached?.candles || []; }
}

function aggregate(candles, tfMin) {
  if (tfMin <= 1) return candles;
  const bucketMs = tfMin * 60_000;
  const out = [];
  for (const c of candles) {
    const b = Math.floor(c.t / bucketMs) * bucketMs;
    const last = out[out.length - 1];
    if (!last || last.t !== b) out.push({ t: b, o: c.o, h: c.h, l: c.l, c: c.c, v: c.v });
    else {
      last.h = Math.max(last.h, c.h);
      last.l = Math.min(last.l, c.l);
      last.c = c.c;
      last.v += c.v;
    }
  }
  return out;
}

const lpUp = (vis) => vis[vis.length - 1].c >= vis[0].o;

function paintMini(cv, candles, livePrice) {
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  if (!W || !H || candles.length < 3) return;
  if (cv.width !== Math.round(W * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const vis = candles.slice(-90).map((c) => ({ ...c }));
  if (livePrice && vis.length) vis[vis.length - 1].c = livePrice;
  let lo = Math.min(...vis.map((c) => c.l)), hi = Math.max(...vis.map((c) => c.h));
  const pad = (hi - lo) * 0.06 || lo * 0.001;
  lo -= pad; hi += pad;
  const cw = W / vis.length;
  const y = (p) => 6 + (1 - (p - lo) / (hi - lo)) * (H - 18);
  const bw = Math.max(1, Math.min(cw * 0.6, 10));
  for (let i = 0; i < vis.length; i++) {
    const c = vis[i];
    const col = c.c >= c.o ? "#31976b" : "#c95d63";
    ctx.strokeStyle = col;
    ctx.fillStyle = col;
    ctx.lineWidth = 1;
    const cx = i * cw + cw / 2;
    ctx.beginPath(); ctx.moveTo(cx, y(c.h)); ctx.lineTo(cx, y(c.l)); ctx.stroke();
    const yO = y(c.o), yC = y(c.c);
    ctx.fillRect(cx - bw / 2, Math.min(yO, yC), bw, Math.max(1, Math.abs(yC - yO)));
  }
  const lp = vis[vis.length - 1].c;
  ctx.setLineDash([2, 3]);
  ctx.strokeStyle = lpUp(vis) ? "#31976b" : "#c95d63";
  ctx.beginPath(); ctx.moveTo(0, y(lp)); ctx.lineTo(W, y(lp)); ctx.stroke();
  ctx.setLineDash([]);
}

function boardPages() {
  const rows = visibleRows(); // same filters as the table: search, watch-only, score, hidden
  const per = S.gridSize * S.gridSize;
  const pages = Math.max(1, Math.ceil(rows.length / per));
  S.page = Math.min(S.page, pages - 1);
  return { slice: rows.slice(S.page * per, (S.page + 1) * per), pages };
}

let _boardSig = "";
function renderBoard() {
  if (S.view !== "board") return;
  const grid = $("board-grid");
  const { slice, pages } = boardPages();
  $("page-ind").textContent = `${S.page + 1}/${pages}`;
  $("board-empty").style.display = slice.length ? "none" : "";
  grid.style.gridTemplateColumns = `repeat(${S.gridSize}, 1fr)`;
  grid.style.gridTemplateRows = `repeat(${S.gridSize}, 1fr)`;
  const sig = slice.map((r) => r.symbol).join(",");
  if (sig !== _boardSig) {
    _boardSig = sig;
    grid.innerHTML = slice.map((r) => `
      <div class="board-tile" data-sym="${esc(r.symbol)}" tabindex="0" role="button" aria-label="SCALP view: ${esc(r.symbol)}">
        <div class="tile-head">
          <span><span class="star ${S.watch.has(r.symbol) ? "on" : ""}" data-star="${esc(r.symbol)}">${S.watch.has(r.symbol) ? "★" : "☆"}</span> <b>${esc(r.symbol)}</b> <span class="tp">${fmtPrice(r.price)}</span></span>
          <span class="tc ${cls(r.change5m)}">${fmtPct(r.change5m, 1)}</span>
          <span class="score">${r.score != null ? r.score.toFixed(0) : "—"}</span>
        </div>
        <div class="tile-canvas-wrap"><canvas></canvas></div>
      </div>`).join("");
  }
  for (const tile of grid.querySelectorAll(".board-tile")) {
    const sym = tile.dataset.sym;
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
    }
    getCandles(sym).then((candles) => {
      if (!candles.length) return;
      paintMini(tile.querySelector("canvas"), aggregate(candles, 5), r?.price);
    });
  }
}

$("board-grid").addEventListener("click", (e) => {
  const star = e.target.closest("[data-star]");
  if (star) {
    const sym = star.dataset.star;
    if (S.watch.has(sym)) S.watch.delete(sym); else S.watch.add(sym);
    LS.set("watch", [...S.watch]);
    _boardSig = ""; renderBoard();
    return;
  }
  const tile = e.target.closest(".board-tile");
  if (tile) openChart(tile.dataset.sym);
});
$("board-grid").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const tile = e.target.closest(".board-tile");
  if (tile) { e.preventDefault(); openChart(tile.dataset.sym); }
});
setInterval(renderBoard, 2000);

// ------------------------- chart modal (interactive candles) -------------------------
const modal = $("chart-modal");
let chartSym = null;

const chart = {
  candles: [], series: [], tfMin: 1, walls: [], levels: [],
  offset: 0, visible: 120, hover: null,
  dragging: false, dragStartX: 0, dragStartOffset: 0, followRight: true,
};

function clampOffset(n) {
  const max = Math.max(0, chart.series.length - chart.visible);
  return Math.max(0, Math.min(max, n));
}

function rebuildSeries() {
  const m = chart.tfMin;
  if (m <= 1) { chart.series = chart.candles; return; }
  const bucketMs = m * 60_000;
  const out = [];
  for (const c of chart.candles) {
    const b = Math.floor(c.t / bucketMs) * bucketMs;
    const last = out[out.length - 1];
    if (!last || last.t !== b) out.push({ t: b, o: c.o, h: c.h, l: c.l, c: c.c, v: c.v });
    else {
      last.h = Math.max(last.h, c.h);
      last.l = Math.min(last.l, c.l);
      last.c = c.c;
      last.v += c.v;
    }
  }
  chart.series = out;
}

async function openChart(symbol) {
  chartSym = symbol;
  const r = S.bySym.get(symbol);
  const pick = S.picks.find((p) => p.symbol === symbol);
  $("chart-title").textContent = `${symbol} · ${chart.tfMin === 60 ? "1h" : chart.tfMin + "m"}`;
  $("chart-tag").textContent = pick?.tag || r?.tag || "—";
  $("chart-tag").className = `tag-cell ${tagClass(pick?.tag || r?.tag)}`;
  $("chart-thesis").textContent = pick?.thesis || r?.thesis || "—";
  $("alert-symbol").textContent = symbol;
  modal.classList.remove("hidden");
  chart.candles = []; chart.series = []; chart.levels = [];
  chart.visible = Math.min(120, 300);
  drawChart();
  await refreshChart(true);
}

async function refreshChart(resetView = false) {
  if (!chartSym || modal.classList.contains("hidden")) return;
  try {
    const res = await fetch(`/api/v1/markets/${encodeURIComponent(chartSym)}/candles?limit=300`, { cache: "no-store" });
    if (!res.ok) return;
    const body = await res.json();
    chart.candles = body.candles || [];
    rebuildSeries();
    chart.walls = body.walls || [];
    chart.levels = body.levels || [];
    chart.visible = Math.min(chart.visible, Math.max(25, chart.series.length));
    if (resetView || chart.followRight) chart.offset = clampOffset(chart.series.length - chart.visible);
    drawChart();
  } catch {}
}

function closeChart() { modal.classList.add("hidden"); chartSym = null; }
$("chart-close").addEventListener("click", closeChart);
modal.addEventListener("click", (e) => { if (e.target === modal) closeChart(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeChart(); });

{
  const cv = $("chart-canvas");
  const plotW = () => cv.clientWidth - AXIS_W;
  cv.style.cursor = "grab";

  cv.addEventListener("mousedown", (e) => {
    chart.dragging = true;
    const rect = cv.getBoundingClientRect();
    chart.dragStartX = e.clientX - rect.left;
    chart.dragStartOffset = chart.offset;
    cv.style.cursor = "grabbing";
  });
  window.addEventListener("mouseup", () => {
    chart.dragging = false;
    if (!modal.classList.contains("hidden")) cv.style.cursor = "grab";
  });
  cv.addEventListener("mousemove", (e) => {
    const rect = cv.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    if (chart.dragging) {
      const cw = plotW() / chart.visible;
      const shift = Math.round((chart.dragStartX - x) / cw);
      chart.offset = clampOffset(chart.dragStartOffset + shift);
      chart.followRight = chart.offset >= chart.series.length - chart.visible;
    }
    chart.hover = { x, y };
    drawChart();
  });
  cv.addEventListener("mouseleave", () => { chart.hover = null; chart.dragging = false; drawChart(); });
  cv.addEventListener("wheel", (e) => {
    if (modal.classList.contains("hidden") || !chart.series.length) return;
    e.preventDefault();
    const rect = cv.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const cw = plotW() / chart.visible;
    const anchor = chart.offset + Math.floor(mx / cw);
    const factor = e.deltaY > 0 ? 1.18 : 1 / 1.18;
    chart.visible = Math.round(Math.max(25, Math.min(300, chart.visible * factor)));
    const cw2 = plotW() / chart.visible;
    chart.offset = clampOffset(anchor - Math.floor(mx / cw2));
    chart.followRight = chart.offset >= chart.series.length - chart.visible;
    drawChart();
  }, { passive: false });
}
setInterval(() => { if (!modal.classList.contains("hidden") && chartSym) refreshChart(); }, 5000);

$("tf-picker").addEventListener("click", (e) => {
  const b = e.target.closest(".tf-btn");
  if (!b) return;
  chart.tfMin = +b.dataset.tf;
  document.querySelectorAll("#tf-picker .tf-btn").forEach((x) => x.classList.toggle("active", x === b));
  rebuildSeries();
  if (chartSym) $("chart-title").textContent = `${chartSym} · ${chart.tfMin === 60 ? "1h" : chart.tfMin + "m"}`;
  chart.visible = Math.min(Math.max(25, chart.visible), Math.max(25, chart.series.length));
  chart.offset = clampOffset(chart.series.length - chart.visible);
  chart.followRight = true;
  drawChart();
});

const AXIS_W = 74, TIME_H = 26, VOL_H = 56;
const UP = "#31976b", DOWN = "#c95d63";

function drawChart() {
  if (!chartSym || modal.classList.contains("hidden")) return;
  const cv = $("chart-canvas");
  const { ctx, W, H } = fitCanvas(cv, 460);
  ctx.fillStyle = "#101318";
  ctx.fillRect(0, 0, W, H);
  ctx.font = "10px 'JetBrains Mono', monospace";
  const plotW = W - AXIS_W;
  const priceTop = 10, priceH = H - TIME_H - VOL_H - priceTop - 6;

  const data = chart.series;
  if (!data.length) {
    ctx.fillStyle = "#7b8491";
    ctx.fillText("loading candles…", 20, H / 2);
    return;
  }

  const vis = data.slice(chart.offset, chart.offset + chart.visible);
  const cw = plotW / chart.visible;
  const cx = (i) => i * cw + cw / 2;

  let lo = Math.min(...vis.map((c) => c.l));
  let hi = Math.max(...vis.map((c) => c.h));
  for (const w of chart.walls) {
    if (w.price > lo * 0.96 && w.price < hi * 1.04) { lo = Math.min(lo, w.price); hi = Math.max(hi, w.price); }
  }
  for (const z of chart.levels) {
    if (z.mid > lo * 0.96 && z.mid < hi * 1.04) { lo = Math.min(lo, z.low); hi = Math.max(hi, z.high); }
  }
  const pad = (hi - lo) * 0.07 || hi * 0.0012;
  lo -= pad; hi += pad;
  const y = (p) => priceTop + (1 - (p - lo) / (hi - lo)) * priceH;

  ctx.textAlign = "left";
  for (let g = 0; g <= 4; g++) {
    const p = lo + ((hi - lo) * g) / 4;
    const gy = y(p);
    ctx.strokeStyle = "#1c2127";
    ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(plotW, gy); ctx.stroke();
    ctx.fillStyle = "#626b77";
    ctx.fillText(fmtPrice(p), plotW + 8, gy + 3);
  }

  const step = Math.max(1, Math.round(chart.visible / 6));
  ctx.textAlign = "center";
  for (let i = 0; i < vis.length; i += step) {
    const gx = cx(i);
    ctx.strokeStyle = "#161b20";
    ctx.beginPath(); ctx.moveTo(gx, priceTop); ctx.lineTo(gx, H - TIME_H); ctx.stroke();
    ctx.fillStyle = "#626b77";
    const d = new Date(vis[i].t);
    ctx.fillText(`${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`, gx, H - 8);
  }

  const vmax = Math.max(...vis.map((c) => c.v), 1e-9);
  for (let i = 0; i < vis.length; i++) {
    const c = vis[i];
    const bh = (c.v / vmax) * (VOL_H - 8);
    ctx.fillStyle = c.c >= c.o ? "rgba(49,151,107,0.35)" : "rgba(201,93,99,0.35)";
    ctx.fillRect(cx(i) - Math.max(1, cw * 0.31), H - TIME_H - bh, Math.max(1.5, cw * 0.62), bh);
  }

  const bw = Math.max(1.5, Math.min(cw * 0.62, 13));
  for (let i = 0; i < vis.length; i++) {
    const c = vis[i];
    const up = c.c >= c.o;
    ctx.strokeStyle = up ? UP : DOWN;
    ctx.fillStyle = up ? UP : DOWN;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(cx(i), y(c.h)); ctx.lineTo(cx(i), y(c.l)); ctx.stroke();
    const yO = y(c.o), yC = y(c.c);
    ctx.fillRect(cx(i) - bw / 2, Math.min(yO, yC), bw, Math.max(1, Math.abs(yC - yO)));
  }

  ctx.textAlign = "left";
  for (const z of chart.levels.slice(0, 6)) {
    if (z.high < lo || z.low > hi) continue;
    const my = y(z.mid);
    ctx.setLineDash([6, 4]);
    ctx.strokeStyle = z.kind === "resistance" ? "rgba(201,93,99,0.4)" : "rgba(49,151,107,0.4)";
    ctx.beginPath(); ctx.moveTo(0, my); ctx.lineTo(plotW, my); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(152,161,173,0.9)";
    ctx.fillText(`${t(z.kind)} ×${z.touches}`, 6, my - 4);
  }

  for (const w of chart.walls) {
    if (w.price < lo || w.price > hi) continue;
    const wy = y(w.price);
    ctx.setLineDash([2, 5]);
    ctx.strokeStyle = w.side === "bid" ? "rgba(49,151,107,0.45)" : "rgba(201,93,99,0.45)";
    ctx.beginPath(); ctx.moveTo(0, wy); ctx.lineTo(plotW, wy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = w.side === "bid" ? UP : DOWN;
    ctx.fillText(`${w.side === "bid" ? "bid wall" : "ask wall"} ${fmtUsd(w.notional)}`, 6, wy + 11);
  }

  const last = vis[vis.length - 1];
  const lpUpC = last.c >= last.o;
  const lpY = y(last.c);
  ctx.setLineDash([2, 3]);
  ctx.strokeStyle = lpUpC ? UP : DOWN;
  ctx.beginPath(); ctx.moveTo(0, lpY); ctx.lineTo(plotW, lpY); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = lpUpC ? UP : DOWN;
  ctx.fillRect(plotW + 2, lpY - 8, AXIS_W - 4, 15);
  ctx.fillStyle = "#101318";
  ctx.fillText(fmtPrice(last.c), plotW + 7, lpY + 3);

  if (chart.hover && !chart.dragging) {
    const { x: hx, y: hy } = chart.hover;
    if (hx < plotW && hy < H - TIME_H) {
      const idx = Math.floor(hx / cw);
      ctx.strokeStyle = "#39414d";
      ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(cx(idx), priceTop); ctx.lineTo(cx(idx), H - TIME_H); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, hy); ctx.lineTo(plotW, hy); ctx.stroke();
      ctx.setLineDash([]);
      const hp = lo + (1 - (hy - priceTop) / priceH) * (hi - lo);
      if (hy > priceTop && hy < priceTop + priceH) {
        ctx.fillStyle = "#262d36";
        ctx.fillRect(plotW + 2, hy - 8, AXIS_W - 4, 15);
        ctx.fillStyle = "#dde2ea";
        ctx.fillText(fmtPrice(hp), plotW + 7, hy + 3);
      }
      const c = vis[idx];
      if (c) {
        const d = new Date(c.t);
        ctx.fillStyle = "#98a1ad";
        ctx.fillText(
          `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}  O ${fmtPrice(c.o)}  H ${fmtPrice(c.h)}  L ${fmtPrice(c.l)}  C ${fmtPrice(c.c)}  V ${fmtUsd(c.v)}`,
          8, priceTop + 12,
        );
      }
    }
  }

  ctx.fillStyle = "#626b77";
  ctx.textAlign = "right";
  ctx.fillText("drag to pan · scroll to zoom", plotW - 8, priceTop + 12);
  ctx.textAlign = "left";
}

// ------------------------- notifications + alerts CRUD -------------------------
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (S.jwt) headers.Authorization = `Bearer ${S.jwt}`;
  return fetch(path, { ...opts, headers });
}

async function pollNotifications() {
  if (!S.jwt) { renderNotifPanel([]); return; }
  try {
    const res = await api("/api/v1/notifications");
    if (!res.ok) return;
    const body = await res.json();
    // toasts fire for genuinely new items; the unread badge only clears when
    // the bell panel is opened (notifLastId is updated there, not here)
    S._toastedId = S._toastedId ?? S.notifLastId;
    const fresh = body.notifications.filter((n) => n.id > S._toastedId);
    if (fresh.length) {
      for (const n of fresh.slice(0, 3)) toast(n.message, "warn");
      S._toastedId = Math.max(...body.notifications.map((n) => n.id));
    }
    renderNotifPanel(body.notifications);
  } catch {}
}
setInterval(pollNotifications, 8000);

function renderNotifPanel(items) {
  const el = $("notif-panel");
  if (items) S._notifItems = items;
  const all = S._notifItems || [];
  const unread = all.filter((n) => n.id > S.notifLastId).length;
  $("bell-count").textContent = unread;
  $("bell-count").classList.toggle("hidden", !unread);
  if (el.classList.contains("hidden")) return;
  if (!S.jwt) {
    el.innerHTML = `
      <div class="notif-item"><b>${t("signIn")}</b><br/>
        <input id="nl-email" class="nl-input" placeholder="${t("email")}" />
        <input id="nl-pass" class="nl-input" type="password" placeholder="${t("password")}" />
        <div style="display:flex;gap:6px;margin-top:6px;">
          <button id="nl-login" class="ctl ctl-btn">${t("signIn")}</button>
          <button id="nl-register" class="ctl ctl-btn">${t("signUp")}</button>
        </div>
      </div>`;
    $("nl-login").onclick = () => authFlow("/api/v1/auth/login");
    $("nl-register").onclick = () => authFlow("/api/v1/auth/register");
    return;
  }
  el.innerHTML = all.length
    ? all.map((n) => `
      <div class="notif-item"><b>${esc(n.symbol)}</b> ${esc(n.message)}
        <div class="delivery">${new Date(n.ts * 1000).toLocaleTimeString()} · ${esc(n.delivery.join(", "))}</div>
      </div>`).join("")
    : `<div class="notif-empty">${t("notifEmpty")}</div>`;
  el.innerHTML += `<div class="notif-actions"><button id="nl-logout" class="ctl ctl-btn">${t("logout")}</button></div>`;
  const lo = $("nl-logout");
  if (lo) lo.onclick = () => {
    S.jwt = null;
    LS.set("jwt", null);
    S.notifLastId = 0;
    LS.set("notifLastId", 0);
    S._notifItems = [];
    renderNotifPanel([]);
    if (ws) ws.close();  // reconnects honestly at the public tier
  };
}

async function authFlow(path) {
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("nl-email").value, password: $("nl-pass").value }),
    });
    if (!res.ok) { toast(t("loginError"), "err"); return; }
    if (path.endsWith("login")) {
      const body = await res.json();
      S.jwt = body.access_token;
      LS.set("jwt", S.jwt);
      toast("✓ " + t("signIn"));
      if (ws) ws.close();  // reconnect carries the token → pro tier immediately
      pollNotifications();
    } else {
      toast("✓ " + t("signUp"));
      await authFlow("/api/v1/auth/login");
    }
  } catch { toast(t("loginError"), "err"); }
}

$("bell").addEventListener("click", () => {
  const panel = $("notif-panel");
  panel.classList.toggle("hidden");
  if (!panel.classList.contains("hidden") && S._notifItems?.length) {
    S.notifLastId = Math.max(...S._notifItems.map((n) => n.id));
    LS.set("notifLastId", S.notifLastId);
    renderNotifPanel(S._notifItems);
  } else {
    renderNotifPanel([]);
  }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#notif-panel") && !e.target.closest("#bell")) $("notif-panel").classList.add("hidden");
});

$("alert-create").addEventListener("click", async () => {
  if (!S.jwt) { toast(t("authNeeded"), "warn"); $("notif-panel").classList.remove("hidden"); renderNotifPanel([]); return; }
  const body = {
    symbol: chartSym,
    rule_type: $("alert-type").value,
    threshold: parseFloat($("alert-threshold").value),
    cooldown_s: 300,
    recurring: true,
  };
  if (!body.threshold || body.threshold <= 0) { toast(t("thresholdNeeded"), "err"); return; }
  const res = await api("/api/v1/alerts", { method: "POST", body: JSON.stringify(body) });
  toast(res.ok ? `✓ ${t("alertCreated")}` : `${res.status}`, res.ok ? "" : "err");
});

// ------------------------- control wiring -------------------------
$("min-score").addEventListener("change", (e) => { S.minScore = +e.target.value; LS.set("minScore", S.minScore); renderTable(); renderHotlist(); renderBoard(); });
$("wall-filter").addEventListener("change", (e) => { S.wallMin = +e.target.value; drawRadar(); renderTable(); });
$("sound-toggle").addEventListener("click", (e) => {
  S.soundOn = !S.soundOn;
  LS.set("sound", S.soundOn);
  e.target.classList.toggle("on", S.soundOn);
  e.target.textContent = S.soundOn ? t("soundOn") : t("soundOff");
  if (S.soundOn) ping();
});
$("lang-toggle").textContent = S.lang === "ru" ? "RU" : "EN";
$("lang-toggle").addEventListener("click", (e) => {
  S.lang = S.lang === "ru" ? "en" : "ru";
  LS.set("lang", S.lang);
  e.target.textContent = S.lang === "ru" ? "RU" : "EN";
  applyI18n();
});
$("search").addEventListener("input", (e) => { S.search = e.target.value.trim(); renderTable(); });
$("view-toggle").addEventListener("click", () => {
  S.view = S.view === "table" ? "board" : "table";
  LS.set("view", S.view);
  applyView();
});
$("grid-size").addEventListener("change", (e) => { S.gridSize = +e.target.value; LS.set("grid", S.gridSize); S.page = 0; _boardSig = ""; renderBoard(); });
$("page-prev").addEventListener("click", () => { if (S.page > 0) { S.page -= 1; _boardSig = ""; renderBoard(); } });
$("page-next").addEventListener("click", () => { S.page += 1; _boardSig = ""; renderBoard(); });
$("watch-only").addEventListener("change", () => renderTable());

function applyView() {
  const board = S.view === "board";
  $("board-panel").classList.toggle("hidden", !board);
  $("table-panel").classList.toggle("hidden", board);
  $("view-toggle").classList.toggle("active", board);
  $("view-toggle").textContent = board ? t("table") : t("board");
  $("grid-size").style.display = board ? "" : "none";
  $("page-prev").style.display = board ? "" : "none";
  $("page-next").style.display = board ? "" : "none";
  $("page-ind").style.display = board ? "" : "none";
  if (board) { _boardSig = ""; renderBoard(); }
  else renderTable();
}

function applyI18n() {
  document.documentElement.lang = S.lang;
  const ths = document.querySelectorAll("#main-table thead th");
  const names = ["ticker", "price", "trend", "ch5m", "natr", "speed", "vol1m", "surge", "imb", "wall", "dist", "fund", "level", "score", "setup"];
  ths.forEach((th, i) => {
    if (!names[i]) return;
    const hint = th.querySelector(".hint-inline");
    th.textContent = t(names[i]);
    if (hint) th.appendChild(hint);
  });
  $("h2-scanner").textContent = t("scanner");
  $("h2-hotlist").textContent = t("hotlist");
  $("h2-radar").textContent = t("radar");
  $("h2-alerts").textContent = t("alerts");
  $("search").placeholder = t("searchPh");
  $("table-empty").textContent = S.symbols.length ? t("noMatch") : t("waiting");
  $("view-toggle").textContent = S.view === "board" ? t("table") : t("board");
  $("board-empty").textContent = t("waiting");
  $("chart-close").textContent = t("close");
  $("sound-toggle").textContent = S.soundOn ? t("soundOn") : t("soundOff");
  $("alert-create").textContent = t("createAlert");
  $("alert-threshold").placeholder = S.lang === "ru" ? "порог" : "threshold";
  const typeSel = $("alert-type");
  const ruleKeys = ["priceCrossAbove", "priceCrossBelow", "pctMove", "volumeSurge", "scoreAbove"];
  const ruleVals = ["price_cross_above", "price_cross_below", "pct_move", "volume_surge", "score_above"];
  const prevSel = typeSel.value;
  typeSel.innerHTML = ruleKeys.map((k, i) => `<option value="${ruleVals[i]}">${t(k)}</option>`).join("");
  typeSel.value = prevSel;
}

// ------------------------- boot -------------------------
$("min-score").value = String(S.minScore);
$("sound-toggle").classList.toggle("on", S.soundOn);
$("sound-toggle").textContent = S.soundOn ? t("soundOn") : t("soundOff");
$("grid-size").value = String(S.gridSize);

renderTicker();
setInterval(renderTicker, 2000);
applyView();
applyI18n();
updateSortIndicator();
pollNotifications();

setInterval(renderLoop, 150);
setInterval(() => { $("clock").textContent = new Date().toISOString().slice(11, 19); }, 1000);

function renderLoop() {
  renderTable();
  drawSparks();
  renderHotlist();
  renderAlerts();
  drawRadar();
  drawChart();
  const cutoff = performance.now() - 1000;
  while (S.msgTimes.length && S.msgTimes[0] < cutoff) S.msgTimes.shift();
  $("msgrate-val").textContent = S.msgTimes.length;
  $("symbols-val").textContent = S.symbols.length;
  $("latency-val").textContent = S.stats.latencyMs != null ? `${S.stats.latencyMs}ms` : "—";
  $("foot-stats").textContent =
    `feed: ${S.feed} · tracked: ${S.stats.tracked ?? 0} · ingested: ${(S.stats.ingested ?? 0).toLocaleString()} · score ≥ ${S.minScore}`;
  if (ws && ws.readyState === 1 && S.feed !== "—") {
    const b = $("feed-badge");
    if (b.classList.contains("badge-live")) b.textContent = `Live · ${S.feed.replace("-trades", "").toUpperCase()}`;
  }
}

connect();
