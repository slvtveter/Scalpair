/* ============================================================
   SCALPAIR v0.5 — Scalpboard-style layout
   Center: live chart board 3×3 · Right: coin list · Toolbar: scalper tools
   ============================================================ */
"use strict";

const $ = (id) => document.getElementById(id);

// Same-origin (docker/nginx) or cross-origin on Render (static → backend)
const API_BASE = window.SCALPAIR_API_BASE ||
  (location.hostname.endsWith("onrender.com") && !location.hostname.startsWith("scalpair-backend")
    ? "https://scalpair-backend.onrender.com"
    : "");
const WS_PATH = API_BASE
  ? `${API_BASE.replace(/^http/, "ws")}/api/v1/ws/live-feed`
  : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/ws/live-feed`;

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;
const STALE_BOOK_MS = 15000;

// ------------------------- i18n (RU default) -------------------------
const I18N = {
  ru: {
    coins: "Монеты", alerts: "Алерты", alertsBtn: "♧",
    feed: "Источник", soundLabel: "Звук", langLabel: "Язык", minScoreLabel: "Минимальный скоринг", minWallLabel: "Мин. плотность",
    ticker: "Тикер", price: "Цена", ch5m: "5м %", surge: "Вспл", vol: "Объём", score: "Скор",
    waiting: "ожидание данных…", noMatch: "нет монет под фильтр", warmup: "движок считает…",
    notifEmpty: "уведомлений нет", signIn: "Войти", signUp: "Регистрация",
    email: "email", password: "пароль", loginError: "Ошибка входа/регистрации",
    authNeeded: "Войдите (Алерты), чтобы создавать правила",
    createAlert: "Создать алерт", alertCreated: "Алертовое правило создано",
    thresholdNeeded: "укажите порог", close: "Закрыть", logout: "Выйти",
    priceCrossAbove: "цена выше", priceCrossBelow: "цена ниже", pctMove: "движение %",
    volumeSurge: "всплеск объёма x", scoreAbove: "скоринг ≥", cascadeDistance: "рядом с каскадом %", densityAppeared: "появилась плотность USDT",
    resistance: "сопротивление", support: "поддержка", inside: "внутри зоны",
    searchPh: "Поиск…", reconnecting: "переподключение", connecting: "подключение",
    soundOn: "Звук вкл", soundOff: "Звук выкл", stale: "stale",
    sortBy: "Сорт", tf: "ТФ", grid: "Сетка",
  },
  en: {
    coins: "Coins", alerts: "Alerts", alertsBtn: "♧",
    feed: "Feed", soundLabel: "Sound", langLabel: "Language", minScoreLabel: "Minimum score", minWallLabel: "Minimum wall",
    ticker: "Ticker", price: "Price", ch5m: "5m %", surge: "Surge", vol: "Volume", score: "Score",
    waiting: "waiting for market data…", noMatch: "no symbols match filter", warmup: "scoring engine warming up…",
    notifEmpty: "no notifications yet", signIn: "Sign in", signUp: "Register",
    email: "email", password: "password", loginError: "Login/registration failed",
    authNeeded: "Sign in (Alerts) to create rules",
    createAlert: "Create alert", alertCreated: "Alert rule created",
    thresholdNeeded: "set a threshold", close: "Close", logout: "Log out",
    priceCrossAbove: "price above", priceCrossBelow: "price below", pctMove: "move %",
    volumeSurge: "volume surge x", scoreAbove: "score ≥", cascadeDistance: "near cascade %", densityAppeared: "large density USDT",
    resistance: "resistance", support: "support", inside: "inside zone",
    searchPh: "Search…", reconnecting: "reconnecting", connecting: "connecting",
    soundOn: "Sound on", soundOff: "Sound off", stale: "stale",
    sortBy: "Sort", tf: "TF", grid: "Grid",
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
  stats: {},
  feed: "—",
  priceHistory: new Map(),
  maxHistory: 900,
  soundOn: LS.get("sound", false),
  minScore: LS.get("minScore", 0),
  wallMin: LS.get("wallMin", 250000),
  sortKey: LS.get("sort", "score"),
  sortAscending: LS.get("sortAscending", false),
  filters: LS.get("filters", {}),
  presets: LS.get("filterPresets", {}),
  boardTf: LS.get("boardTf", 5),
  showLevels: LS.get("showLevels", true),
  showWalls: LS.get("showWalls", true),
  gridSize: LS.get("grid", 3),
  page: 0,
  search: "",
  searchQuery: "",
  searchIndex: 0,
  catalog: [],
  catalogAt: 0,
  hidden: new Set(LS.get("hidden", [])),
  focusOpen: false,
  syncCrosshair: LS.get("syncCrosshair", true),
  syncRange: LS.get("syncRange", false),
  paneOverlays: LS.get("paneOverlays", {}),
  focusLayout: [1,2,4].includes(LS.get("focusLayout",2)) ? LS.get("focusLayout",2) : 2,
  watch: new Set(LS.get("watch", [])),
  groups: LS.get("groups", []),
  activeGroup: LS.get("activeGroup", "all"),
  jwt: LS.get("jwt", null),
  lang: LS.get("lang", "ru"),
  candleCache: new Map(),
  notifLastId: LS.get("notifLastId", 0),
  _toastedId: LS.get("notifLastId", 0),
  _notifItems: [],
  msgTimes: [],
  lastAlertAt: new Map(),
  sideTab: "coins",
  venue: "ALL",
  autoSort: LS.get("autoSort", true),
  order: LS.get("order", []),
  hoverUntil: 0,
  connected: false,
  feedDegraded: false,
  sourceGeneration: null,
  dataEpoch: 0,
  lastSnapshotAt: 0,
};

const GROUP_COLORS = {green:"#43c77b", red:"#d3656e", blue:"#7aa0e8", purple:"#b18bda", amber:"#e0b765"};
const GROUP_COLOR_NAMES = {green:["Зелёный","Green"],red:["Красный","Red"],blue:["Синий","Blue"],purple:["Фиолетовый","Purple"],amber:["Жёлтый","Amber"]};
S.groups = Array.isArray(S.groups) ? S.groups.filter(g => g && typeof g.id === "string" && typeof g.name === "string" && GROUP_COLORS[g.color] && Array.isArray(g.symbols)) : [];
if (!["all","watch"].includes(S.activeGroup) && !S.groups.some(g=>g.id===S.activeGroup)) S.activeGroup="all";

function saveGroup(id, name, color) {
  name = name.trim();
  if (!name || name.length>40 || !GROUP_COLORS[color]) throw new Error(S.lang==="ru" ? "Укажите название и цвет" : "Enter a name and color");
  const existing=S.groups.find(g=>g.id===id);
  if (id && !existing) throw new Error("Group not found");
  if (existing) { existing.name=name; existing.color=color; }
  else { id=crypto.randomUUID(); S.groups.push({id,name,color,symbols:[]}); }
  LS.set("groups",S.groups); return id;
}
function deleteGroup(id) {
  S.groups=S.groups.filter(g=>g.id!==id);
  if (S.activeGroup===id) {S.activeGroup="all";LS.set("activeGroup","all");}
  LS.set("groups",S.groups);
}
function toggleGroupSymbol(id,symbol) {
  if (id==="watch") {
    if (S.watch.has(symbol)) S.watch.delete(symbol); else S.watch.add(symbol);
    LS.set("watch",[...S.watch]);return;
  }
  const group=S.groups.find(g=>g.id===id);
  if (!group) return;
  group.symbols=group.symbols.includes(symbol)?group.symbols.filter(s=>s!==symbol):[...group.symbols,symbol];
  LS.set("groups",S.groups);
}
function inActiveGroup(symbol) {
  if (S.activeGroup==="all") return true;
  if (S.activeGroup==="watch") return S.watch.has(symbol);
  return Boolean(S.groups.find(g=>g.id===S.activeGroup)?.symbols.includes(symbol));
}
function groupColor(symbol) {
  const group=S.groups.find(g=>g.id===S.activeGroup && g.symbols.includes(symbol)) || S.groups.find(g=>g.symbols.includes(symbol));
  return GROUP_COLORS[group?.color] || "transparent";
}
function renderGroups() {
  const en=S.lang==="en";
  const options=`<option value="watch">★ ${en?"Favorites":"Избранное"}</option>` + S.groups.map(g=>`<option value="${esc(g.id)}">${esc(g.name)}</option>`).join("");
  $("group-select").innerHTML=`<option value="all">${en?"All markets":"Все монеты"}</option>`+options;
  $("group-select").value=S.activeGroup;
  $("group-edit").disabled=["all","watch"].includes(S.activeGroup);
  $("group-edit").textContent=en?"Edit":"Изменить";
  const previous=$("chart-group").value;
  $("chart-group").innerHTML=options;
  $("chart-group").value=previous==="watch"||S.groups.some(g=>g.id===previous)?previous:"watch";
  $("group-color").innerHTML=Object.keys(GROUP_COLORS).map(c=>`<option value="${c}">${GROUP_COLOR_NAMES[c][en?1:0]}</option>`).join("");
  $("group-save").textContent=en?"Save":"Сохранить";
  $("group-delete").textContent=en?"Delete":"Удалить";
  $("group-cancel").textContent=en?"Cancel":"Отмена";
  $("group-name-label").textContent=en?"Name":"Название";
  $("group-color-label").textContent=en?"Color":"Цвет";
}
let editingGroup=null;
function openGroupEditor(id=null) {
  editingGroup=id;
  const group=S.groups.find(g=>g.id===id);
  $("group-editor-title").textContent=S.lang==="ru"?(group?"Изменить группу":"Новая группа"):(group?"Edit group":"New group");
  $("group-name").value=group?.name||"";$("group-color").value=group?.color||"green";
  $("group-delete").disabled=!group;$("group-error").textContent="";
  $("group-dialog").showModal();$("group-name").focus();
}
$("group-create").addEventListener("click",()=>openGroupEditor());
$("group-edit").addEventListener("click",()=>openGroupEditor(S.activeGroup));
$("group-cancel").addEventListener("click",()=>$("group-dialog").close());
$("group-form").addEventListener("submit",e=>{
  e.preventDefault();
  try {
    S.activeGroup=saveGroup(editingGroup,$("group-name").value,$("group-color").value);
    LS.set("activeGroup",S.activeGroup);S.page=0;
    $("group-dialog").close();renderGroups();renderBoard();renderCoinList();
  } catch(error) {$("group-error").textContent=error.message;}
});
$("group-delete").addEventListener("click",()=>{
  deleteGroup(editingGroup);$("group-dialog").close();renderGroups();renderBoard();renderCoinList();
});
$("group-select").addEventListener("change",e=>{
  S.activeGroup=e.target.value;S.page=0;LS.set("activeGroup",S.activeGroup);renderGroups();renderBoard();renderCoinList();
});

// ------------------------- ws -------------------------
let ws = null;
let wsAttempt = 0;

function setFeedStatus(status) {
  S.connected = status === "live";
  const demo = S.feed.includes("mock");
  const text = status === "live"
    ? `${demo ? "DEMO · " : ""}${S.feed.toUpperCase()}`
    : status === "reconnecting" ? t("reconnecting") : t("connecting");
  $("feed-badge").textContent = text;
  $("feed-badge").style.color = status === "live" && !demo ? "var(--up)" : "var(--warn)";
  const badge = document.querySelector(".live-pill");
  badge.textContent = demo ? "DEMO · synthetic data" : status === "live" ? "Live" : "Нет свежих данных";
  if (status === "live" && S.feedDegraded) badge.textContent = "Неполный поток · нет сделок";
  badge.style.color = status === "live" && !demo && !S.feedDegraded ? "var(--up)" : "var(--warn)";
  $("set-feed").textContent = `${S.feed} · ${S.stats.tracked ?? 0}`;
  if (!S.connected) document.querySelectorAll(".tile-canvas-wrap canvas").forEach(cv => {
    if (cv._chartArgs) paintTile(cv,...cv._chartArgs);
  });
}

function connect() {
  setFeedStatus(wsAttempt === 0 ? "connecting" : "reconnecting");
  try { ws = new WebSocket(WS_PATH); } catch { scheduleReconnect(); return; }
  ws.onopen = () => {
    wsAttempt = 0;
    setFeedStatus("connecting");
    ws.send(JSON.stringify({ type: "hello", token: S.jwt || undefined }));
  };
  ws.onmessage = (ev) => { S.msgTimes.push(performance.now()); try { onSnapshot(JSON.parse(ev.data)); } catch {} };
  ws.onclose = () => scheduleReconnect();
  ws.onerror = () => { try { ws.close(); } catch {} };
}

function scheduleReconnect() {
  setFeedStatus("reconnecting");
  const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** Math.min(wsAttempt, 4));
  wsAttempt += 1;
  setTimeout(connect, delay + Math.random() * 400);
}

function onSnapshot(snap) {
  if (snap.type !== "snapshot") return;
  if ((snap.feed && snap.feed !== S.feed) || (snap.sourceGeneration != null && snap.sourceGeneration !== S.sourceGeneration)) {
    S.dataEpoch += 1;
    S.candleCache.clear(); S.priceHistory.clear();
    S.catalog = []; S.catalogAt = 0;
    for (const pane of focusPanes) { pane.candles = []; pane.series = []; pane.walls = []; pane.levels = []; }
    document.querySelectorAll(".density-pill.dynamic").forEach(el => el.remove());
  }
  S.sourceGeneration = snap.sourceGeneration ?? null;
  S.feedDegraded = Boolean(snap.feedDegraded);
  S.lastSnapshotAt = Date.now();
  S.feed = snap.feed || S.feed;
  S.symbols = snap.symbols || [];
  S.picks = snap.picks || [];
  S.stats = snap.stats || {};
  S.bySym.clear();
  setFeedStatus("live");
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
function isStale(r) { return !S.connected || !r.bookTs || Date.now() - r.bookTs > STALE_BOOK_MS || Date.now() - S.lastSnapshotAt > 30000; }

// ------------------------- sorting / filtering -------------------------
const FILTER_FIELDS = [
  { key: "vol24h", ru: "Объём 24ч · млн USDT", en: "24h volume · M USDT", scale: 1e6, nonnegative: true },
  { key: "natr", ru: "NATR(14) · 1м · %", en: "NATR(14) · 1m · %", scale: 1, nonnegative: true },
  { key: "change5m", ru: "Изменение · 5м · %", en: "Change · 5m · %", scale: 1 },
  { key: "speed", ru: "Скорость · 60с · %/мин", en: "Speed · 60s · %/min", scale: 1 },
  { key: "levelDist", ru: "До каскада · %", en: "Cascade distance · %", scale: 1, nonnegative: true },
];

function parseMarketFilters(read) {
  const result = {};
  for (const field of FILTER_FIELDS) {
    const bounds = {};
    for (const side of ["min", "max"]) {
      const raw = String(read(field.key, side) ?? "").trim();
      if (!raw) continue;
      const value = Number(raw);
      if (!Number.isFinite(value) || (field.nonnegative && value < 0)) throw new Error(S.lang === "ru" ? "Введите допустимое число" : "Enter a valid number");
      bounds[side] = value * field.scale;
      if (!Number.isFinite(bounds[side])) throw new Error("Value out of range");
    }
    if (bounds.min != null && bounds.max != null && bounds.min > bounds.max) throw new Error(S.lang === "ru" ? "Минимум не может быть больше максимума" : "Minimum cannot exceed maximum");
    if (Object.keys(bounds).length) result[field.key] = bounds;
  }
  return result;
}

function matchesMarketFilters(row, filters) {
  return FILTER_FIELDS.every(({key}) => {
    const bounds = filters[key];
    if (!bounds || (bounds.min == null && bounds.max == null)) return true;
    const value = row[key];
    return Number.isFinite(value) && (bounds.min == null || value >= bounds.min) && (bounds.max == null || value <= bounds.max);
  });
}

function renderMarketFilters() {
  const en = S.lang === "en";
  $("filters-fields").innerHTML = FILTER_FIELDS.map(f => `<fieldset><legend>${en ? f.en : f.ru}</legend><div class="filter-range">${["min", "max"].map(side => `<label>${side}<input id="filter-${f.key}-${side}" type="number" step="any" ${f.nonnegative ? 'min="0"' : ''} value="${S.filters[f.key]?.[side] != null ? S.filters[f.key][side] / f.scale : ''}" aria-label="${en ? f.en : f.ru} ${side}"/></label>`).join("")}</div></fieldset>`).join("");
  $("filters-label").textContent = en ? "Filters" : "Фильтры";
  $("filters-count").textContent = Object.keys(S.filters).length;
  $("filters-apply").textContent = en ? "Apply" : "Применить";
  $("filters-reset").textContent = en ? "Reset" : "Сбросить";
  $("presets-label").textContent = en ? "Saved filters" : "Сохранённые фильтры";
  $("preset-name").placeholder = en ? "Name" : "Название";
  $("preset-save").textContent = en ? "Save" : "Сохранить";
  $("preset-delete").textContent = en ? "Delete" : "Удалить";
  $("filter-presets").innerHTML = '<option value="">—</option>' + Object.keys(S.presets).sort().map(name => `<option value="${esc(name)}">${esc(name)}</option>`).join("");
}

function applyMarketFilters(filters) {
  S.filters = filters; S.page = 0;
  LS.set("filters", filters);
  $("filters-error").textContent = "";
  renderMarketFilters(); renderBoard(); renderCoinList();
}

function readFilterForm() {
  return parseMarketFilters((key, side) => $(`filter-${key}-${side}`).value);
}

$("filters-form").addEventListener("submit", e => {
  e.preventDefault();
  try { applyMarketFilters(readFilterForm()); $("market-filters").open = false; }
  catch (error) { $("filters-error").textContent = error.message; }
});
$("filters-reset").addEventListener("click", () => applyMarketFilters({}));
$("filter-presets").addEventListener("change", e => {
  const name = e.target.value;
  if (Object.hasOwn(S.presets, name)) { applyMarketFilters(structuredClone(S.presets[name])); $("filter-presets").value = name; }
});
$("preset-save").addEventListener("click", () => {
  const name = $("preset-name").value.trim();
  if (!name) { $("filters-error").textContent = S.lang === "ru" ? "Укажите название" : "Enter a name"; return; }
  try {
    const filters = readFilterForm();
    S.presets = {...S.presets, [name]: filters}; LS.set("filterPresets", S.presets);
    applyMarketFilters(filters); $("filter-presets").value = name;
  } catch (error) { $("filters-error").textContent = error.message; }
});
$("preset-delete").addEventListener("click", () => {
  delete S.presets[$("filter-presets").value]; LS.set("filterPresets", S.presets); renderMarketFilters();
});
$("sort-direction").addEventListener("click", () => {
  S.sortAscending = !S.sortAscending; LS.set("sortAscending", S.sortAscending);
  $("sort-direction").textContent = S.sortAscending ? "↑" : "↓";
  renderBoard(); renderCoinList();
});

function compareRows(a, b) {
  const key = S.sortKey === "levelDist" ? "levelDist" :
    ["surge", "vol1m", "vol24h", "natr", "range5m", "speed", "change5m"].includes(S.sortKey) ? S.sortKey : "score";
  const av = a[key], bv = b[key];
  // Missing metrics always come last, including when both are missing.
  const validA = Number.isFinite(av), validB = Number.isFinite(bv);
  if (validA !== validB) return validA ? -1 : 1;
  const delta = validA ? (S.sortAscending ? av - bv : bv - av) : 0;
  return delta || a.symbol.localeCompare(b.symbol);
}

function sortedRows() {
  const available = new Map(S.symbols.map((r) => [r.symbol, r]));
  if (S.autoSort && !S.focusOpen && Date.now() >= S.hoverUntil) {
    S.order = [...S.symbols].sort(compareRows).map((r) => r.symbol);
  } else {
    const known = new Set(S.order);
    const added = S.symbols.filter((r) => !known.has(r.symbol)).sort(compareRows);
    S.order = [...S.order, ...added.map((r) => r.symbol)];
  }
  return S.order.map((sym) => available.get(sym)).filter((r) => r && !S.hidden.has(r.symbol) && inActiveGroup(r.symbol) &&
    (!S.search || r.symbol.includes(S.search.toUpperCase())) &&
    (S.venue === "ALL" || (r.venue || r.exchange || "—") === S.venue) &&
    (r.score ?? 0) >= S.minScore && matchesMarketFilters(r, S.filters));
}

function pauseBoardInteraction() {
  S.hoverUntil = Date.now() + 3000;
}

function setAutoSort(enabled) {
  // Keep the last displayed ordering; do not recompute on the pause click.
  S.autoSort = enabled;
  if (enabled) S.hoverUntil = 0;
  LS.set("autoSort", enabled);
  if (!enabled) LS.set("order", S.order);
  updateSortControl();
}

function updateSortControl() {
  const control = document.querySelector(".auto-chip");
  control.classList.toggle("active", S.autoSort);
  control.setAttribute("aria-pressed", String(S.autoSort));
  control.textContent = !S.autoSort ? "Ⅱ PAUSED" : Date.now() < S.hoverUntil ? "Ⅱ 3s" : "↗ AUTO";
}

function moveBoardSymbol(source, target) {
  if (S.autoSort || source === target || !S.order.includes(source) || !S.order.includes(target)) return false;
  const order = S.order.filter((sym) => sym !== source);
  order.splice(order.indexOf(target), 0, source);
  S.order = order;
  LS.set("order", order);
  return true;
}

// ------------------------- board (center) -------------------------
async function getCandles(sym) {
  const epoch = S.dataEpoch;
  const cached = S.candleCache.get(sym);
  if (cached && Date.now() - cached.ts < 60000) return cached.candles;
  try {
    const res = await fetch(`${API_BASE}/api/v1/markets/${encodeURIComponent(sym)}/candles?limit=300`, { cache: "no-store" });
    if (epoch !== S.dataEpoch) return [];
    if (!res.ok) return cached?.candles || [];
    const body = await res.json();
    if (epoch !== S.dataEpoch) return [];
    S.candleCache.set(sym, { ts: Date.now(), candles: body.candles || [] });
    return body.candles || [];
  } catch { return epoch === S.dataEpoch ? cached?.candles || [] : []; }
}

async function refreshDensityMap() {
  const epoch = S.dataEpoch;
  const grid = $("density-grid");
  const badge = document.querySelector(".density-live");
  if (!grid) return;
  try {
    const res = await fetch(`${API_BASE}/api/v1/screeners/densities?limit=200`, { cache: "no-store", signal: AbortSignal.timeout(8000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json();
    if (epoch !== S.dataEpoch) return;
    const walls = (body.walls || []).filter((w) =>
      Number.isFinite(w.distance_pct) && Math.abs(w.distance_pct) <= 3 &&
      w.notional_usd >= S.wallMin && (S.venue === "ALL" || w.venue === S.venue) &&
      w.book_ts && Date.now() - w.book_ts <= STALE_BOOK_MS);
    grid.querySelectorAll(".density-pill").forEach((el) => el.remove());
    $("density-mid").textContent = "0% · от mid каждого инструмента";
    badge.textContent = body.feed === "mock" ? "DEMO · synthetic" : walls.length ? `${walls.length} · top L2` : "Нет свежих плотностей";
    walls.slice(0, 12).forEach((wall, i) => {
      const pill = document.createElement("button");
      pill.className = `density-pill dynamic ${wall.side === "bid" ? "bid" : "ask"}`;
      pill.style.top = `${50 - wall.distance_pct / 3 * 45}%`;
      pill.style.left = `${(i % 2) * 47 + 2}%`;
      pill.textContent = `${wall.venue || "—"} ${wall.symbol.replace(/USDT$/, "")} ${fmtUsd(wall.notional_usd)}`;
      pill.title = `${wall.symbol} · ${wall.side} · ${fmtPrice(wall.price)} · ${wall.distance_pct.toFixed(2)}%`;
      pill.onclick = () => openChart(wall.symbol);
      grid.appendChild(pill);
    });
  } catch {
    if (epoch !== S.dataEpoch) return;
    grid.querySelectorAll(".density-pill").forEach((el) => el.remove());
    badge.textContent = "Плотности недоступны";
  }
}

setInterval(refreshDensityMap, 10000);
setTimeout(refreshDensityMap, 2200);

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

function boardOverlays(row) {
  const fresh = S.connected && Date.now() - S.lastSnapshotAt <= 30000;
  return {
    levels: fresh && S.showLevels ? (row?.levels || []).filter(z =>
      Number.isFinite(z.low) && Number.isFinite(z.high) && Number.isFinite(z.mid) && z.low <= z.high && z.touches >= 3) : [],
    walls: fresh && S.showWalls && !isStale(row || {}) ? (row?.walls || []).filter(w =>
      Number.isFinite(w.price) && Number.isFinite(w.notional) && w.notional >= S.wallMin && ["bid", "ask"].includes(w.side)) : [],
  };
}

for (const [id,key] of [["show-levels","showLevels"],["show-walls","showWalls"]]) {
  $(id).addEventListener("change", e => { S[key] = e.target.checked; LS.set(key,S[key]); renderBoard(); });
}

function paintTile(cv, candles, livePrice, symbol = "", overlays = {levels:[],walls:[]}) {
  if (!S.connected || Date.now()-S.lastSnapshotAt>30000) overlays={levels:[],walls:[]};
  else if (isStale(S.bySym.get(symbol) || {})) overlays={...overlays,walls:[]};
  cv._chartArgs = [candles,livePrice,symbol,overlays];
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  if (!W || !H) return;
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.font = "9px monospace";
  ctx.fillStyle = "#929292";
  if (!candles.length) {
    ctx.fillText(S.lang === "ru" ? "История свечей недоступна" : "Candle history unavailable", 12, 22);
    return;
  }
  const vis = candles.slice(-80).map((c) => ({ ...c }));
  const last = vis[vis.length - 1];
  if (livePrice > 0 && last.t + S.boardTf * 60000 > Date.now()) {
    last.c = livePrice; last.h = Math.max(last.h, livePrice); last.l = Math.min(last.l, livePrice);
  }
  const plotW = Math.max(40, W - 68), bottom = H - 20;
  const volumeH = Math.min(36, H * .17), priceH = Math.max(20, bottom - volumeH - 12);
  let lo = Math.min(...vis.map(c => c.l)), hi = Math.max(...vis.map(c => c.h));
  const pad = (hi - lo) * .09 || Math.max(Math.abs(lo) * .001, .000001);
  lo -= pad; hi += pad;
  const y = price => 6 + (hi - price) / (hi - lo) * priceH;
  const slots = Math.max(25, vis.length), cw = plotW / slots;
  const x = i => (slots - vis.length + i + .5) * cw;
  ctx.strokeStyle = "#252525"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const price = hi - i / 4 * (hi - lo), py = y(price);
    ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(plotW, py); ctx.stroke();
    ctx.fillStyle = "#888888"; ctx.fillText(fmtPrice(price), plotW + 4, py + 3);
  }
  ctx.beginPath(); ctx.moveTo(plotW, 0); ctx.lineTo(plotW, bottom); ctx.stroke();
  ctx.save(); ctx.font = `${Math.min(40, plotW / 6)}px monospace`;
  ctx.fillStyle = "#ffffff10"; ctx.textAlign = "center";
  ctx.fillText(symbol.replace(/USDT$/, ""), plotW / 2, priceH / 2 + 12); ctx.restore();
  ctx.save();
  ctx.beginPath(); ctx.rect(0, 0, plotW, bottom); ctx.clip();
  for (const z of overlays.levels) {
    if (z.mid < lo || z.mid > hi) continue;
    ctx.fillStyle = "#a984db1a";
    ctx.fillRect(0, y(z.high), plotW, Math.max(2,y(z.low)-y(z.high)));
    ctx.strokeStyle = "#a984db"; ctx.setLineDash([5,3]);
    ctx.beginPath(); ctx.moveTo(0,y(z.mid)); ctx.lineTo(plotW,y(z.mid)); ctx.stroke();ctx.setLineDash([]);
    ctx.fillStyle = "#bda2e4";
    ctx.fillText(`1m · ${z.touches} ${S.lang === "ru" ? "касаний" : "touches"}`, 4, Math.max(11,y(z.mid)-4));
  }
  ctx.restore();
  const maxVolume = Math.max(1, ...vis.map(c => c.v || 0));
  const bw = Math.max(1, Math.min(cw * .65, 9));
  for (let i = 0; i < vis.length; i++) {
    const c = vis[i], cx = x(i), color = c.c >= c.o ? "#43c77b" : "#d3656e";
    ctx.fillStyle = color; ctx.strokeStyle = color;
    ctx.beginPath(); ctx.moveTo(cx, y(c.h)); ctx.lineTo(cx, y(c.l)); ctx.stroke();
    ctx.fillRect(cx - bw / 2, Math.min(y(c.o), y(c.c)), bw, Math.max(1, Math.abs(y(c.c) - y(c.o))));
    ctx.fillStyle = "#555555";
    const vh = (c.v || 0) / maxVolume * volumeH;
    ctx.fillRect(cx - bw / 2, bottom - vh, bw, vh);
  }
  for (const wall of overlays.walls) {
    if (wall.price < lo || wall.price > hi) continue;
    const py = y(wall.price);
    ctx.strokeStyle = wall.side === "bid" ? "#43c77b" : "#d3656e";
    ctx.setLineDash([2,2]); ctx.beginPath(); ctx.moveTo(plotW*.55,py); ctx.lineTo(plotW,py); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = ctx.strokeStyle;
    ctx.fillText(`${wall.side.toUpperCase()} ${fmtUsd(wall.notional)}`, Math.max(2,plotW-92), Math.max(11,py-4));
  }
  ctx.strokeStyle = "#969696"; ctx.setLineDash([2, 3]);
  ctx.beginPath(); ctx.moveTo(0, y(last.c)); ctx.lineTo(plotW, y(last.c)); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = "#333333"; ctx.fillRect(plotW, y(last.c) - 7, 68, 14);
  ctx.fillStyle = "#dddddd"; ctx.fillText(fmtPrice(last.c), plotW + 4, y(last.c) + 3);
  const ticks = [...new Set([0, Math.floor((vis.length - 1) / 2), vis.length - 1])];
  let lastTimeLabelX = -Infinity;
  for (const i of ticks) {
    const labelX = Math.min(plotW - 32, Math.max(0, x(i) - 15));
    if (labelX - lastTimeLabelX < 42) continue;
    lastTimeLabelX = labelX;
    ctx.fillStyle = "#888888";
    ctx.fillText(new Date(vis[i].t).toISOString().slice(11, 16), labelX, H - 5);
  }
  if (cv._hover && cv._hover.x >= 0 && cv._hover.x < plotW && cv._hover.y >= 6 && cv._hover.y < 6+priceH) {
    const {x:hx,y:hy} = cv._hover;
    ctx.strokeStyle = "#888888";ctx.setLineDash([3,3]);
    ctx.beginPath();ctx.moveTo(hx,0);ctx.lineTo(hx,bottom);ctx.moveTo(0,hy);ctx.lineTo(plotW,hy);ctx.stroke();ctx.setLineDash([]);
    ctx.fillStyle = "#444444";ctx.fillRect(plotW,hy-7,68,14);
    ctx.fillStyle = "#eeeeee";ctx.fillText(fmtPrice(hi-(hy-6)/priceH*(hi-lo)),plotW+4,hy+3);
    const index = Math.floor(hx/cw) - (slots-vis.length);
    if (vis[index]) {
      ctx.fillStyle = "#333333";ctx.fillRect(Math.min(plotW-38,hx),bottom,38,20);
      ctx.fillStyle = "#eeeeee";ctx.fillText(new Date(vis[index].t).toISOString().slice(11,16),Math.min(plotW-35,hx+3),H-5);
    }
  }

}

function boardPages() {
  const rows = sortedRows();
  const per = S.gridSize * S.gridSize;
  const pages = Math.max(1, Math.ceil(rows.length / per));
  S.page = Math.min(S.page, pages - 1);
  return { slice: rows.slice(S.page * per, (S.page + 1) * per), pages };
}

let _boardSig = "";
let _boardTf = null;
function renderBoard() {
  const grid = $("board-grid");
  updateSortControl();
  const { slice, pages } = boardPages();
  $("page-ind").textContent = `${S.page + 1}/${pages}`;
  $("board-empty").style.display = slice.length ? "none" : "";
  $("board-empty").textContent = S.waking
    ? "сервер просыпается (free-тариф, до ~60 с)…"
    : S.symbols.length ? t("noMatch") : t("waiting");
  grid.style.gridTemplateColumns = `repeat(${S.gridSize}, 1fr)`;
  grid.style.gridTemplateRows = `repeat(${S.gridSize}, 1fr)`;
  const sig = `${slice.map((r) => r.symbol).join(",")}|${S.boardTf}`;
  if (sig !== _boardSig || _boardTf !== S.boardTf) {
    _boardSig = sig;
    _boardTf = S.boardTf;
    grid.innerHTML = slice.map((r) => `
      <div class="board-tile" data-sym="${esc(r.symbol)}" tabindex="0" role="button" aria-label="Focus: ${esc(r.symbol)}">
        <div class="tile-head">
          <span class="tile-id"><span class="star ${S.watch.has(r.symbol) ? "on" : ""}" data-star="${esc(r.symbol)}">${S.watch.has(r.symbol) ? "★" : "☆"}</span> <b>${esc(r.symbol.replace(/USDT$/, ""))}</b><em class="tile-venue">${esc(r.venue || r.exchange || "—")}</em></span>
          <span class="tc ${cls(r.change5m)}">${fmtPct(r.change5m, 1)}</span>
          <span class="ts ${cls(r.surge - 1)}">${r.surge != null ? `${r.surge.toFixed(1)}x` : ""}</span>
          <span class="score-num ${scoreClass(r.score)}">${r.score != null ? r.score.toFixed(0) : "—"}</span>
        </div>
        <div class="tile-sub mono"><span class="tp">${fmtPrice(r.price)}</span><span class="tile-volume dim">${fmtUsd(r.vol1m)}</span><span class="tag-cell ${tagClass(r.tag)}">${esc(r.tag ?? "")}</span></div>
        <div class="tile-canvas-wrap"><canvas></canvas></div>
      </div>`).join("");
  }
  for (const tile of grid.querySelectorAll(".board-tile")) {
    const sym = tile.dataset.sym;
    const r = S.bySym.get(sym);
    tile.draggable = !S.autoSort;
    tile.style.borderLeftColor = groupColor(sym);
    if (r) updateTileMetrics(tile, r);
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
      if (!tile.isConnected) return;
      const latest = S.bySym.get(sym);
      paintTile(tile.querySelector("canvas"), aggregate(candles, S.boardTf), isStale(latest || {}) ? null : latest?.price, sym, boardOverlays(latest));
    });
  }
}

function updateTileMetrics(tile, r) {
  const set = (selector, text, className) => {
    const el = tile.querySelector(selector);
    el.textContent = text;
    if (className) el.className = className;
  };
  set(".tp", fmtPrice(r.price));
  set(".tc", fmtPct(r.change5m, 1), `tc ${cls(r.change5m)}`);
  set(".ts", r.surge != null ? `${r.surge.toFixed(1)}x` : "—", `ts ${cls(r.surge - 1)}`);
  set(".score-num", r.score != null ? r.score.toFixed(0) : "—", `score-num ${scoreClass(r.score)}`);
  set(".tile-volume", fmtUsd(r.vol1m));
  set(".tag-cell", r.tag || "", `tag-cell ${tagClass(r.tag)}`);
  set(".tile-venue", r.venue || r.exchange || "—");
  set(".star", S.watch.has(r.symbol) ? "★" : "☆", `star ${S.watch.has(r.symbol) ? "on" : ""}`);
}

for (const event of ["pointerover", "pointermove", "pointerdown", "focusin", "wheel"]) {
  $("board-grid").addEventListener(event, pauseBoardInteraction, { passive: true });
}
$("board-grid").addEventListener("pointermove", e => {
  const cv = e.target.closest("canvas");
  if (!cv?._chartArgs) return;
  const rect = cv.getBoundingClientRect();cv._hover={x:e.clientX-rect.left,y:e.clientY-rect.top};
  paintTile(cv,...cv._chartArgs);
});
$("board-grid").addEventListener("pointerout", e => {
  const cv = e.target.closest("canvas");
  if (!cv?._chartArgs) return;
  cv._hover=null;paintTile(cv,...cv._chartArgs);
});
let draggedSymbol = null;
$("board-grid").addEventListener("dragstart", (e) => {
  const tile = e.target.closest(".board-tile");
  if (!tile || S.autoSort) { e.preventDefault(); return; }
  draggedSymbol = tile.dataset.sym;
  e.dataTransfer.setData("text/plain", draggedSymbol);
  e.dataTransfer.effectAllowed = "move";
});
$("board-grid").addEventListener("dragover", (e) => {
  if (!S.autoSort && draggedSymbol && e.target.closest(".board-tile")) e.preventDefault();
});
$("board-grid").addEventListener("drop", (e) => {
  const target = e.target.closest(".board-tile");
  if (!target || !draggedSymbol) return;
  e.preventDefault();
  if (moveBoardSymbol(draggedSymbol, target.dataset.sym)) { renderBoard(); renderCoinList(); }
  draggedSymbol = null;
});
$("board-grid").addEventListener("dragend", () => { draggedSymbol = null; });

$("board-grid").addEventListener("click", (e) => {
  const star = e.target.closest("[data-star]");
  if (star) {
    const sym = star.dataset.star;
    toggleGroupSymbol("watch",sym);
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
$("grid-size").addEventListener("change", (e) => { S.gridSize = +e.target.value; LS.set("grid", S.gridSize); S.page = 0; _boardSig = ""; renderBoard(); });
$("board-tf").addEventListener("change", (e) => { S.boardTf = +e.target.value; LS.set("boardTf", S.boardTf); S.page = 0; _boardSig = ""; renderBoard(); });
$("page-prev").addEventListener("click", () => { if (S.page > 0) { S.page -= 1; _boardSig = ""; renderBoard(); } });
$("page-next").addEventListener("click", () => { S.page += 1; _boardSig = ""; renderBoard(); });
// board repaint on its own cadence (2s) — decoupled from the 150ms light loop
setInterval(renderBoard, 2000);

// ------------------------- coin list (right sidebar) -------------------------
let _listSig = "";
function renderCoinList() {
  const el = $("tab-coins");
  if (S.sideTab !== "coins") return;
  const rows = sortedRows();
  document.querySelector(".count").textContent = `(${rows.length})`;
  if (!rows.length) {
    const msg = S.symbols.length ? t("noMatch") : S.waking
      ? "сервер просыпается (free-тариф, до ~60 с)…"
      : S.feed !== "—"
        ? "фид подключён — индикаторы прогреваются (~30–60 с)…"
        : t("waiting");
    setIfChanged(el, `<div class="empty-note">${msg}</div>`);
    return;
  }
  const html = rows.map((r) => {
    const ticker = r.symbol.replace(/USDT$/, "");
    const venue = r.venue || r.exchange || "—";
    const imb = r.imbalance ?? 0;
    const imbW = Math.min(Math.abs(imb) * 50, 50);
    const star = S.watch.has(r.symbol) ? "★" : "☆";
    const levelTxt = r.levelDist != null
      ? `${r.levelDist.toFixed(1)}%${r.levelKind === "resistance" ? "↑" : r.levelKind === "support" ? "↓" : "◎"}`
      : "";
    const stale = isStale(r) ? `<span class="tile-stale">${t("stale")}</span>` : "";
    return `
    <div class="coin-row" style="border-left:2px solid ${groupColor(r.symbol)}" data-sym="${esc(r.symbol)}" tabindex="0" role="button" aria-label="Focus: ${esc(r.symbol)}">
      <div class="cr-top">
        <span class="star ${S.watch.has(r.symbol) ? "on" : ""}" data-star="${esc(r.symbol)}">${star}</span>
        <b>${esc(ticker)}</b><em class="row-venue">${esc(venue)}</em>${stale}
        <span class="cr-tag tag-cell ${tagClass(r.tag)}">${esc(r.tag ?? "")}</span>
        <span class="cr-score mono ${scoreClass(r.score)}">${r.score != null ? r.score.toFixed(0) : ""}</span>
      </div>
      <div class="cr-bottom mono">
        <span class="cr-price">${fmtPrice(r.price)}</span>
        <span class="${cls(r.change5m)}">${fmtPct(r.change5m, 1)}</span>
        <span class="${cls(r.surge - 1)}">${r.surge != null ? `${r.surge.toFixed(1)}x` : ""}</span>
        <span class="dim">${fmtUsd(r.vol1m)}</span>
        <span class="cr-imb"><span class="imb-meter"><span class="imb-fill ${imb >= 0 ? "bid" : "ask"}" style="width:${imbW}%"></span></span>${levelTxt}</span>
      </div>
    </div>`;
  }).join("");
  setIfChanged(el, html);
}
$("tab-coins").addEventListener("click", (e) => {
  const star = e.target.closest("[data-star]");
  if (star) {
    const sym = star.dataset.star;
    toggleGroupSymbol("watch",sym);
    renderCoinList();
    return;
  }
  const row = e.target.closest(".coin-row");
  if (row) openChart(row.dataset.sym);
});
$("tab-coins").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const row = e.target.closest(".coin-row");
  if (row) { e.preventDefault(); openChart(row.dataset.sym); }
});

function setIfChanged(el, html) {
  if (el._sig !== html) { el._sig = html; el.innerHTML = html; }
}

// ------------------------- sidebar tabs -------------------------
document.querySelectorAll(".side-tab").forEach((tabBtn) => {
  tabBtn.addEventListener("click", () => {
    document.querySelectorAll(".side-tab").forEach((x) => x.classList.toggle("active", x === tabBtn));
    S.sideTab = tabBtn.dataset.tab;
    $("tab-coins").classList.toggle("hidden", S.sideTab !== "coins");
    $("tab-alerts").classList.toggle("hidden", S.sideTab !== "alerts");
    if (S.sideTab === "alerts") renderAlertsInline();
  });
});

function renderAlertsInline() {
  const el = $("alerts-inline");
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
  const items = S._notifItems || [];
  el.innerHTML = items.length
    ? items.map((n) => `
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
    renderAlertsInline();
    if (ws) ws.close();
  };
}

// ------------------------- notifications -------------------------
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (S.jwt) headers.Authorization = `Bearer ${S.jwt}`;
  return fetch(`${API_BASE}${path}`, { ...opts, headers });
}

async function pollNotifications() {
  if (!S.jwt) return;
  try {
    const res = await api("/api/v1/notifications");
    if (!res.ok) return;
    const body = await res.json();
    S._notifItems = body.notifications;
    const fresh = body.notifications.filter((n) => n.id > S._toastedId);
    if (fresh.length) {
      for (const n of fresh.slice(0, 3)) toast(n.message, "warn");
      S._toastedId = Math.max(...body.notifications.map((n) => n.id));
    }
    const unread = body.notifications.filter((n) => n.id > S.notifLastId).length;
    $("bell-count").textContent = unread;
    $("bell-count").classList.toggle("hidden", !unread);
    if (S.sideTab === "alerts") renderAlertsInline();
  } catch {}
}
setInterval(pollNotifications, 8000);

async function authFlow(path) {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
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
      if (ws) ws.close(); // reconnect carries the token → full feed
      pollNotifications();
    } else {
      toast("✓ " + t("signUp"));
      await authFlow("/api/v1/auth/login");
    }
  } catch { toast(t("loginError"), "err"); }
}

$("bell").addEventListener("click", () => {
  S.sideTab = "alerts";
  document.querySelectorAll(".side-tab").forEach((x) => x.classList.toggle("active", x.dataset.tab === "alerts"));
  $("tab-coins").classList.add("hidden");
  $("tab-alerts").classList.remove("hidden");
  $("settings-menu").classList.add("hidden");
  if (S._notifItems?.length) {
    S.notifLastId = Math.max(...S._notifItems.map((n) => n.id));
    LS.set("notifLastId", S.notifLastId);
    const unread = S._notifItems.filter((n) => n.id > S.notifLastId).length;
    $("bell-count").textContent = unread;
    $("bell-count").classList.toggle("hidden", !unread);
  }
  renderAlertsInline();
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#notif-panel") && !e.target.closest("#bell")) $("notif-panel").classList.add("hidden");
  if (!e.target.closest("#settings-menu") && !e.target.closest("#burger")) $("settings-menu").classList.add("hidden");
});

$("alert-create").addEventListener("click", async () => {
  if (!S.jwt) { toast(t("authNeeded"), "warn"); $("bell").click(); return; }
  const body = {
    symbol: chartSym,
    instrument_id: S.bySym.get(chartSym)?.instrument_id || undefined,
    rule_type: $("alert-type").value,
    threshold: parseFloat($("alert-threshold").value),
    cooldown_s: 300,
    recurring: true,
  };
  if (!body.threshold || body.threshold <= 0) { toast(t("thresholdNeeded"), "err"); return; }
  const res = await api("/api/v1/alerts", { method: "POST", body: JSON.stringify(body) });
  toast(res.ok ? `✓ ${t("alertCreated")}` : `${res.status}`, res.ok ? "" : "err");
});

// ------------------------- toolbar / burger -------------------------
$("burger").addEventListener("click", () => {
  const menu = $("settings-menu");
  menu.classList.toggle("hidden");
  if (!menu.classList.contains("hidden")) {
    $("set-feed").textContent = `${S.feed} · ${S.stats.tracked ?? 0}`;
  if (!S.connected) document.querySelectorAll(".tile-canvas-wrap canvas").forEach(cv => {
    if (cv._chartArgs) paintTile(cv,...cv._chartArgs);
  });
  }
});

$("sound-toggle").addEventListener("click", (e) => {
  S.soundOn = !S.soundOn;
  LS.set("sound", S.soundOn);
  e.target.textContent = S.soundOn ? t("soundOn") : t("soundOff");
  if (S.soundOn) ping();
});
$("lang-toggle").addEventListener("click", (e) => {
  S.lang = S.lang === "ru" ? "en" : "ru";
  LS.set("lang", S.lang);
  e.target.textContent = S.lang === "ru" ? "RU" : "EN";
  applyI18n();
});
$("min-score").addEventListener("change", (e) => { S.minScore = +e.target.value; LS.set("minScore", S.minScore); renderBoard(); renderCoinList(); });
$("wall-filter").addEventListener("change", (e) => { S.wallMin = +e.target.value; LS.set("wallMin", S.wallMin); });
function searchMarkets(query) {
  const normalized = query.toUpperCase().replace(/[\s/\-]/g, "");
  if (!normalized) return [];
  const all = new Map(S.catalog.map(row => [row.symbol, row]));
  for (const row of S.symbols) all.set(row.symbol, {...all.get(row.symbol), ...row});
  const exact = row => row.symbol === normalized || row.symbol.replace(/USDT$/, "") === normalized;
  return [...all.values()].filter(row => row.symbol.includes(normalized) || (row.name || "").toUpperCase().includes(normalized))
    .sort((a,b) => Number(exact(b)) - Number(exact(a)) || a.symbol.localeCompare(b.symbol)).slice(0, 30);
}

let catalogRequest = null;
async function loadSearchCatalog() {
  const epoch = S.dataEpoch;
  if (S.catalogAt && Date.now() - S.catalogAt < 30000) return;
  if (catalogRequest?.epoch === epoch) return catalogRequest.promise;
  const promise = (async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/markets/symbols`, {signal: AbortSignal.timeout(8000)});
      if (!res.ok) return;
      const body = await res.json();
      if (epoch !== S.dataEpoch) return;
      S.catalog = body.symbols || []; S.catalogAt = Date.now();
    } catch { /* Unfiltered snapshot symbols remain searchable offline. */ }
  })();
  catalogRequest = {epoch, promise};
  await promise;
  if (catalogRequest?.promise === promise) catalogRequest = null;
}

function closeSearch() {
  $("search-results").classList.add("hidden");
  $("search").setAttribute("aria-expanded", "false");
  $("search").removeAttribute("aria-activedescendant");
}

function renderSearch() {
  if (!S.searchQuery) { closeSearch(); return; }
  const rows = searchMarkets(S.searchQuery);
  S.searchIndex = Math.max(0, Math.min(S.searchIndex, rows.length - 1));
  $("search-results").innerHTML = rows.length ? rows.map((row,i) => `<button type="button" role="option" tabindex="-1" id="search-option-${i}" aria-selected="${i === S.searchIndex}" data-search-symbol="${esc(row.symbol)}"><b>${esc(row.symbol)}</b><span>${esc(row.venue || "—")}</span><span>${fmtPrice(row.price)}</span>${S.hidden.has(row.symbol) ? `<small>${S.lang === "ru" ? "Скрыт с доски" : "Hidden from board"}</small>` : ''}</button>`).join("") : `<p>${S.lang === "ru" ? "Нет совпадений в доступном каталоге" : "No matches in the available catalog"}</p>`;
  $("search-results").classList.remove("hidden");
  $("search").setAttribute("aria-expanded", "true");
  if (rows.length) $("search").setAttribute("aria-activedescendant", `search-option-${S.searchIndex}`);
  else $("search").removeAttribute("aria-activedescendant");
}

$("search").addEventListener("input", async e => {
  S.searchQuery = e.target.value.trim(); S.searchIndex = 0;
  renderSearch(); await loadSearchCatalog();
  if (document.activeElement === $("search")) renderSearch();
});
$("search").addEventListener("focus", async () => {
  renderSearch(); await loadSearchCatalog();
  if (document.activeElement === $("search")) renderSearch();
});
$("search").addEventListener("keydown", e => {
  if (e.key === "Escape") { closeSearch(); e.stopPropagation(); return; }
  const rows = searchMarkets(S.searchQuery);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault(); S.searchIndex = (S.searchIndex + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % Math.max(1, rows.length); renderSearch();
    document.getElementById(`search-option-${S.searchIndex}`)?.scrollIntoView({block:"nearest"});
  }
  if (e.key === "Enter" && rows[S.searchIndex]) { e.preventDefault(); closeSearch(); openChart(rows[S.searchIndex].symbol); }
});
$("search-results").addEventListener("click", e => {
  const result = e.target.closest("[data-search-symbol]");
  if (result) { closeSearch(); openChart(result.dataset.searchSymbol); }
});
document.addEventListener("click", e => {
  if (!e.target.closest(".search-wrap") && !e.target.closest("#search-results")) closeSearch();
});

$("sort-by").addEventListener("change", (e) => { S.sortKey = e.target.value; LS.set("sort", S.sortKey); renderBoard(); renderCoinList(); });
document.querySelectorAll(".venue-chip").forEach((btn) => btn.addEventListener("click", () => {
  document.querySelectorAll(".venue-chip").forEach((x) => x.classList.toggle("active", x === btn));
  S.venue = btn.textContent.trim(); S.page = 0; _boardSig = ""; renderBoard(); renderCoinList();
}));
document.querySelector(".auto-chip")?.addEventListener("click", (e) => {
  setAutoSort(!S.autoSort);
  renderBoard(); renderCoinList();
});
document.querySelectorAll(".view-chip").forEach((btn, index) => btn.addEventListener("click", () => {
  document.querySelectorAll(".view-chip").forEach((x, i) => x.classList.toggle("active", i === index));
  document.querySelector(".layout")?.classList.toggle("list-mode", index === 1);
  _boardSig = ""; renderBoard();
}));

// ------------------------- focus modal -------------------------
const modal = $("chart-modal");
let chartSym = null;
let focusReturn = null;

const chart = {
  index: 0, candles: [], series: [], tfMin: 1, sourceTf: 1, historyStatus: "", walls: [], levels: [],
  offset: 0, visible: 120, hover: null,
  dragging: false, dragStartX: 0, dragStartOffset: 0, followRight: true,
};

const focusPanes = [chart, ...[1440,5,60].map((tf,index) => ({...chart,index:index+1,tfMin:tf,sourceTf:tf,candles:[],series:[],walls:[],levels:[]}))];
function paneCanvas(pane) { return $(pane.index ? `chart-canvas-${pane.index}` : "chart-canvas"); }
let drawingTool = "none", drawingDraft = null;
function drawingKey() { return `drawings:${chartSym || ""}`; }
function loadDrawings() { return LS.get(drawingKey(), []); }
function saveDrawings(rows) {
  LS.set(drawingKey(), rows);
  if (S.jwt && chartSym) api(`/api/v1/drawings/${encodeURIComponent(chartSym)}`, {method:"PUT", body:JSON.stringify({drawings:rows})}).catch(()=>{});
}
async function loadServerDrawings(symbol) {
  if (!S.jwt || !symbol) return;
  try { const r=await api(`/api/v1/drawings/${encodeURIComponent(symbol)}`); if(!r.ok)return; const b=await r.json(); LS.set(`drawings:${symbol}`,b.drawings||[]); drawChart(); } catch {}
}
function setDrawingTool(tool) { drawingTool=tool; drawingDraft=null; document.querySelectorAll(".draw-btn").forEach(b=>b.classList.toggle("active",b.dataset.draw===tool)); drawChart(); }
document.querySelectorAll(".draw-btn").forEach(b=>b.addEventListener("click",()=>setDrawingTool(b.dataset.draw)));
$("draw-undo").addEventListener("click",()=>{const d=loadDrawings();d.pop();saveDrawings(d);drawChart();});
$("draw-clear").addEventListener("click",()=>{saveDrawings([]);drawingDraft=null;drawChart();});
function drawingAnchor(chart,x,y,cv) {
  const plotW=cv.clientWidth-AXIS_W, idx=Math.max(0,Math.min(chart.series.length-1,chart.offset+Math.floor(x/(plotW/chart.visible))));
  const c=chart.series[idx]; if(!c)return null;
  const vis=chart.series.slice(chart.offset,chart.offset+chart.visible); const lo=Math.min(...vis.map(q=>q.l)),hi=Math.max(...vis.map(q=>q.h)); const pad=(hi-lo)*.07||hi*.0012;
  return {t:c.t,p:lo-pad+(1-(y-10)/(Math.max(1,cv.clientHeight-TIME_H-VOL_H-16)))*(hi-lo+2*pad)};
}

function updateFocusLayout() {
  $("focus-crosshair-sync").checked=S.syncCrosshair;
  $("focus-range-sync").checked=S.syncRange;
  document.querySelectorAll(".pane-overlays input").forEach(input=>{ const k=input.className.replace("pane-overlay-",""); input.checked=paneOverlay(focusPanes[Number(input.dataset.pane)],k); });
  $("focus-layout").value=String(S.focusLayout);
  $("focus-grid").dataset.layout=String(S.focusLayout);
  document.querySelectorAll(".focus-pane").forEach(el => el.classList.toggle("hidden",Number(el.dataset.pane)>=S.focusLayout));
}
function focusTimeAt(pane,x,width) {
  const plotWidth=width-74;
  if (x<0 || x>=plotWidth || plotWidth<=0) return null;
  return pane.series[pane.offset+Math.floor(x/(plotWidth/pane.visible))]?.t ?? null;
}
function syncFocusCursor(origin,timestamp) {
  for (const pane of focusPanes) if (pane!==origin) pane.syncedTime=S.syncCrosshair?timestamp:null;
}
$("focus-crosshair-sync").addEventListener("change",e=>{
  S.syncCrosshair=e.target.checked;LS.set("syncCrosshair",S.syncCrosshair);
  for (const pane of focusPanes) pane.syncedTime=null;
  drawChart();
});
$("focus-range-sync").addEventListener("change",e=>{ S.syncRange=e.target.checked; LS.set("syncRange",S.syncRange); });
function syncFocusRange(origin) {
  if (!S.syncRange || !origin.series.length) return;
  const anchor=origin.series[origin.offset]?.t;
  if (anchor==null) return;
  for (const pane of focusPanes.slice(0,S.focusLayout)) if (pane!==origin && pane.series.length) {
    pane.visible=Math.max(25,Math.min(300,origin.visible));
    const idx=pane.series.findIndex(c=>c.t>=anchor);
    pane.offset=clampOffset(idx<0?pane.series.length-pane.visible:idx,pane);
    pane.followRight=pane.offset>=pane.series.length-pane.visible;
  }
}
function refreshFocus(reset=false) {
  return Promise.all(focusPanes.slice(0,S.focusLayout).map(pane=>refreshChart(reset,pane)));
}
$("focus-layout").addEventListener("change",e=>{
  S.focusLayout=Number(e.target.value);LS.set("focusLayout",S.focusLayout);updateFocusLayout();refreshFocus(true);drawChart();
});
document.querySelectorAll(".pane-overlays input").forEach(input=>input.addEventListener("change",e=>{
  const i=String(e.target.dataset.pane), kind=e.target.className.replace("pane-overlay-","");
  S.paneOverlays[i]={...(S.paneOverlays[i]||{}),[kind]:e.target.checked}; LS.set("paneOverlays",S.paneOverlays); drawChart();
}));
function paneOverlay(pane,kind){ return S.paneOverlays[String(pane.index)]?.[kind] !== false; }
for (const pane of focusPanes.slice(1)) {
  $(`focus-tf-${pane.index}`).addEventListener("change",e=>{
    pane.tfMin=Number(e.target.value);pane.candles=[];pane.series=[];pane.walls=[];pane.levels=[];pane.visible=120;pane.offset=0;pane.followRight=true;
    refreshChart(true,pane);drawChart();
  });
}

function clampOffset(n, chart = focusPanes[0]) {
  const max = Math.max(0, chart.series.length - chart.visible);
  return Math.max(0, Math.min(max, n));
}

function timeframeName(minutes) {
  return {1:"1m",3:"3m",5:"5m",15:"15m",30:"30m",60:"1h",240:"4h",1440:"1D"}[minutes];
}
function rebuildSeries(chart = focusPanes[0]) {
  chart.series = chart.sourceTf === chart.tfMin ? chart.candles : [];
}
function setHistoryStatus(text, chart = focusPanes[0]) {
  chart.historyStatus = text; $(chart.index ? `chart-history-status-${chart.index}` : "chart-history-status").textContent = text;
}

async function openChart(symbol) {
  if (!S.focusOpen) focusReturn = document.activeElement;
  S.focusOpen = true;
  chartSym = symbol;
  setDrawingTool("none");
  loadServerDrawings(symbol);
  updateHiddenControl();
  updateChartGroupControl();
  const r = S.bySym.get(symbol);
  const pick = S.picks.find((p) => p.symbol === symbol);
  $("chart-title").textContent = `${symbol} · ${timeframeName(chart.tfMin)}`;
  $("chart-tag").textContent = pick?.tag || r?.tag || "—";
  $("chart-tag").className = `tag-cell ${tagClass(pick?.tag || r?.tag)}`;
  $("chart-thesis").textContent = pick?.thesis || r?.thesis || "—";
  $("alert-symbol").textContent = symbol;
  modal.classList.remove("hidden");
  $("chart-close").focus();
  for (const pane of focusPanes) { pane.candles=[];pane.series=[];pane.walls=[];pane.levels=[];pane.visible=120;pane.offset=0;pane.followRight=true;pane.hover=null;pane.syncedTime=null; }
  updateFocusLayout();
  drawChart();
  await refreshFocus(true);
}

async function refreshChart(resetView = false, chart = focusPanes[0]) {
  const symbol = chartSym, epoch = S.dataEpoch, tf = chart.tfMin;
  const requestId = chart.requestId = (chart.requestId || 0) + 1;
  if (!chartSym || modal.classList.contains("hidden")) return;
  if (resetView) setHistoryStatus(S.lang === "ru" ? "Загрузка истории…" : "Loading history…", chart);
  try {
    const res = await fetch(`${API_BASE}/api/v1/markets/${encodeURIComponent(chartSym)}/candles?limit=300&timeframe=${timeframeName(tf)}`, { cache: "no-store", signal: AbortSignal.timeout(20000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json();
    if (requestId !== chart.requestId || symbol !== chartSym || epoch !== S.dataEpoch || tf !== chart.tfMin || chart.index >= S.focusLayout) return;
    chart.sourceTf = tf;
    setHistoryStatus(`${body.venue || "—"} · ${body.timeframe || timeframeName(tf)} · ${(body.candles || []).length} bars${body.limitedHistory ? " · " + (S.lang === "ru" ? "ограниченная история потока" : "limited stream history") : ""} · ${S.lang === "ru" ? "Уровни: 1м" : "Levels: 1m"}`, chart);
    chart.candles = body.candles || [];
    rebuildSeries(chart);
    chart.walls = body.walls || [];
    chart.levels = body.levels || [];
    chart.visible = Math.min(chart.visible, Math.max(25, chart.series.length));
    chart.offset = clampOffset(resetView || chart.followRight ? chart.series.length-chart.visible : chart.offset, chart);
    drawChart();
  } catch {
    if (requestId !== chart.requestId || symbol !== chartSym || epoch !== S.dataEpoch || tf !== chart.tfMin || chart.index >= S.focusLayout) return;
    setHistoryStatus(S.lang === "ru" ? "История недоступна · повтор через 5с" : "History unavailable · retry in 5s", chart);
    drawChart();
  }
}

function closeChart() {
  if (!S.focusOpen) return;
  if (document.fullscreenElement) document.exitFullscreen().catch(()=>{});
  modal.classList.add("hidden"); chartSym = null; S.focusOpen = false;
  if (focusReturn?.isConnected) focusReturn.focus();
  else $("search").focus();
}
function updateChartGroupControl() {
  const id=$("chart-group").value;
  const included=id==="watch"?S.watch.has(chartSym):S.groups.find(g=>g.id===id)?.symbols.includes(chartSym);
  $("chart-group-toggle").textContent=S.lang==="ru"?(included?"Убрать из группы":"В группу"):(included?"Remove from group":"Add to group");
}
$("chart-group").addEventListener("change",updateChartGroupControl);
$("chart-group-toggle").addEventListener("click",()=>{
  if (!chartSym) return;
  toggleGroupSymbol($("chart-group").value,chartSym);updateChartGroupControl();renderBoard();renderCoinList();
});
function updateHiddenControl() {
  $("chart-hide").textContent = S.hidden.has(chartSym)
    ? (S.lang === "ru" ? "Вернуть на доску" : "Show on board")
    : (S.lang === "ru" ? "Скрыть с доски" : "Hide from board");
}
$("chart-hide").addEventListener("click", () => {
  if (!chartSym) return;
  if (S.hidden.has(chartSym)) S.hidden.delete(chartSym); else S.hidden.add(chartSym);
  LS.set("hidden", [...S.hidden]); updateHiddenControl(); renderBoard(); renderCoinList();
});
$("chart-fullscreen").addEventListener("click", async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await modal.requestFullscreen();
  } catch { modal.classList.toggle("focus-fullscreen"); }
});
document.addEventListener("fullscreenchange", () => {
  const active=Boolean(document.fullscreenElement); modal.classList.toggle("focus-fullscreen",active);
  $("chart-fullscreen").textContent=active ? (S.lang === "ru" ? "Выйти из полноэкранного" : "Exit fullscreen") : (S.lang === "ru" ? "На весь экран" : "Fullscreen");
  drawChart();
});
$("chart-close").addEventListener("click", closeChart);
modal.addEventListener("click", (e) => { if (e.target === modal) closeChart(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeChart(); return; }
  if (!S.focusOpen || modal.classList.contains("hidden")) return;
  const tag=e.target?.tagName?.toLowerCase();
  if (tag === "input" || tag === "select" || tag === "textarea") return;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") { e.preventDefault(); $("draw-undo").click(); return; }
  const shortcuts={h:"horizontal",t:"trend",r:"rectangle",f:"fib",m:"ruler",p:"pencil",v:"none"};
  const tool=shortcuts[e.key.toLowerCase()]; if (tool) { e.preventDefault(); setDrawingTool(tool); }
});

for (const chart of focusPanes) {
  const cv = paneCanvas(chart);
  const plotW = () => cv.clientWidth - AXIS_W;
  cv.style.cursor = "grab";

  cv.addEventListener("mousedown", (e) => {
    if (drawingTool === "pencil") {
      const r=cv.getBoundingClientRect(), a=drawingAnchor(chart,e.clientX-r.left,e.clientY-r.top,cv);
      if (a) { drawingDraft={type:"pencil",points:[a],pane:chart.index}; chart.drawingPencil=true; }
      return;
    }
    chart.dragging = true;
    const rect = cv.getBoundingClientRect();
    chart.dragStartX = e.clientX - rect.left;
    chart.dragStartOffset = chart.offset;
    cv.style.cursor = "grabbing";
  });
  window.addEventListener("mouseup", () => {
    if (chart.drawingPencil && drawingDraft?.type === "pencil") {
      if (drawingDraft.points.length > 1) { const d=loadDrawings(); d.push({type:"pencil",points:drawingDraft.points}); saveDrawings(d); }
      drawingDraft=null; chart.drawingPencil=false; drawChart();
    }
    chart.dragging = false;
    if (!modal.classList.contains("hidden")) cv.style.cursor = "grab";
  });
  cv.addEventListener("mousemove", (e) => {
    const rect = cv.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    if (chart.drawingPencil && drawingDraft?.type === "pencil") {
      const a=drawingAnchor(chart,x,y,cv); if(a) drawingDraft.points.push(a); drawChart(); return;
    }
    if (chart.dragging) {
      const cw = plotW() / chart.visible;
      const shift = Math.round((chart.dragStartX - x) / cw);
      chart.offset = clampOffset(chart.dragStartOffset + shift, chart);
      chart.followRight = chart.offset >= chart.series.length - chart.visible;
      syncFocusRange(chart);
    }
    chart.hover = { x, y };
    syncFocusCursor(chart,focusTimeAt(chart,x,cv.clientWidth));
    drawChart();
  });
  cv.addEventListener("click", (e) => {
    if (drawingTool === "none" || drawingTool === "pencil" || !chart.series.length) return;
    const r=cv.getBoundingClientRect(), a=drawingAnchor(chart,e.clientX-r.left,e.clientY-r.top,cv); if(!a)return;
    if (drawingTool === "horizontal") { const d=loadDrawings(); d.push({type:"horizontal",a}); saveDrawings(d); }
    else if (!drawingDraft) drawingDraft={type:drawingTool,a,pane:chart.index};
    else { const d=loadDrawings(); d.push({type:drawingTool,a:drawingDraft.a,b:a}); saveDrawings(d); drawingDraft=null; }
    drawChart();
  });
  cv.addEventListener("mouseleave", () => { chart.hover = null; chart.dragging = false; syncFocusCursor(chart,null); drawChart(); });
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
    chart.offset = clampOffset(anchor - Math.floor(mx / cw2), chart);
    chart.followRight = chart.offset >= chart.series.length - chart.visible;
    syncFocusRange(chart);
    drawChart();
  }, { passive: false });
}
setInterval(() => { if (!modal.classList.contains("hidden") && chartSym) refreshFocus(); }, 5000);

$("tf-picker").addEventListener("click", (e) => {
  const b = e.target.closest(".tf-btn");
  if (!b) return;
  chart.tfMin = +b.dataset.tf;
  document.querySelectorAll("#tf-picker .tf-btn").forEach((x) => x.classList.toggle("active", x === b));
  chart.candles = []; chart.series = []; chart.walls = []; chart.levels = [];
  if (chartSym) $("chart-title").textContent = `${chartSym} · ${timeframeName(chart.tfMin)}`;
  chart.visible = 120;
  chart.offset = clampOffset(chart.series.length - chart.visible);
  chart.followRight = true;
  refreshChart(true);
  drawChart();
});

const AXIS_W = 74, TIME_H = 26, VOL_H = 56;
const UP = "#31976b", DOWN = "#c95d63";

function fitCanvas(cv, logicalH) {
  const dpr = window.devicePixelRatio || 1;
  const cssW = Math.max(200, cv.clientWidth || cv.width / dpr);
  const bw = Math.round(cssW * dpr), bh = Math.round(logicalH * dpr);
  if (cv.width !== bw || cv.height !== bh) { cv.width = bw; cv.height = bh; }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, W: cssW, H: logicalH };
}

function drawChart() {
  if (!chartSym || modal.classList.contains("hidden")) return;
  for (const pane of focusPanes.slice(0,S.focusLayout)) drawFocusPane(pane,paneCanvas(pane));
}
function drawFocusPane(chart,cv) {
  const { ctx, W, H } = fitCanvas(cv, S.focusLayout === 4 ? 260 : S.focusLayout === 2 ? 380 : 460);
  ctx.fillStyle = "#101318";
  ctx.fillRect(0, 0, W, H);
  ctx.font = "10px 'JetBrains Mono', monospace";
  const plotW = W - AXIS_W;
  const priceTop = 10, priceH = H - TIME_H - VOL_H - priceTop - 6;

  const data = chart.series;
  if (!data.length) {
    ctx.fillStyle = "#7b8491";
    ctx.fillText(chart.historyStatus || "No candle history", 20, H / 2);
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
    ctx.fillText(chart.tfMin >= 240 ? d.toISOString().slice(5,10) : `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`, Math.max(25, Math.min(plotW-25,gx)), H - 8);
  }

  ctx.textAlign = "left";
  const vmax = Math.max(...vis.map((c) => c.v), 1e-9);
  if (paneOverlay(chart,"volume")) for (let i = 0; i < vis.length; i++) {
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

  // Level overlays: quality over quantity — only the 3 strongest (touches),
  // opacity/width proportional to relative strength, label only the strongest.
  const maxTouches = Math.max(1, ...chart.levels.map((z) => z.touches));
  const strongest = [...chart.levels]
    .sort((a, b) => b.touches - a.touches)
    .slice(0, 3);
  if (paneOverlay(chart,"levels")) strongest.forEach((z, rank) => {
    if (z.high < lo || z.low > hi) return;
    const my = y(z.mid);
    const strength = z.touches / maxTouches;          // 0..1
    const distRank = rank;                            // 0 = strongest
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = 1 + strength * 1.5;               // proportional width
    ctx.strokeStyle = z.kind === "resistance"
      ? `rgba(201,93,99,${0.3 + strength * 0.4})`
      : `rgba(49,151,107,${0.3 + strength * 0.4})`;
    ctx.beginPath(); ctx.moveTo(0, my); ctx.lineTo(plotW, my); ctx.stroke();
    ctx.setLineDash([]);
    if (distRank === 0) {                             // label only the strongest
      ctx.fillStyle = "rgba(152,161,173,0.95)";
      ctx.fillText(`${t(z.kind)} ×${z.touches}`, 6, my - 4);
    }
  });

  if (paneOverlay(chart,"walls")) for (const w of chart.walls) {
    if (w.price < lo || w.price > hi) continue;
    const wy = y(w.price);
    ctx.setLineDash([2, 5]);
    ctx.strokeStyle = w.side === "bid" ? "rgba(49,151,107,0.45)" : "rgba(201,93,99,0.45)";
    ctx.beginPath(); ctx.moveTo(0, wy); ctx.lineTo(plotW, wy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = w.side === "bid" ? UP : DOWN;
    ctx.fillText(`${w.side === "bid" ? "bid wall" : "ask wall"} ${fmtUsd(w.notional)}${w.age_s != null ? ` · ${Math.round(w.age_s)}s` : ""}`, 6, wy + 11);
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

  if (!chart.hover && chart.syncedTime != null) {
    const idx=vis.findIndex(c=>chart.syncedTime>=c.t && chart.syncedTime<c.t+chart.tfMin*60000);
    if (idx>=0) {
      ctx.strokeStyle="#888888";ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(cx(idx),priceTop);ctx.lineTo(cx(idx),H-TIME_H);ctx.stroke();ctx.setLineDash([]);
    }
  }
  // User drawings use timestamp/price anchors, so they remain meaningful across panes and timeframes.
  const drawings=loadDrawings();
  const xFor=t=>{const i=vis.findIndex(c=>t>=c.t&&t<c.t+chart.tfMin*60000); return i<0?null:cx(i);};
  const yFor=p=>y(p);
  ctx.lineWidth=1.5; ctx.strokeStyle="#a78bfa"; ctx.fillStyle="rgba(167,139,250,.10)";
  for(const d of drawings){ const x1=xFor(d.a.t), y1=yFor(d.a.p); if(y1<priceTop||y1>priceTop+priceH) continue;
    if(d.type==="horizontal"){ctx.setLineDash([6,4]);ctx.beginPath();ctx.moveTo(0,y1);ctx.lineTo(plotW,y1);ctx.stroke();ctx.setLineDash([]);}
    else if(d.type==="pencil" && d.points){ const pts=d.points.map(q=>[xFor(q.t),yFor(q.p)]).filter(q=>q[0]!=null); if(pts.length>1){ctx.beginPath();ctx.moveTo(pts[0][0],pts[0][1]);for(const q of pts.slice(1))ctx.lineTo(q[0],q[1]);ctx.stroke();} }
    else if(d.b){ const x2=xFor(d.b.t),y2=yFor(d.b.p); if(x2==null)continue; if(d.type==="fib"){ctx.setLineDash([4,3]);const levels=[0,.236,.382,.5,.618,.786,1];for(const f of levels){const yy=y1+(y2-y1)*f;ctx.beginPath();ctx.moveTo(Math.min(x1,x2),yy);ctx.lineTo(Math.max(x1,x2),yy);ctx.stroke();ctx.fillText(`${Math.round(f*100)}%`,Math.min(x1,x2)+4,yy-3);}ctx.setLineDash([]);} else {ctx.setLineDash(d.type==="trend"||d.type==="ruler"?[]:[4,3]); if(d.type==="rectangle")ctx.strokeRect(Math.min(x1,x2),Math.min(y1,y2),Math.abs(x2-x1),Math.abs(y2-y1)); else {ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();} if(d.type==="ruler"){const pct=((d.b.p/d.a.p)-1)*100;ctx.fillText(`${pct.toFixed(2)}% · ${Math.abs(d.b.t-d.a.t)/60000|0}m`,Math.min(x1,x2)+5,Math.min(y1,y2)-6);} ctx.setLineDash([]); } }
  }

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
}

// ------------------------- cold-start wake-up (Render free tier) -------------------------
// The free backend sleeps after 15 idle minutes. While it is down we poll
// /health every 5s and show an explicit "waking up" status, so the page never
// looks broken — it renders instantly and fills in when the server wakes.
S.waking = false;
setInterval(() => {
  if (S.symbols.length > 0) {
    if (S.waking) { S.waking = false; renderBoard(); }
    return;
  }
  S.waking = true;
  fetch(`${API_BASE}/api/v1/health`, { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((h) => {
      if (h && (h.tracked_symbols ?? 0) > 0 && S.symbols.length === 0) {
        if (!ws || ws.readyState > 1) { wsAttempt = 0; connect(); }
        renderBoard(); renderCoinList();
      }
    })
    .catch(() => {});
  renderBoard();
  renderCoinList();
}, 5000);

// ------------------------- i18n -------------------------
function applyI18n() {
  document.documentElement.lang = S.lang;
  const setMap = { "set-feed-label": "feed", "set-sound-label": "soundLabel", "set-lang-label": "langLabel",
                   "set-score-label": "minScoreLabel", "set-wall-label": "minWallLabel" };
  for (const [id, key] of Object.entries(setMap)) {
    const el = $(id);
    if (el) el.textContent = t(key);
  }
  $("bell").childNodes.forEach((n) => { if (n.nodeType === 3) n.textContent = t("alertsBtn"); });
  $("search").placeholder = t("searchPh");
  $("chart-close").textContent = t("close");
  $("sound-toggle").textContent = S.soundOn ? t("soundOn") : t("soundOff");
  $("alert-create").textContent = t("createAlert");
  $("alert-threshold").placeholder = S.lang === "ru" ? "порог" : "threshold";
  const typeSel = $("alert-type");
  const ruleKeys = ["priceCrossAbove", "priceCrossBelow", "pctMove", "volumeSurge", "scoreAbove", "cascadeDistance", "densityAppeared"];
  const ruleVals = ["price_cross_above", "price_cross_below", "pct_move", "volume_surge", "score_above", "cascade_distance", "density_appeared"];
  const prevSel = typeSel.value;
  typeSel.innerHTML = ruleKeys.map((k, i) => `<option value="${ruleVals[i]}">${t(k)}</option>`).join("");
  typeSel.value = prevSel;
  const sortSel = $("sort-by");
  const sortPrev = sortSel.value;
  const sortDefs = [["score", "Скоринг", "Score"], ["surge", "Всплеск", "Surge"], ["vol1m", "Объём 1м", "1m Volume"],
                    ["vol24h", "Объём 24ч · USDT", "24h volume · USDT"], ["natr", "NATR(14) · 1м · %", "NATR(14) · 1m · %"], ["range5m", "Range · 5м · %", "Range · 5m · %"], ["speed", "Скорость · %/мин", "Speed · %/min"], ["change5m", "5м %", "5m %"], ["levelDist", "До уровня", "To level"]];
  sortSel.innerHTML = sortDefs.map(([v, ru, en]) => `<option value="${v}">${S.lang === "ru" ? ru : en}</option>`).join("");
  sortSel.value = sortPrev;
  const tabBtns = document.querySelectorAll(".side-tab");
  if (tabBtns[0]) tabBtns[0].textContent = t("coins");
  if (tabBtns[1]) tabBtns[1].textContent = t("alerts");
  $("show-levels").checked = S.showLevels; $("show-walls").checked = S.showWalls;
  $("levels-label").textContent = S.lang === "ru" ? "Уровни" : "Levels";
  $("walls-label").textContent = S.lang === "ru" ? "Плотности" : "Densities";
  renderGroups();
  renderMarketFilters();
  $("sort-direction").textContent = S.sortAscending ? "↑" : "↓";
  renderCoinList();
  renderAlertsTabIfVisible();
}

function renderAlertsTabIfVisible() {
  if (S.sideTab === "alerts") renderAlertsInline();
}

// ------------------------- boot -------------------------
$("min-score").value = String(S.minScore);
$("wall-filter").value = String(S.wallMin);
$("grid-size").value = String(S.gridSize);
$("board-tf").value = String(S.boardTf);
$("sort-by").value = S.sortKey;
$("sound-toggle").textContent = S.soundOn ? t("soundOn") : t("soundOff");
$("lang-toggle").textContent = S.lang === "ru" ? "RU" : "EN";

applyI18n();
pollNotifications();

// single light loop (150ms): coin list + focus chart; board repaints on its own 2s cadence
setInterval(() => {
  renderCoinList();
  drawChart();
}, 150);
setInterval(() => { $("clock").textContent = new Date().toISOString().slice(11, 19); }, 1000);

connect();

setInterval(() => { if (S.lastSnapshotAt && Date.now() - S.lastSnapshotAt > 30000) setFeedStatus("reconnecting"); }, 1000);

window.addEventListener("resize", drawChart);
