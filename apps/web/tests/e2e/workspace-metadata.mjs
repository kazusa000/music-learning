import {chromium} from 'playwright';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const root=path.resolve(process.argv[2]);
const fixture=JSON.parse(await readFile(path.join(root,'fixture.json'),'utf8'));
const {project,revision,tracks,metadata_scale:scale}=fixture;
assert.equal(scale.tracks,7);assert(scale.notes>=6684);assert(scale.duration_seconds>=395);
const base='http://127.0.0.1:'+fixture.port;
const report={status:'running',scale,samples:[],errors:[],audioRequests:[],pcmRequests:[],
 scope:'20 fresh renderer contexts and empty HTTP caches, warm server/OS cache; navigation through visible tracks and an acknowledged track selection. Plugin readiness is measured separately. No mock API, audio playback, H library or formal project.'};
async function call(operation,args={}){
 const response=await fetch(base+'/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+fixture.token,'Content-Type':'application/json'},body:JSON.stringify({operation,arguments:args})});
 const value=await response.json();assert(value.ok,JSON.stringify(value.error));return value.result;
}
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
 const initial=await call('revisions.get',{project_id:project.id});
 assert.equal(initial.id,revision.id);assert.equal(initial.snapshot.notes.length,scale.notes);
 assert.equal((await call('jobs.list',{project_id:project.id})).length,0);
 for(let i=0;i<20;i++){
  const context=await browser.newContext({viewport:{width:1536,height:864},deviceScaleFactor:2.5});
  const page=await context.newPage();page.setDefaultTimeout(15000);
  page.on('pageerror',error=>report.errors.push(error.message));
  page.on('response',r=>{if((r.headers()['content-type']??'').startsWith('audio/'))report.audioRequests.push(r.url());});
  page.on('request',r=>{if((r.postData()??'').includes('playback.live.'))report.pcmRequests.push(r.postData());});
  try{
   const begin=performance.now();
   await page.goto(base+'/?project='+project.id+'&view=workbench#token='+fixture.token,{waitUntil:'domcontentloaded'});
   await page.waitForFunction(count=>document.querySelectorAll('.track-card').length===count,scale.tracks);
   const visibleMs=performance.now()-begin;
   const selector='.track-card[data-track="'+tracks.at(-1)+'"]';
   await page.locator(selector+' .track-select').click();
   await page.waitForFunction(selector=>document.querySelector(selector)?.classList.contains('is-selected'),selector);
   const interactiveMs=performance.now()-begin;
   assert.equal(await page.locator('.studio').getAttribute('data-audio-clock'),'native');
   let state;const until=performance.now()+20000;
   do{state=await call('engine.status');if(state.status==='ready'&&state.applied_revision===revision.id)break;await new Promise(r=>setTimeout(r,25));}while(performance.now()<until);
   assert.equal(state.status,'ready');assert.equal(state.applied_revision,revision.id);assert.equal(state.playing,false);
   report.samples.push({index:i+1,visible_ms:visibleMs,interactive_ms:interactiveMs,native_ready_ms:performance.now()-begin,plugins:Object.keys(state.plugins??{}).length});
   if(i===0)await page.screenshot({path:path.join(root,'metadata-4k.png')});
  }finally{await context.close();await call('engine.close');}
 }
 const stats=key=>{const values=report.samples.map(s=>s[key]).sort((a,b)=>a-b);return {samples:values.length,median_ms:(values[9]+values[10])/2,p95_ms:values[18],maximum_ms:values[19]};};
 report.statistics={visible:stats('visible_ms'),interactive:stats('interactive_ms'),native_ready:stats('native_ready_ms')};
 report.within_initial_target=report.statistics.interactive.p95_ms<=1000;
 assert.equal((await call('revisions.get',{project_id:project.id})).id,revision.id);
 assert.equal((await call('jobs.list',{project_id:project.id})).length,0);
 assert.deepEqual(report.errors,[]);assert.deepEqual(report.audioRequests,[]);assert.deepEqual(report.pcmRequests,[]);
 assert(report.within_initial_target,'Workspace interaction P95 exceeded the initial 1000ms target');
 report.status='succeeded';
}catch(error){report.status='failed';report.error=String(error.stack??error);throw error;}
finally{await writeFile(path.join(root,'metadata-report.json'),JSON.stringify(report,null,2));await browser.close();await call('engine.close').catch(()=>{});}
console.log(JSON.stringify({status:report.status,statistics:report.statistics}));
