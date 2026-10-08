const { chromium } = require('playwright-core');
const path='file:///workspace/fuel-prices/fuel-prices-northwest.html';
let fails=0; const ok=(c,m)=>{console.log((c?'PASS ':'FAIL ')+m); if(!c) fails++;};
(async()=>{
 const b=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true});
 for (const [w,h,name] of [[1280,900,'desktop'],[390,844,'mobile']]) {
  const ctx=await b.newContext({viewport:{width:w,height:h},deviceScaleFactor: name==='mobile'?2:1, isMobile:name==='mobile', hasTouch:name==='mobile'});
  const p=await ctx.newPage(); const errs=[];
  p.on('console',m=>{if(m.type()==='error') errs.push(m.text())}); p.on('pageerror',e=>errs.push(e.message));
  await p.goto(path,{waitUntil:'networkidle'}); await p.waitForTimeout(1500);
  let s=await p.evaluate(()=>window.__state);
  ok(s && s.fuel==='E10' && s.count>0, `${name}: loads, E10, ${s&&s.count} stations within 10mi of Parbold`);
  console.log('  top E10:', JSON.stringify(s.top.slice(0,3)));
  ok(await p.evaluate(()=>!!window.L && !!document.querySelector('#map canvas') && window.__state.markers>0 && document.querySelectorAll('.leaflet-tile-loaded').length>0), `${name}: map tiles + ${s.markers} markers drawn`);
  await p.screenshot({path:`/workspace/fuel-prices/shots/${name}-top.png`});
  await p.screenshot({path:`/workspace/fuel-prices/shots/${name}-full.png`,fullPage:true});
  if(name==='desktop'){
   await p.click('#fuelBtns button[data-f="B7"]'); s=await p.evaluate(()=>window.__state);
   ok(s.fuel==='B7' && s.count>0, `diesel toggle (${s.count})`); console.log('  top B7:', JSON.stringify(s.top.slice(0,3)));
   const tops=s.top.map(t=>t.p); ok(tops.every((v,i)=>!i||v>=tops[i-1]), 'top5 ascending');
   const rowsP=await p.$$eval('#tbl tbody td.p',t=>t.map(x=>parseFloat(x.textContent)));
   ok(rowsP.every((v,i)=>!i||v>=rowsP[i-1]),'table sorted by price asc by default');
   await p.click('#tbl th[data-k="price"]');
   const d=await p.$$eval('#tbl tbody td.p',t=>t.map(x=>parseFloat(x.textContent)));
   ok(d.every((v,i)=>!i||v<=d[i-1]),'price sort toggles to desc');
   await p.click('#tbl th[data-k="dist"]');
   const dist=await p.$$eval('#tbl tbody tr td:nth-child(7)',t=>t.map(x=>parseFloat(x.textContent)));
   ok(dist.every((v,i)=>!i||v>=dist[i-1]) && dist.every(v=>v<=10),'distance sort asc, all within 10mi');
   await p.click('#tbl th[data-k="brand"]');
   const br=await p.$$eval('#tbl tbody tr td:nth-child(2)',t=>t.map(x=>x.textContent.toLowerCase()));
   ok(br.every((v,i)=>!i||v>=br[i-1]),'brand sort');
   await p.click('#radBtns button[data-r="0"]'); s=await p.evaluate(()=>window.__state); const all=s.count;
   ok(all>500,'radius All shows whole NW: '+all);
   await p.click('#radBtns button[data-r="5"]'); s=await p.evaluate(()=>window.__state); ok(s.count<all && s.count>0,'radius 5: '+s.count);
   await p.click('button[data-place="orrell"]'); s=await p.evaluate(()=>window.__state); ok(s.count>0,'Orrell 5mi: '+s.count+' '+JSON.stringify(s.top[0]));
   await p.click('#radBtns button[data-r="0"]');
   await p.fill('#search','tesco'); await p.waitForTimeout(400);
   const brands=await p.$$eval('#tbl tbody tr td:nth-child(2)',t=>[...new Set(t.map(x=>x.textContent))]);
   ok(brands.length>=1 && brands.every(x=>/tesco/i.test(x)) , 'search tesco -> '+brands.join(','));
   await p.fill('#search','WN5'); await p.waitForTimeout(400);
   const pcs=await p.$$eval('#tbl tbody tr td:nth-child(5)',t=>t.map(x=>x.textContent));
   ok(pcs.length>0 && pcs.every(x=>x.startsWith('WN5')),'search WN5 -> '+pcs.length);
   await p.fill('#search',''); await p.waitForTimeout(300);
   for(const f of ['E5','SDV','E10']){await p.click(`#fuelBtns button[data-f="${f}"]`); s=await p.evaluate(()=>window.__state); ok(s.fuel===f&&s.count>0,`toggle ${f}: ${s.count}`);}
   await p.click('#freshOnly'); s=await p.evaluate(()=>window.__state); ok(s.count>0&&s.count<=all,'fresh-only filter: '+s.count); await p.click('#freshOnly');
   await p.fill('#placeInput','WN8 7AA'); await p.click('#placeGo'); await p.waitForTimeout(2500);
   ok(/WN8 7AA/.test(await p.textContent('#placeMsg')),'postcode lookup: '+await p.textContent('#placeMsg'));
   await p.click('button[data-place="parbold"]'); await p.click('#radBtns button[data-r="10"]');
   await p.click('#top5 li'); await p.waitForTimeout(800);
   ok(await p.$('.leaflet-popup')!==null,'clicking top5 opens map popup');
   await p.screenshot({path:'/workspace/fuel-prices/shots/desktop-popup.png'});
  } else {
   const sw=await p.evaluate(()=>document.documentElement.scrollWidth); ok(sw<=392,'mobile no horizontal overflow: '+sw);
   await p.click('#fuelBtns button[data-f="B7"]');
   await p.screenshot({path:'/workspace/fuel-prices/shots/mobile-diesel.png'});
  }
  ok(errs.length===0, `${name}: no console errors ${errs.join(' | ')}`);
  await ctx.close();
 }
 // offline: block network
 const ctx=await b.newContext({viewport:{width:1280,height:900}}); const p=await ctx.newPage(); const errs=[];
 p.on('pageerror',e=>errs.push(e.message)); await ctx.route('**/*',r=>r.request().url().startsWith('file:')?r.continue():r.abort());
 await p.goto(path); await p.waitForTimeout(800); const s=await p.evaluate(()=>window.__state);
 ok(s&&s.count>0&&errs.length===0,'offline: table works without map ('+(s&&s.count)+')');
 await b.close(); console.log(fails?`${fails} FAILED`:'ALL PASSED'); process.exit(fails?1:0);
})();
