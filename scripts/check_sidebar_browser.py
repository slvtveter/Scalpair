"""Browser regression check; serve frontend on 4175 and launch Chrome with
--remote-debugging-port=9242 pointing at that URL. Requires websockets.
Checks real DOM handlers, which the lightweight Node mocks do not exercise.
"""
import asyncio,json,urllib.request
import websockets
async def main():
 tab=next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:9242/json')) if t['type']=='page')
 async with websockets.connect(tab['webSocketDebuggerUrl'],max_size=10000000) as ws:
  serial=0;errors=[]
  async def cmd(method,params={}):
   nonlocal serial
   serial+=1
   await ws.send(json.dumps({'id':serial,'method':method,'params':params}))
   while True:
    r=json.loads(await ws.recv())
    if r.get('method')=='Runtime.exceptionThrown':errors.append(r['params'])
    if r.get('id')==serial:return r.get('result',{})
  async def js(e):
   r=await cmd('Runtime.evaluate',{'expression':e,'returnByValue':True})
   assert 'exceptionDetails' not in r,r
   return r['result'].get('value')
  await cmd('Runtime.enable'); await cmd('Page.reload',{'ignoreCache':True});await asyncio.sleep(2)
  for width in [1440,390]:
   await cmd('Emulation.setDeviceMetricsOverride',{'width':width,'height':960,'deviceScaleFactor':1,'mobile':width==390})
   for lang in ['ru','en']:
    await js(f'S.lang="{lang}";applyI18n()')
    for name in ['density','alerts','coins']:
     await js(f'document.querySelector("[data-tab={name}]").click()')
     assert await js('S.sideTab')==name
     assert await js('$("tab-coins").classList.contains("hidden")')==(name!='coins')
     assert await js('$("tab-alerts").classList.contains("hidden")')==(name!='alerts')
     assert await js('$("tab-density").classList.contains("hidden")')==(name=='alerts')
    await js('$("bell").click()')
    assert await js('Boolean($("nl-login"))')
   print(width,await js('({width:innerWidth,scroll:document.documentElement.scrollWidth,labels:[...document.querySelectorAll(".side-tab")].map(x=>x.textContent)})'))
  assert not errors,errors
  print('Sidebar navigation + bell + RU/EN: PASS; runtime exceptions: 0')
  await cmd('Browser.close')
asyncio.run(main())
