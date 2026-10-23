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
let state={status:'stopped'},opens=0,releaseMetadata,releaseOpen;
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
 if(operation==='engine.transport')state={...state,...args};
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
 state.status='ready';state.applied_revision=state.revision_id;
 await until(()=>bar().getAttribute('aria-valuenow').then(n=>n==='100'),'Applied graph did not complete');
 await page.getByText('工程已就绪',{exact:true}).waitFor();await bar().waitFor({state:'detached'});
 report.checks.push('Narrow layout, graph handoff below 100%, correct applied revision and auto-hide while telemetry continues');
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
 // Re-enter a new controller: old status belongs to a different project and must not finish it.
 await page.getByRole('button',{name:'返回音乐库',exact:true}).click();
 await page.getByRole('button',{name:/打开编曲/}).click();
 await until(()=>Promise.resolve(opens===3),'Re-entry did not open graph');
 await until(()=>bar().getAttribute('aria-valuenow').then(n=>n==='0'),'Re-entry failed to reset progress');
 report.checks.push('Failure stays visible, explicit retry resets native graph and progress, re-entering project resets');
 assert.deepEqual(report.errors,[]);assert.deepEqual(report.external,[]);
 console.log(JSON.stringify(report));
}finally{
 releaseMetadata();releaseOpen();await writeFile(path.join(f.report,'report.json'),JSON.stringify(report,null,2));await browser.close();
}
