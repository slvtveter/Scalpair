"""CDP acceptance fixture: 200 instruments, 16 charts, 100 Focus round trips.

Serve frontend on 4175 and run isolated Chrome on debugging port 9242.
Uses synthetic HTTP/WS fixtures only, never production data. Requires websockets.
"""
import asyncio
import base64
import json
import platform
import urllib.request
from pathlib import Path

import websockets

FIXTURE = r'''
localStorage.clear();
window.fixtureSockets = 0;
window.fixtureFetches = [];
window.WebSocket = class { constructor() { fixtureSockets++; } send() {} close() {} };
window.fetch = async (input) => {
 const url = new URL(String(input), location.href);
 window.fixtureFetches.push(url.href);
 const tf = url.searchParams.get('timeframe') || '1m';
 const step = tf === '1D' ? 86400000 : 60000;
 const now = Math.floor(Date.now()/step)*step;
 const candles = Array.from({length:300},(_,i)=> {
   const p=100+i*.01+Math.sin(i/10);
   return {t:now-(299-i)*step,o:p,h:p+.2,l:p-.2,c:p+.1,v:100+i};
 });
 const body = url.pathname.endsWith('/candles')
   ? {candles,venue:'DEMO',market:'FUTURES',timeframe:tf,overlaysAvailable:false}
   : {symbols:[],instruments:[],walls:[],items:[]};
 return new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}});
};
'''

async def main():
    tabs = json.load(urllib.request.urlopen('http://127.0.0.1:9242/json'))
    tab = next(t for t in tabs if t['type'] == 'page')
    async with websockets.connect(tab['webSocketDebuggerUrl'], max_size=20_000_000) as ws:
        serial, errors = 0, []
        async def cmd(method, params=None):
            nonlocal serial
            serial += 1
            await ws.send(json.dumps({'id':serial,'method':method,'params':params or {}}))
            while True:
                response = json.loads(await ws.recv())
                if response.get('method') == 'Runtime.exceptionThrown':
                    errors.append(response['params'])
                if response.get('id') == serial:
                    assert 'error' not in response, response
                    return response.get('result', {})
        async def js(expression):
            result = await cmd('Runtime.evaluate', {'expression':expression,'returnByValue':True,'awaitPromise':True})
            assert 'exceptionDetails' not in result, result
            return result['result'].get('value')
        await cmd('Runtime.enable')
        await cmd('Page.addScriptToEvaluateOnNewDocument', {'source':FIXTURE})
        await cmd('Emulation.setDeviceMetricsOverride', {'width':1600,'height':1000,'deviceScaleFactor':1,'mobile':False})
        await cmd('Page.navigate', {'url':'http://127.0.0.1:4175/'})
        await asyncio.sleep(2)
        # Install after navigation as well as through the new-document hook. This
        # keeps the check deterministic when Chrome reuses an existing target.
        await js(FIXTURE)
        assert await js('typeof fixtureFetches') == 'object', await js('location.href')
        await js('''S.gridSize=4;S.autoSort=false;
          onSnapshot({type:'snapshot',feed:'mock',symbols:Array.from({length:200},(_,i)=>({
          symbol:`TEST${i}USDT`,instrument_id:`DEMO:FUTURES:TEST${i}USDT`,venue:'DEMO',market:'FUTURES',
          price:103,change5m:1,vol1m:100000,vol24h:1e8,score:50,surge:2,natr:.5,bookTs:Date.now()}))});
          renderBoard();renderCoinList();''')
        await asyncio.sleep(1)
        assert await js('document.querySelectorAll(".board-tile canvas").length') == 16
        assert await js('S.symbols.length') == 200
        await js("openChart('TEST0USDT','DEMO:FUTURES:TEST0USDT')")
        await asyncio.sleep(0.5)
        assert await js('focusPanes[0].series.length') == 300
        await js('closeChart()')
        await cmd('HeapProfiler.collectGarbage')
        before = await cmd('Memory.getDOMCounters')
        heap_before = await cmd('Runtime.getHeapUsage')
        timings = await js('''(async()=>{const times=[];
          for(let i=0;i<100;i++) {
            const start=performance.now();
            document.querySelector('.board-tile').click();
            await new Promise(resolve=>requestAnimationFrame(resolve));
            document.getElementById('chart-close').click();
            times.push(performance.now()-start);
          } return times.sort((a,b)=>a-b);})()''')
        await asyncio.sleep(1)
        await cmd('HeapProfiler.collectGarbage')
        after = await cmd('Memory.getDOMCounters')
        heap_after = await cmd('Runtime.getHeapUsage')
        assert not await js('S.focusOpen')
        assert await js('fixtureSockets') == 1
        assert after['jsEventListeners'] == before['jsEventListeners'], (before,after)
        assert after['nodes'] <= before['nodes'] + 50, (before,after)
        assert heap_after['usedSize'] < heap_before['usedSize'] + 2_000_000
        assert not errors, errors
        shot = await cmd('Page.captureScreenshot', {'format':'png'})
        Path('/tmp/scalpair-16-charts.png').write_bytes(base64.b64decode(shot['data']))
        print(json.dumps({'platform':platform.platform(),'browser':await cmd('Browser.getVersion'),
              'instruments':200,'charts':16,'roundTrips':100,'p95RoundTripMs':timings[94],
              'domBefore':before,'domAfter':after,'heapBefore':heap_before['usedSize'],
              'heapAfter':heap_after['usedSize'],'runtimeExceptions':len(errors)},indent=2))
        await cmd('Browser.close')

asyncio.run(main())
