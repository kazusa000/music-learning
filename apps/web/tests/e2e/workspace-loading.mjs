import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const anchor=process.env.MUSIC_LEARNING_TEST_RESOURCE_ROOT?path.join(process.env.MUSIC_LEARNING_TEST_RESOURCE_ROOT,'apps/web/package.json'):import.meta.url;
const {chromium}=createRequire(anchor)('playwright');
const f=JSON.parse(await readFile(path.join(process.argv[2],'fixture.json'),'utf8')),base='http://127.0.0.1:'+f.port;
const browser=await chromium.launch({channel:'msedge',headless:true});
const context=await browser.newContext({viewport:{width:1536,height:864},deviceScaleFactor:2.5});
const page=await context.newPage();page.setDefaultTimeout(6000);
const report={checks:[],errors:[],external:[],evidence:'Isolated real project API; native telemetry injected, no audio or plugins loaded'};
let state={status:'stopped'},opens=0,transports=0,releaseMetadata,releaseOpen;
const settling=new Set();
let metadataGate=new Promise(resolve=>releaseMetadata=resolve),openGate=new Promise(resolve=>releaseOpen=resolve);
const track=f.revision.snapshot.tracks[0].id;
page.on('pageerror',e=>report.errors.push(e.message));
await page.route('**/*',async route=>{
 const request=route.request();
 if(!request.url().startsWith(base)){report.external.push(request.url());return route.abort();}
 if(!request.url().endsWith('/v1/commands'))return route.continue();
 const {operation,arguments:args}=request.postDataJSON();
 if(operation==='revisions.get'){await metadataGate;return route.continue();}
 if(!operation.startsWith('engine.'))return route.continue();
 if(operation==='engine.open'){
  opens++;await openGate;
  state={...args,status:'loading',engine_session:'a'.repeat(48),generation:opens,applied_revision:null,
   source_sample_rate:44100,duration_samples:44100*120,position_sample:0,playing:false,
   plugins:{[track]:{status:'loading',load_stage:0},bus:{status:'loading',load_stage:0}}};
 }
 if(operation==='engine.claim')state={...state,owner_id:args.owner_id};
 if(operation==='engine.monitor')state={...state,monitor:args.tracks};
 if(operation==='engine.transport'){
  transports++;state={...state,...args,status:'loading',applied_revision:null};
  const pending=state;
  const timer=setTimeout(()=>{settling.delete(timer);if(state===pending)state={...state,status:'ready',applied_revision:state.revision_id};},220);
  settling.add(timer);
 }
 if(operation==='engine.close')state={status:'stopped'};
 return route.fulfill({json:{ok:true,result:state}});
});
const until=async(fn,message)=>{const end=Date.now()+6000;while(Date.now()<end){if(await fn())return;await new Promise(r=>setTimeout(r,40));}throw Error(message);};
const bar=()=>page.getByRole('progressbar',{name:'工程加载进度',exact:true});
try{
 await page.goto(`${base}/?project=${f.project.id}&view=workbench#token=${f.token}`);
 await page.getByText('正在打开工程',{exact:true}).waitFor();
 assert.equal(await bar().getAttribute('aria-valuenow'),null);
 releaseMetadata();await page.getByLabel('音乐工作台',{exact:true}).waitFor();
 await until(()=>Promise.resolve(opens===1),'Native open did not start');
 await page.getByText('正在准备工程',{exact:true}).waitFor();
 assert.equal(await bar().getAttribute('aria-valuenow'),null);
 releaseOpen();await until(()=>bar().getAttribute('aria-valuenow').then(n=>n==='0'),'Native node loading not shown');
 state.plugins[track]={status:'ready'};state.plugins.bus={status:'loading',load_stage:2};
 await until(()=>bar().getAttribute('aria-valuenow').then(n=>n==='50'),'Ready count not reflected');
 await page.getByText(/1 \/ 2 个音源就绪/).waitFor();
 assert((await page.locator('.workspace-loading-copy p').innerText()).includes('恢复音色'));
 await page.locator('.workspace-loading-details summary').click();
 await page.screenshot({path:path.join(f.report,'loading-4k.png')});
 await page.locator('.workspace-loading-details summary').click();
 await until(()=>page.locator('.workspace-loading-heading').innerText().then(t=>t.includes('已用 1 秒')),'Elapsed time absent');
 report.checks.push('Immediate metadata/open feedback, actual 0/2 → 1/2 progress, stage, elapsed and track details at 4K');
 await page.setViewportSize({width:760,height:740});
 const box=await page.locator('.workspace-loading').boundingBox();assert(box.width<=760&&box.x>=0);
 await page.screenshot({path:path.join(f.report,'loading-narrow.png')});
 state.plugins.bus={status:'ready'};
 await until(()=>bar().getAttribute('aria-valuenow').then(n=>n==='99'),'Graph handoff must not announce 100 early');
 // Initial-entry failure remains actionable until this entry first succeeds.
 state.status='loading';state.applied_revision=null;state.plugins.bus={status:'failed',error:'采样文件不可用'};
 await page.getByText('部分音源加载失败',{exact:true}).waitFor();
 await page.getByRole('button',{name:'重新加载',exact:true}).waitFor();
 assert.notEqual(await bar().getAttribute('aria-valuenow'),'100');
 await page.screenshot({path:path.join(f.report,'loading-failure.png')});
 await page.getByRole('button',{name:'重新加载',exact:true}).click();
 await until(()=>Promise.resolve(opens===2),'Retry did not reopen native graph');
 await until(()=>bar().getAttribute('aria-valuenow').then(n=>n==='0'),'Retry retained stale progress');
 state.plugins={[track]:{status:'ready'},bus:{status:'ready'}};state.status='ready';state.applied_revision=state.revision_id;
 await bar().waitFor({state:'detached'});
 report.checks.push('Initial-entry failure and retry remain visible; the first ready graph retires the entry bar');
 let unexpectedBars=0;
 await page.evaluate(()=>{
  window.__loadingFlashes=0;
  window.__loadingObserver=new MutationObserver(records=>{
   for(const record of records)for(const node of record.addedNodes)
    if(node instanceof Element&&(node.matches('.workspace-loading')||node.querySelector('.workspace-loading')))window.__loadingFlashes++;
  });
  window.__loadingObserver.observe(document.querySelector('.studio'),{childList:true,subtree:true});
 });
 await page.setViewportSize({width:1536,height:864});
 // Exercise actual UI mode controls. Inject a loading graph for every view switch.
 for(const mode of ['重建','原曲','重建']){
  const count=opens;
  await page.getByRole('button',{name:new RegExp('^'+mode)}).click();
  await until(()=>Promise.resolve(opens>count),'View switch did not reach native transport');
  await new Promise(r=>setTimeout(r,180));assert.equal(await bar().count(),0);
  state.plugins={[track]:{status:'ready'},bus:{status:'ready'}};state.status='ready';state.applied_revision=state.revision_id;
 }
 await page.locator('.daw-playlist-ruler svg').waitFor();
 // Seeking while paused and while playing both pass through temporary loading.
 for(const playing of [false,true]){
  if(playing)await page.getByRole('button',{name:'播放',exact:true}).click();
  for(const x of [90,240,390]){
   const count=transports;
   await page.locator('.daw-playlist-ruler svg').click({position:{x,y:12}});
   await until(()=>Promise.resolve(transports>count),'Timeline seek did not reach native transport');
   await new Promise(r=>setTimeout(r,280));assert.equal(await bar().count(),0);
  }
 }
 state.playing=false;state.status='loading';state.plugins.bus={status:'failed',error:'后续音源故障'};
 await page.locator('.studio-message').filter({hasText:'后续音源故障'}).waitFor();
 assert.equal(await bar().count(),0);
 state.status='ready';state.applied_revision=state.revision_id;state.plugins.bus={status:'ready'};
 unexpectedBars=await page.evaluate(()=>{window.__loadingObserver.disconnect();return window.__loadingFlashes;});
 assert.equal(unexpectedBars,0,'Entry bar flashed during an in-workspace operation');
 report.checks.push('Three original/reconstruction switches, six paused/playing seeks, and a later failure produce zero loading-bar mounts');
 // Re-enter a new controller: old status belongs to a different project and must not finish it.
 const beforeReentry=opens;
 await page.getByRole('button',{name:'返回音乐库',exact:true}).click();
 await page.getByRole('button',{name:/打开编曲|打开工程/}).click();
 await until(()=>Promise.resolve(opens>beforeReentry),'Re-entry did not open graph');
 await until(()=>bar().getAttribute('aria-valuenow').then(n=>n==='0'),'Re-entry failed to reset progress');
 report.checks.push('Leaving and re-entering the project starts a fresh entry loading lifecycle');
 assert.deepEqual(report.errors,[]);assert.deepEqual(report.external,[]);
 console.log(JSON.stringify(report));
}finally{
 for(const timer of settling)clearTimeout(timer);
 releaseMetadata();releaseOpen();await writeFile(path.join(f.report,'report.json'),JSON.stringify(report,null,2));await browser.close();
}
