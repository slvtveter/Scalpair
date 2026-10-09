/* ============================================================
   SCALPAIR TERMINAL — vanilla JS, no build step
   WebSocket client · table renderer · density radar · hotlist
   canvas chart modal · WebAudio alerts · auto-reconnect
   ============================================================ */
"use strict";

const $ = (id) => document.getElementById(id);

const WS_PATH = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/ws/live-feed`;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

// ------------------------- state -------------------------
const S = {
  symbols: [],          // latest snapshot rows
  bySym: new Map(),     // symbol -> row
  picks: [],
  alerts: [],
  stats: {},
  feed: "—",
  priceHistory: new Map(), // symbol -> [{t, p}] (client-side ring buffer for chart)
  maxHistory: 900,
  soundOn: false,
  minScore: 0,
  wallMin: 250000,
  sortKey: "score",
  tickCounter: 0,
  msgTimes: [],
  lastAlertAt: new Map(),
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
    ws.send(JSON.stringify({ type: "hello" }));
  };
  ws.onmessage = (ev) => {
    const now = performance.now();
    S.msgTimes.push(now);
    try { onSnapshot(JSON.parse(ev.data)); } catch (e) { console.warn("bad frame", e); }
  };
  ws.onclose = () => scheduleReconnect();
  ws.onerror = () => { try { ws.close(); } catch {} };
}

function scheduleReconnect() {
  setBadge("reconnecting");
  const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** Math.min(wsAttempt, 4));
  wsAttempt += 1;
  setTimeout(connect, delay + Math.random() * 400);
}

// ------------------------- snapshot handling -------------------------
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
      if (!last || nowMs() - last.t > 1000) {
        hist.push({ t: nowMs(), p: r.price });
        if (hist.length > S.maxHistory) hist.shift();
      }
    }
    S.bySym.set(r.symbol, r);
  }
  checkAlerts();
}

function nowMs() { return Date.now(); }

// ------------------------- alerts (score >= 85) -------------------------
function checkAlerts() {
  if (!S.soundOn) return;
  for (const r of S.symbols) {
    if ((r.score ?? 0) < 85) continue;
    const last = S.lastAlertAt.get(r.symbol) || 0;
    if (nowMs() - last < 5 * 60 * 1000) continue;
    S.lastAlertAt.set(r.symbol, nowMs());
    ping(r.score);
  }
}

// WebAudio two-tone ping
function ping(score) {
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

// ------------------------- trend sparklines -------------------------
// draws the last ~10 minutes of client-collected prices into each row canvas
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
    if (max - min < min * 0.0002) max = min + min * 0.0002; // flat-market padding
    const up = ps[ps.length - 1] >= ps[0];
    const col = up ? "#31976b" : "#c95d63";
    const x = (i) => (i / (ps.length - 1)) * (W - 4) + 2;
    const y = (p) => H - 3 - ((p - min) / (max - min)) * (H - 6);
    // area
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
    // last point marker
    ctx.beginPath();
    ctx.arc(x(ps.length - 1), y(ps[ps.length - 1]), 1.7, 0, Math.PI * 2);
    ctx.fillStyle = col;
    ctx.fill();
  }
}

// ------------------------- main table -------------------------
const tbody = $("table-body");
const rowMap = new Map(); // symbol -> <tr>

function visibleRows() {
  let rows = S.symbols.filter((r) => (r.score ?? 0) >= S.minScore);
  const key = S.sortKey;
  rows = [...rows].sort((a, b) => {
    if (key === "symbol") return a.symbol.localeCompare(b.symbol);
    if (key === "wall") return (b.walls?.[0]?.notional ?? 0) - (a.walls?.[0]?.notional ?? 0);
    if (key === "surge") return (b.surge ?? 0) - (a.surge ?? 0);
    if (key === "vol1m") return (b.vol1m ?? 0) - (a.vol1m ?? 0);
    return (b.score ?? -1) - (a.score ?? -1);
  });
  return rows;
}

function bestWall(r) {
  if (!r.walls || !r.walls.length) return null;
  const filtered = r.walls.filter((w) => w.notional >= S.wallMin);
  return filtered[0] || r.walls[0] || null;
}

function renderTable() {
  const rows = visibleRows();
  $("table-empty").style.display = rows.length ? "none" : "";
  $("table-empty").textContent = rows.length ? "" :
    (S.symbols.length ? `no symbols match score ≥ ${S.minScore} — lower the filter` : "waiting for market data…");

  const seen = new Set();
  for (const r of rows) {
    seen.add(r.symbol);
    let tr = rowMap.get(r.symbol);
    if (!tr) {
      tr = document.createElement("tr");
      tr.tabIndex = 0;
      tr.setAttribute("role", "button");
      tr.setAttribute("aria-label", `Open SCALP view for ${r.symbol}`);
      tr.onclick = () => openChart(r.symbol);
      tr.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openChart(r.symbol); } };
      rowMap.set(r.symbol, tr); tbody.appendChild(tr);
    }
    tr.className = (r.score ?? 0) >= 85 ? "row-hot" : "";

    const wall = bestWall(r);
    const imb = r.imbalance ?? 0;
    const imbW = Math.min(Math.abs(imb) * 50, 50);
    const imbHtml = `<span class="imb-val mono">${imb >= 0 ? "+" : ""}${imb.toFixed(2)}</span><div class="imb-meter"><div class="imb-fill ${imb >= 0 ? "bid" : "ask"}" style="width:${imbW}%"></div></div>`;
    const sc = scoreClass(r.score);

    const cells = [
      `<td class="col-sym sym-cell">${esc(r.symbol)}<small>${r.tps != null ? `${r.tps.toFixed(1)} tps` : ""}</small></td>`,
      `<td class="mono">${fmtPrice(r.price)}</td>`,
      `<td class="col-trend spark-cell"><canvas class="spark" width="110" height="26"></canvas></td>`,
      `<td class="mono ${cls(r.change5m)}">${fmtPct(r.change5m)}</td>`,
      `<td class="mono dim">${fmtUsd(r.vol1m)}</td>`,
      `<td class="mono ${cls(r.surge - 1)}">${r.surge != null ? `${r.surge.toFixed(1)}x` : "—"}</td>`,
      `<td class="col-imb">${imbHtml}</td>`,
      `<td class="mono ${wall ? (wall.side === "bid" ? "up" : "down") : "dim"}">${wall ? fmtUsd(wall.notional) : "—"}</td>`,
      `<td class="mono dim">${wall ? `${Math.abs(wall.distance).toFixed(2)}% ${wall.side === "bid" ? "↓" : "↑"}` : "—"}</td>`,
      `<td class="mono ${cls((r.funding ?? 0) * -1)}">${r.funding != null ? `${r.funding > 0 ? "+" : ""}${(r.funding * 100).toFixed(1)}bp` : "—"}</td>`,
      `<td class="score-cell"><span class="score-num ${sc}">${r.score != null ? r.score.toFixed(0) : "—"}</span><span class="score-bar"><i class="${sc}" style="width:${r.score ?? 0}%"></i></span></td>`,
      `<td class="col-tag"><span class="tag-cell ${tagClass(r.tag)}">${esc(r.tag ?? "—")}</span></td>`,
    ];
    if (tr._sig !== cells.join("|")) { tr.innerHTML = cells.join(""); tr._sig = cells.join("|"); }
  }
  for (const [sym, tr] of rowMap) {
    if (!seen.has(sym)) { tr.remove(); rowMap.delete(sym); }
  }
}

// table header sorting (with active-column indicator)
const TH_KEYS = ["symbol", "price", null, "change5m", "vol1m", "surge", null, "wall", null, null, "score", "tag"];

function updateSortIndicator() {
  document.querySelectorAll("#main-table thead th").forEach((th, i) => {
    const active = TH_KEYS[i] === S.sortKey;
    th.classList.toggle("th-sort-active", active);
    if (active) th.setAttribute("aria-sort", "descending");
    else th.removeAttribute("aria-sort");
    th.style.cursor = TH_KEYS[i] ? "pointer" : "default";
  });
}

document.querySelectorAll("#main-table thead th").forEach((th, i) => {
  th.addEventListener("click", () => { if (TH_KEYS[i]) { S.sortKey = TH_KEYS[i]; renderTable(); updateSortIndicator(); } });
});

// ------------------------- hotlist -------------------------
let _hotlistSig = "";
function renderHotlist() {
  const el = $("hotlist-body");
  const picks = S.picks.filter((p) => p.score >= S.minScore);
  $("hotlist-mode").textContent = picks.length ? `TOP ${picks.length}` : "TOP 5";
  if (!S.picks.length) { setIfChanged(el, `<div class="empty-note">scoring engine warming up…</div>`); return; }
  if (!picks.length) { setIfChanged(el, `<div class="empty-note">no setups ≥ ${S.minScore} right now</div>`); return; }
  const html = picks.map((p) => `
    <div class="pick" data-sym="${esc(p.symbol)}" tabindex="0" role="button" aria-label="Open SCALP view for ${esc(p.symbol)}">
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
  if (html !== _hotlistSig) {
    _hotlistSig = html;
    el.innerHTML = html; // click delegation handles interactions; no per-render listeners
  }
}

// signature-diffed innerHTML: avoids re-render churn (hover/selection resets)
function setIfChanged(el, html) {
  if (el._sig !== html) { el._sig = html; el.innerHTML = html; }
}

// keyboard + click delegation for hotlist cards (innerHTML re-rendered)
$("hotlist-body").addEventListener("keydown", (e) => {
  const card = e.target.closest(".pick");
  if (card && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openChart(card.dataset.sym); }
});

// ------------------------- alerts panel -------------------------
let _alertsSig = "";
function renderAlerts() {
  const el = $("alerts-body");
  $("alert-count").textContent = S.alerts.length;
  const html = S.alerts.length
    ? [...S.alerts].reverse().map((a) => `
      <div class="alert-item"><b>${esc(a.symbol)}</b> ${a.score?.toFixed(0)} · ${esc(a.tag)}<br/>
      <span class="dim">${new Date(a.ts).toLocaleTimeString()} — ${esc(a.thesis ?? "")}</span></div>`).join("")
    : `<div class="empty-note">no alerts fired yet</div>`;
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
  // only touch the DOM when content actually changed — keeps the CSS
  // marquee from jittering on every re-render
  setIfChanged($("ticker-track"), html + html);
}

// ------------------------- density radar (canvas) -------------------------
const radar = $("radar-canvas");

// DPR-aware backing store: crisp lines/text on retina displays
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

  // grid
  rctx.strokeStyle = "#1e232a";
  rctx.lineWidth = 1;
  rctx.font = "9px 'JetBrains Mono', monospace";
  rctx.fillStyle = "#626b77";
  const distRange = 2.0; // ±2%
  for (let d = -2; d <= 2; d += 0.5) {
    const x = ((d + distRange) / (2 * distRange)) * (W - 40) + 30;
    rctx.beginPath();
    rctx.moveTo(x, 10); rctx.lineTo(x, H - 24);
    rctx.stroke();
    if (Number.isInteger(d * 2)) rctx.fillText(`${d > 0 ? "+" : ""}${d}%`, x - 10, H - 10);
  }
  for (const m of [0.25, 1, 4, 16]) { // notional rings (M$), log scale
    const y = H - 34 - (Math.log10(m * 1e6) / Math.log10(5e7)) * (H - 60);
    if (y < 10 || y > H - 24) continue;
    rctx.beginPath(); rctx.moveTo(30, y); rctx.lineTo(W - 10, y); rctx.stroke();
    rctx.fillText(`$${m}M`, 2, y + 3);
  }
  // mid line
  const x0 = 30 + 0.5 * (W - 40);
  rctx.strokeStyle = "#262d36";
  rctx.beginPath(); rctx.moveTo(x0, 10); rctx.lineTo(x0, H - 24); rctx.stroke();

  // walls
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

// ------------------------- chart modal (interactive candles) -------------------------
const modal = $("chart-modal");
let chartSym = null;

// interactive chart state: 1m OHLCV candles from the backend + walls
const chart = {
  candles: [],      // base 1m candles {t,o,h,l,c,v}
  series: [],       // aggregated view series per selected timeframe
  tfMin: 1,         // timeframe in minutes (client-side aggregation)
  walls: [],        // {side, price, notional}
  offset: 0,        // index of first visible candle
  visible: 120,     // candles in view (zoom level)
  hover: null,      // {x, y} in css px
  dragging: false,
  dragStartX: 0,
  dragStartOffset: 0,
  followRight: true, // auto-scroll to the live edge
};

function clampOffset(n) {
  const max = Math.max(0, chart.series.length - chart.visible);
  return Math.max(0, Math.min(max, n));
}

// aggregate base 1m candles into the selected timeframe
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
  $("chart-thesis").textContent = pick?.thesis || r?.thesis || "no scoring thesis yet";
  modal.classList.remove("hidden");
  chart.candles = [];
  chart.series = [];
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
    chart.visible = Math.min(chart.visible, Math.max(25, chart.series.length));
    if (resetView || chart.followRight) chart.offset = clampOffset(chart.series.length - chart.visible);
    drawChart();
  } catch {}
}

function closeChart() { modal.classList.add("hidden"); chartSym = null; }
$("chart-close").addEventListener("click", closeChart);
modal.addEventListener("click", (e) => { if (e.target === modal) closeChart(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeChart(); });

// --- interactions: drag to pan, wheel to zoom, hover for crosshair ---
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
    if (modal.classList.contains("hidden") || !chart.candles.length) return;
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
// timeframe picker (client-side aggregation of the 1m base series)
$("tf-picker").addEventListener("click", (e) => {
  const b = e.target.closest(".tf-btn");
  if (!b) return;
  chart.tfMin = +b.dataset.tf;
  document.querySelectorAll("#tf-picker .tf-btn").forEach((x) => x.classList.toggle("active", x === b));
  rebuildSeries();
  if (!$("chart-title").textContent) return;
  const sym = chartSym;
  if (sym) $("chart-title").textContent = `${sym} · ${chart.tfMin === 60 ? "1h" : chart.tfMin + "m"}`;
  chart.visible = Math.min(Math.max(25, chart.visible), Math.max(25, chart.series.length));
  chart.offset = clampOffset(chart.series.length - chart.visible);
  chart.followRight = true;
  drawChart();
});

// live edge refresh while the modal is open
setInterval(() => { if (!modal.classList.contains("hidden") && chartSym) refreshChart(); }, 5000);

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
  const volTop = priceTop + priceH + 6;

  const data = chart.series;
  if (!data.length) {
    ctx.fillStyle = "#7b8491";
    ctx.fillText("loading candles…", 20, H / 2);
    return;
  }

  const vis = data.slice(chart.offset, chart.offset + chart.visible);
  const cw = plotW / chart.visible;
  const cx = (i) => i * cw + cw / 2;

  // price scale over visible candles (+ nearby walls)
  let lo = Math.min(...vis.map((c) => c.l));
  let hi = Math.max(...vis.map((c) => c.h));
  for (const w of chart.walls) {
    if (w.price > lo * 0.96 && w.price < hi * 1.04) { lo = Math.min(lo, w.price); hi = Math.max(hi, w.price); }
  }
  const pad = (hi - lo) * 0.07 || hi * 0.0012;
  lo -= pad; hi += pad;
  const y = (p) => priceTop + (1 - (p - lo) / (hi - lo)) * priceH;

  // horizontal grid + right price axis
  ctx.textAlign = "left";
  for (let g = 0; g <= 4; g++) {
    const p = lo + ((hi - lo) * g) / 4;
    const gy = y(p);
    ctx.strokeStyle = "#1c2127";
    ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(plotW, gy); ctx.stroke();
    ctx.fillStyle = "#626b77";
    ctx.fillText(fmtPrice(p), plotW + 8, gy + 3);
  }

  // vertical time grid + labels
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

  // volume bars
  const vmax = Math.max(...vis.map((c) => c.v), 1e-9);
  for (let i = 0; i < vis.length; i++) {
    const c = vis[i];
    const bh = (c.v / vmax) * (VOL_H - 8);
    ctx.fillStyle = c.c >= c.o ? "rgba(49,151,107,0.35)" : "rgba(201,93,99,0.35)";
    ctx.fillRect(cx(i) - Math.max(1, cw * 0.31), H - TIME_H - bh, Math.max(1.5, cw * 0.62), bh);
  }

  // candles
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

  // wall levels (faint dashed, if inside range)
  ctx.textAlign = "left";
  for (const w of chart.walls) {
    if (w.price < lo || w.price > hi) continue;
    const wy = y(w.price);
    ctx.setLineDash([2, 5]);
    ctx.strokeStyle = w.side === "bid" ? "rgba(49,151,107,0.45)" : "rgba(201,93,99,0.45)";
    ctx.beginPath(); ctx.moveTo(0, wy); ctx.lineTo(plotW, wy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = w.side === "bid" ? UP : DOWN;
    ctx.fillText(`${w.side === "bid" ? "bid wall" : "ask wall"} ${fmtUsd(w.notional)}`, 6, wy - 4);
  }

  // last price marker
  const last = vis[vis.length - 1];
  const lpUp = last.c >= last.o;
  const lpY = y(last.c);
  ctx.setLineDash([2, 3]);
  ctx.strokeStyle = lpUp ? UP : DOWN;
  ctx.beginPath(); ctx.moveTo(0, lpY); ctx.lineTo(plotW, lpY); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = lpUp ? UP : DOWN;
  ctx.fillRect(plotW + 2, lpY - 8, AXIS_W - 4, 15);
  ctx.fillStyle = "#101318";
  ctx.fillText(fmtPrice(last.c), plotW + 7, lpY + 3);

  // crosshair + OHLC readout
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
        const hh = String(d.getUTCHours()).padStart(2, "0");
        const mm = String(d.getUTCMinutes()).padStart(2, "0");
        ctx.fillStyle = "#98a1ad";
        ctx.fillText(
          `${hh}:${mm}  O ${fmtPrice(c.o)}  H ${fmtPrice(c.h)}  L ${fmtPrice(c.l)}  C ${fmtPrice(c.c)}  V ${fmtUsd(c.v)}`,
          8, priceTop + 12,
        );
      }
    }
  }

  // drag hint
  ctx.fillStyle = "#626b77";
  ctx.textAlign = "right";
  ctx.fillText("drag to pan · scroll to zoom", plotW - 8, priceTop + 12);
  ctx.textAlign = "left";
}

// ------------------------- control wiring -------------------------
$("min-score").addEventListener("change", (e) => { S.minScore = +e.target.value; renderTable(); renderHotlist(); });
$("wall-filter").addEventListener("change", (e) => { S.wallMin = +e.target.value; drawRadar(); renderTable(); });
$("sound-toggle").addEventListener("click", (e) => {
  S.soundOn = !S.soundOn;
  e.target.classList.toggle("on", S.soundOn);
  e.target.textContent = S.soundOn ? "Sound on" : "Sound off";
  if (S.soundOn) ping(100);
});

// ------------------------- boot & render loops -------------------------
function renderLoop() {
  renderTable();
  drawSparks();
  renderHotlist();
  renderAlerts();
  drawRadar();
  drawChart();
  // stats
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
setInterval(renderLoop, 150);

setInterval(() => {
  $("clock").textContent = new Date().toISOString().slice(11, 19);
}, 1000);

renderTicker();
setInterval(renderTicker, 2000);

connect();
updateSortIndicator();
