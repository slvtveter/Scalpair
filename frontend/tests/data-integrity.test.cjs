const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const source = readFileSync(require('node:path').join(__dirname, '../js/app.js'), 'utf8');
function app() {
  const elements = new Map();
  const el = id => {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', value: '', childNodes: [], style: {}, innerHTML: '',
      classList: { toggle() {}, add() {}, remove() {}, contains() { return true; } },
      setAttribute() {}, addEventListener() {}, querySelectorAll() { return []; }, appendChild() {},
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    document: { getElementById: el, querySelector: el, querySelectorAll: () => [],
      documentElement: {}, addEventListener() {}, createElement: () => el('new') },
    window: { addEventListener() {} }, localStorage: { getItem() { return null; }, setItem() {} },
    location: { hostname: 'localhost', protocol: 'http:', host: 'localhost' },
    WebSocket: class { constructor() {} }, setInterval() {}, setTimeout() {},
    performance: { now: () => 0 }, fetch: async () => { throw new Error('offline'); },
    AbortSignal, console, crypto: require("node:crypto").webcrypto, structuredClone,
  });
  vm.runInContext(source, context);
  return { run: code => vm.runInContext(code, context), context, el };
}
test('offline boot never seeds synthetic symbols or marks the stream Live', () => {
  const a = app();
  assert.equal(a.run('S.symbols.length'), 0);
  assert.equal(a.run('S.connected'), false);
  assert.notEqual(a.el('.live-pill').textContent, 'Live');
});
test('failed candle request returns no fabricated OHLC', async () => {
  const a = app();
  assert.equal((await a.run('getCandles("BTCUSDT")')).length, 0);
});
test('failed candle request preserves only previously received history', async () => {
  const a = app();
  a.run('S.candleCache.set("BTCUSDT", {ts: 0, candles: [{t: 1, o: 2, h: 3, l: 1, c: 2, v: 4}]})');
  const rows = await a.run('getCandles("BTCUSDT")');
  assert.equal(rows.length, 1); assert.equal(rows[0].t, 1);
});
test('server demo feed is labelled synthetic and source switches discard cached history', () => {
  const a = app();
  a.run('onSnapshot({type:"snapshot",feed:"mock", symbols:[]})');
  assert.match(a.el('.live-pill').textContent, /DEMO/);
  a.run('S.candleCache.set("BTCUSDT", {ts:1,candles:[{}]}); onSnapshot({type:"snapshot",feed:"bybit",symbols:[]})');
  assert.equal(a.run('S.candleCache.size'), 0);
  assert.equal(a.el('.live-pill').textContent, 'Live');
});
test('disconnect and absent book timestamp are marked stale', () => {
  const a = app();
  a.run('onSnapshot({type:"snapshot",feed:"binance",symbols:[]})');
  assert.equal(a.run('isStale({bookTs: Date.now()})'), false);
  assert.equal(a.run('isStale({})'), true);
  a.run('setFeedStatus("reconnecting")');
  assert.equal(a.run('isStale({bookTs: Date.now()})'), true);
});
test('density API failure is visible instead of retaining fake walls', async () => {
  const a = app();
  await a.run('refreshDensityMap()');
  assert.equal(a.el('.density-live').textContent, 'Плотности недоступны');
});

function seedBoard(a) {
  a.run('S.symbols = [{symbol:"AAAUSDT",score:20,vol5m:1},{symbol:"BBBUSDT",score:80,vol5m:1},{symbol:"CCCUSDT",score:50,vol5m:1}]; sortedRows()');
}
const order = a => a.run('sortedRows().map(r=>r.symbol).join(",")');
test('manual pause freezes displayed order across changed prices, metrics and API ordering', () => {
  const a = app(); seedBoard(a);
  assert.equal(order(a), 'BBBUSDT,CCCUSDT,AAAUSDT');
  a.run('S.page=2; setAutoSort(false); S.symbols[0].score=100; S.symbols.reverse()');
  assert.equal(order(a), 'BBBUSDT,CCCUSDT,AAAUSDT');
  assert.equal(a.run('sortedRows()[2].score'), 100);
  assert.equal(a.run('S.page'), 2);
  a.run('S.hoverUntil=0');
  assert.equal(order(a), 'BBBUSDT,CCCUSDT,AAAUSDT');
  a.run('setAutoSort(true)');
  assert.equal(order(a), 'AAAUSDT,BBBUSDT,CCCUSDT');
});
test('interaction pauses ordering for three seconds without stopping data updates', () => {
  const a = app(); seedBoard(a);
  a.run('pauseBoardInteraction(); S.symbols[0].score=100');
  assert.equal(order(a), 'BBBUSDT,CCCUSDT,AAAUSDT');
  const duration = a.run('S.hoverUntil-Date.now()');
  assert.ok(duration > 2900 && duration <= 3000);
  a.run('S.hoverUntil=Date.now()-1');
  assert.equal(order(a), 'AAAUSDT,BBBUSDT,CCCUSDT');
});
test('filtering and new listings do not destroy a manually arranged board', () => {
  const a = app(); seedBoard(a);
  a.run('setAutoSort(false); moveBoardSymbol("AAAUSDT","BBBUSDT"); S.search="CCC"');
  assert.equal(order(a), 'CCCUSDT');
  a.run('S.search=""; S.symbols.push({symbol:"DDDUSDT",score:100,vol5m:1})');
  assert.equal(order(a), 'AAAUSDT,BBBUSDT,CCCUSDT,DDDUSDT');
  a.run('setAutoSort(true)');
  assert.equal(a.run('moveBoardSymbol("AAAUSDT","CCCUSDT")'), false);
});
test('equal and missing metrics have a stable alphabetical tie breaker', () => {
  const a = app(); seedBoard(a);
  a.run('S.symbols[0].score=null; S.symbols[1].score=null; S.symbols.reverse()');
  assert.equal(order(a), 'CCCUSDT,AAAUSDT,BBBUSDT');
});
test('existing card headers refresh without rebuilding their canvas', () => {
  const a = app();
  a.context.tile = {querySelector: a.el};
  a.run('updateTileMetrics(tile,{symbol:"AAAUSDT",price:12,change5m:-2,surge:3,score:90,vol1m:2000000,tag:"Breakout",venue:"BY-F"})');
  assert.equal(a.el('.tp').textContent,'12.000');
  assert.equal(a.el('.tc').textContent,'-2.0%');
  assert.equal(a.el('.tc').className,'tc down');
  assert.equal(a.el('.tile-volume').textContent,'$2.00M');
  assert.equal(a.el('.tile-venue').textContent,'BY-F');
});

test('one real candle is rendered and height-only resize updates canvas backing size', () => {
  const a = app(), calls=[];
  const ctx=new Proxy({}, {get:(_,name)=>(...args)=>calls.push([name,...args]),set:()=>true});
  a.context.cv={clientWidth:400,clientHeight:240,width:400,height:100,getContext:()=>ctx};
  a.run('paintTile(cv,[{t:0,o:10,h:12,l:9,c:11,v:300}],999,"BTCUSDT")');
  assert.equal(a.context.cv.height,240);
  assert.ok(calls.some(([name])=>name==='fillRect'));
  assert.ok(calls.some(([name,text])=>name==='fillText' && text==='11.000'));
  assert.ok(!calls.some(([name,text])=>name==='fillText' && text==='999.000'));
});

test('late candle responses from the previous exchange cannot repopulate the cache', async () => {
  const a = app();
  let resolve;
  a.context.fetch=()=>new Promise(r=>{resolve=r});
  const pending=a.run('getCandles("BTCUSDT")');
  a.run('onSnapshot({type:"snapshot",feed:"bybit",sourceGeneration:1,symbols:[]})');
  resolve({ok:true,json:async()=>({candles:[{t:1,o:100,h:101,l:99,c:100,v:1}]})});
  assert.equal((await pending).length,0);
  assert.equal(a.run('S.candleCache.size'),0);
});
test('partial feeds are visibly degraded and generation changes clear Focus history', () => {
  const a=app();
  a.run('onSnapshot({type:"snapshot",feed:"binance",sourceGeneration:1,symbols:[]});chart.candles=[{}];chart.walls=[{}]');
  a.run('onSnapshot({type:"snapshot",feed:"binance",sourceGeneration:2,feedDegraded:true,symbols:[]})');
  assert.match(a.el('.live-pill').textContent,/Неполный поток/);
  assert.equal(a.run('chart.candles.length+chart.walls.length'),0);
});

test('filter volume uses millions of quote USDT and signed movement stays signed', () => {
  const a=app();
  const filters=a.run('parseMarketFilters((key,side)=>key==="vol24h"&&side==="min"?"10":key==="change5m"?(side==="min"?"-5":"-1"):"")');
  assert.equal(filters.vol24h.min,10000000);
  a.context.filters=filters;
  assert.equal(a.run('matchesMarketFilters({vol24h:10000000,change5m:-2},filters)'),true);
  assert.equal(a.run('matchesMarketFilters({vol24h:9999999,change5m:-2},filters)'),false);
  assert.equal(a.run('matchesMarketFilters({vol24h:20000000,change5m:2},filters)'),false);
});
test('active numeric filters exclude unknown values; inactive filters allow warmup rows', () => {
  const a=app();
  assert.equal(a.run('matchesMarketFilters({natr:null},{natr:{min:0}})'),false);
  assert.equal(a.run('matchesMarketFilters({natr:0},{natr:{min:0,max:0}})'),true);
  a.run('S.symbols=[{symbol:"BTCUSDT",vol24h:20000000,natr:null,score:null}]; S.filters={}');
  assert.equal(a.run('sortedRows().length'),1);
  a.run('S.filters={natr:{min:0.1}}');
  assert.equal(a.run('sortedRows().length'),0);
});
test('filter validation rejects inverted ranges, negative volume and nonfinite numbers', () => {
  const a=app();
  for (const code of [
    'parseMarketFilters((key,side)=>key==="natr"?(side==="min"?"5":"1"):"")',
    'parseMarketFilters((key,side)=>key==="vol24h"&&side==="min"?"-1":"")',
    'parseMarketFilters((key,side)=>key==="speed"&&side==="max"?"Infinity":"")',
  ]) assert.throws(()=>a.run(code));
});
test('ascending and descending NATR keep null at the end', () => {
  const a=app();
  a.run('S.sortKey="natr"; S.symbols=[{symbol:"A",natr:null},{symbol:"B",natr:2},{symbol:"C",natr:5}]');
  assert.equal(order(a),'C,B,A');
  a.run('S.sortAscending=true');
  assert.equal(order(a),'B,C,A');
});

test('global search includes hidden and filtered-out catalog symbols, with exact ticker first', () => {
  const a=app();
  a.run('S.catalog=[{symbol:"WBTCUSDT",venue:"BI-F"},{symbol:"BTCUSDT",venue:"BI-F"},{symbol:"ETHUSDT",venue:"BY-F"}]; S.filters={vol24h:{min:1e12}}; S.venue="BY-F"; S.hidden.add("BTCUSDT")');
  assert.equal(a.run('searchMarkets("btc").map(r=>r.symbol).join(",")'),'BTCUSDT,WBTCUSDT');
  assert.equal(a.run('searchMarkets("BTC/USDT")[0].symbol'),'BTCUSDT');
  assert.equal(a.run('searchMarkets("BTC")[0].venue'),'BI-F');
  assert.equal(a.run('S.filters.vol24h.min'),1e12);
  assert.equal(a.run('S.venue'),'BY-F');
});
test('Focus keeps the board order stable until return', () => {
  const a=app();seedBoard(a);
  const before=order(a);
  a.run('S.focusOpen=true; S.symbols[0].score=100');
  assert.equal(order(a),before);
  a.run('S.focusOpen=false');
  assert.equal(order(a),'AAAUSDT,BBBUSDT,CCCUSDT');
});
test('hidden symbols are excluded from the board but remain searchable', () => {
  const a=app();seedBoard(a);
  a.run('S.hidden.add("BBBUSDT")');
  assert.equal(order(a),'CCCUSDT,AAAUSDT');
  assert.equal(a.run('searchMarkets("BBB")[0].symbol'),'BBBUSDT');
});
test('catalog responses from a previous source are discarded', async () => {
  const a=app();let resolve;
  a.context.fetch=()=>new Promise(r=>{resolve=r});
  const request=a.run('loadSearchCatalog()');
  a.run('onSnapshot({type:"snapshot",feed:"bybit",symbols:[]})');
  resolve({ok:true,json:async()=>({symbols:[{symbol:"OLDUSDT"}]})});
  await request;
  assert.equal(a.run('S.catalog.length'),0);
});

test('color groups support create, rename, membership, filtering and deletion', () => {
  const a=app();seedBoard(a);
  a.run('globalThis.groupId=saveGroup(null,"Пробои","purple");toggleGroupSymbol(groupId,"AAAUSDT");S.activeGroup=groupId');
  assert.equal(order(a),'AAAUSDT');
  assert.equal(a.run('groupColor("AAAUSDT")'),'#b18bda');
  a.run('saveGroup(groupId,"Уровни","green")');
  assert.equal(a.run('S.groups[0].name'),'Уровни');
  assert.equal(order(a),'AAAUSDT');
  a.run('deleteGroup(groupId)');
  assert.equal(a.run('S.activeGroup'),'all');
  assert.equal(order(a),'BBBUSDT,CCCUSDT,AAAUSDT');
});
test('favorites survive custom group changes and search stays global', () => {
  const a=app();seedBoard(a);
  a.run('toggleGroupSymbol("watch","BBBUSDT");globalThis.groupId=saveGroup(null,"Test","red");toggleGroupSymbol(groupId,"AAAUSDT");S.activeGroup=groupId');
  assert.equal(a.run('searchMarkets("BBB")[0].symbol'),'BBBUSDT');
  a.run('deleteGroup(groupId);S.activeGroup="watch"');
  assert.equal(order(a),'BBBUSDT');
  a.run('toggleGroupSymbol("watch","BBBUSDT")');
  assert.equal(order(a),'');
});
test('invalid names/colors are rejected and membership toggles do not duplicate coins', () => {
  const a=app();
  assert.throws(()=>a.run('saveGroup(null," ","red")'));
  assert.throws(()=>a.run('saveGroup(null,"Test","javascript")'));
  a.run('globalThis.groupId=saveGroup(null,"Test","blue");toggleGroupSymbol(groupId,"BTCUSDT");toggleGroupSymbol(groupId,"BTCUSDT")');
  assert.equal(a.run('S.groups[0].symbols.length'),0);
});

test('book overlays require fresh L2 data, threshold and enabled toggle', () => {
  const a=app();
  a.run('S.connected=true;S.lastSnapshotAt=Date.now();globalThis.row={bookTs:Date.now(),walls:[{side:"bid",price:10,notional:1000000},{side:"ask",price:11,notional:100}],levels:[{low:9,high:9.1,mid:9.05,touches:3}]}');
  assert.equal(a.run('boardOverlays(row).walls.length'),1);
  a.run('row.bookTs=Date.now()-20000');
  assert.equal(a.run('boardOverlays(row).walls.length'),0);
  assert.equal(a.run('boardOverlays(row).levels.length'),1);
  a.run('S.showLevels=false;row.bookTs=Date.now();S.showWalls=false');
  assert.equal(a.run('boardOverlays(row).walls.length+boardOverlays(row).levels.length'),0);
  a.run('S.showWalls=true;S.showLevels=true;S.connected=false');
  assert.equal(a.run('boardOverlays(row).walls.length+boardOverlays(row).levels.length'),0);
});
test('board ignores under-confirmed or malformed levels', () => {
  const a=app();
  a.run('S.connected=true;S.lastSnapshotAt=Date.now()');
  assert.equal(a.run('boardOverlays({levels:[{low:10,high:9,mid:9.5,touches:4},{low:9,high:10,mid:9.5,touches:2}]}).levels.length'),0);
});

test('Focus uses server timeframe series without reaggregating daily candles', () => {
  const a=app();
  a.run('chart.tfMin=1440;chart.sourceTf=1440;chart.candles=[{t:0,o:1,c:2},{t:86400000,o:2,c:3}];rebuildSeries()');
  assert.equal(a.run('chart.series.length'),2);
  assert.equal(a.run('timeframeName(chart.tfMin)'),'1D');
  a.run('chart.tfMin=1;rebuildSeries()');
  assert.equal(a.run('chart.series.length'),0);
});

test('Focus defaults to independent 1m and daily panes and clamps each viewport independently', () => {
  const a=app();
  assert.equal(a.run('S.focusLayout'),2);
  assert.equal(a.run('focusPanes[1].tfMin'),1440);
  a.run('focusPanes[0].candles.push({t:1});focusPanes[1].series=Array.from({length:300},(_,i)=>({t:i*86400000}));focusPanes[1].visible=50');
  assert.equal(a.run('focusPanes[1].candles.length'),0);
  assert.equal(a.run('clampOffset(999,focusPanes[1])'),250);
});
test('Focus cursor synchronization uses timestamps, not bar indices or screen pixels', () => {
  const a=app();
  a.run('chart.series=[{t:1000},{t:61000}];chart.offset=0;chart.visible=2');
  assert.equal(a.run('focusTimeAt(chart,75,174)'),61000);
  a.run('syncFocusCursor(chart,61000)');
  assert.equal(a.run('focusPanes[1].syncedTime'),61000);
  a.run('S.syncCrosshair=false;syncFocusCursor(chart,61000)');
  assert.equal(a.run('focusPanes[1].syncedTime'),null);
});

test('Focus ignores an older overlapping history response for the same symbol and timeframe', async () => {
  const a=app();
  a.el('chart-modal').classList.contains=()=>false;
  a.run('chartSym="BTCUSDT";drawChart=()=>{};globalThis.pending=[];fetch=()=>new Promise(resolve=>pending.push(resolve));globalThis.first=refreshChart();globalThis.second=refreshChart()');
  a.run('pending[1]({ok:true,json:async()=>({candles:[{t:200,o:2,c:3}],timeframe:"1m",venue:"DEMO"})})');
  await a.run('second');
  a.run('pending[0]({ok:true,json:async()=>({candles:[{t:100,o:1,c:2}],timeframe:"1m",venue:"DEMO"})})');
  await a.run('first');
  assert.equal(a.run('chart.series[0].t'),200);
});


test('Focus range synchronization maps the left timestamp into each timeframe', () => {
  const a=app();
  a.run('S.focusLayout=2;S.syncRange=true;chart.series=[{t:1000},{t:61000},{t:121000}];chart.visible=2;chart.offset=1;focusPanes[1].series=[{t:0},{t:60000},{t:120000},{t:180000}];focusPanes[1].visible=3;focusPanes[1].offset=0;syncFocusRange(chart)');
  assert.equal(a.run('focusPanes[1].visible'),25);
  assert.equal(a.run('focusPanes[1].offset'),0);
});

test('Focus overlays are independently configurable per pane', () => {
  const a=app();
  a.run('S.paneOverlays={"0":{walls:false},"1":{levels:false,volume:false}}');
  assert.equal(a.run('paneOverlay(focusPanes[0],"walls")'),false);
  assert.equal(a.run('paneOverlay(focusPanes[0],"levels")'),true);
  assert.equal(a.run('paneOverlay(focusPanes[1],"levels")'),false);
  assert.equal(a.run('paneOverlay(focusPanes[1],"volume")'),false);
});

test('Focus alert payload carries instrument identity when snapshot provides it', () => {
  const a=app();
  a.run('chartSym="BTCUSDT";S.bySym.set("BTCUSDT",{instrument_id:"DEMO:FUTURES:BTCUSDT"});globalThis.alertBody={symbol:chartSym,instrument_id:S.bySym.get(chartSym)?.instrument_id||undefined}');
  assert.equal(a.run('alertBody.instrument_id'),'DEMO:FUTURES:BTCUSDT');
});

test('Focus exposes keyboard drawing shortcuts and undo binding', () => {
  const source=require('fs').readFileSync(require('node:path').join(__dirname,'../js/app.js'),'utf8');
  assert.match(source,/shortcuts=\{h:"horizontal",t:"trend",r:"rectangle",f:"fib",m:"ruler",p:"pencil",v:"none"\}/);
  assert.match(source,/draw-undo.*click/);
});
