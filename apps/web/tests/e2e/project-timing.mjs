import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const root=path.resolve(process.argv[2]),f=JSON.parse(await readFile(path.join(root,'fixture.json'),'utf8'));
const anchor=process.env.MUSIC_LEARNING_TEST_RESOURCE_ROOT?path.join(process.env.MUSIC_LEARNING_TEST_RESOURCE_ROOT,'apps/web/package.json'):import.meta.url;
const {chromium}=createRequire(anchor)('playwright');
const base='http://127.0.0.1:'+f.port,report={checks:[],errors:[]};
const id=()=>crypto.randomUUID().replaceAll('-','');
async function call(operation,args={},revision){const response=await fetch(base+'/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+f.token,'Content-Type':'application/json'},body:JSON.stringify({operation,arguments:args,expected_revision:revision??null,idempotency_key:id()})});const data=await response.json();assert(data.ok,JSON.stringify(data.error));return data.result;}
async function until(fn,message){const end=Date.now()+15000;while(Date.now()<end){if(await fn())return;await new Promise(r=>setTimeout(r,60));}throw Error(message);}
const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1536,height:864},deviceScaleFactor:2.5}),page=await context.newPage();
await page.addInitScript(()=>{window.mlDesktop={post:()=>{}};});
page.setDefaultTimeout(12000);page.on('pageerror',e=>report.errors.push(e.message));
try{
 await page.goto(base+'/#token='+f.token);await page.getByRole('button',{name:'从零编曲',exact:false}).click();
 await page.getByLabel('作品名称',{exact:true}).fill('速度拍号验收');await page.getByLabel('初始拍号').selectOption('6/8');await page.getByLabel('初始小节数').fill('4');
 await page.getByRole('button',{name:'进入编曲',exact:true}).click();await page.locator('.studio').waitFor();
 const p=(await call('projects.list',{limit:1000})).find(p=>p.title==='速度拍号验收');let rev=await call('revisions.get',{project_id:p.id});
 assert.equal(rev.snapshot.duration_samples,264600);assert.equal(await page.getByLabel('工程拍号').inputValue(),'6/8');
 const tid=rev.snapshot.tracks[0].id,provenance={origin:'user',source_id:'timing-browser'};
 for(const sha of [f.template.state_asset_id,f.template.descriptor_asset_id]){
   const bytes=await (await fetch(base+'/v1/assets/'+sha,{headers:{Authorization:'Bearer '+f.token}})).arrayBuffer();
   const uploaded=await (await fetch(base+'/v1/uploads',{method:'PUT',headers:{Authorization:'Bearer '+f.token,'Content-Type':'application/octet-stream','Idempotency-Key':id()},body:bytes})).json();assert(uploaded.ok);
   await call('assets.register',{project_id:p.id,upload_id:uploaded.result.upload_id,provenance},rev.id);
 }
 const snapshot={...rev.snapshot,tracks:rev.snapshot.tracks.map(t=>({...t,gain_db:-90})),plugins:[{...f.template,id:id(),track_id:tid}],notes:[{id:id(),track_id:tid,pitch:60,velocity:.7,start_sample:0,end_sample:220500,provenance}]};
 rev=await call('revisions.commit',{project_id:p.id,snapshot,author:'test',description:'Independent sounding MIDI'},rev.id);
 await until(async()=>await page.locator('.studio').getAttribute('data-engine-revision')===rev.id,'MIDI engine revision not ready');
 await until(async()=>Object.values((await call('engine.status')).plugins??{}).some(p=>p.status==='ready'),'Fixture plugin not ready');
 await page.getByRole('button',{name:'播放',exact:true}).click();await until(async()=>{const e=await call('engine.status');return e.playing&&e.meters[tid]>0;},'MIDI did not sound');
 const tempo=page.getByLabel('工程速度 BPM');await tempo.fill('60');await tempo.press('Enter');
 await until(async()=>{rev=await call('revisions.get',{project_id:p.id});return rev.snapshot.duration_samples===529200;},'Tempo edit not saved');
 await until(async()=>{const e=await call('engine.status');return e.playing&&e.applied_revision===rev.id&&e.meters[tid]>0;},'Live tempo update stopped sounding');
 assert.equal(rev.snapshot.notes[0].end_sample,441000);assert.equal(rev.snapshot.notes[0].pitch,60);
 await page.getByLabel('工程拍号').selectOption('3/4');
 await until(async()=>{rev=await call('revisions.get',{project_id:p.id});return rev.snapshot.timing_map.meters[0].numerator===3&&rev.snapshot.timing_map.meters[0].denominator===4;},'Meter edit not saved');
 assert.equal(rev.snapshot.notes[0].end_sample,441000);assert.equal(rev.snapshot.duration_samples,529200);
 assert.equal((await call('engine.status')).playing,true);await page.getByRole('button',{name:'暂停',exact:true}).click();
 await until(async()=>!(await call('engine.status')).playing,'Pause failed');
 await page.screenshot({path:path.join(f.report,'timing-4k.png')});
 await tempo.fill('180');await tempo.press('Escape');assert.equal(await tempo.inputValue(),'60');assert.equal((await call('revisions.get',{project_id:p.id})).id,rev.id);
 await page.getByLabel('工程拍号').selectOption('custom');await page.getByLabel('拍号分子').fill('5');await page.getByLabel('拍号分母').selectOption('16');await page.locator('.timing-custom').getByRole('button',{name:'应用',exact:true}).click();
 await until(async()=>await page.getByLabel('工程拍号').inputValue()==='5/16','Custom meter failed');
 await until(async()=>await page.getByRole('button',{name:'撤销',exact:true}).isEnabled(),'Undo not ready');await page.getByRole('button',{name:'撤销',exact:true}).click();await until(async()=>await page.getByLabel('工程拍号').inputValue()==='3/4','Undo meter failed');
 await until(async()=>await page.getByRole('button',{name:'重做',exact:true}).isEnabled(),'Redo not ready');await page.getByRole('button',{name:'重做',exact:true}).click();await until(async()=>await page.getByLabel('工程拍号').inputValue()==='5/16','Redo meter failed');
 await page.reload();await page.locator('.studio').waitFor();assert.equal(await tempo.inputValue(),'60');assert.equal(await page.getByLabel('工程拍号').inputValue(),'5/16');
 report.checks.push('Create 6/8; real native fixture audio; tempo change while playing; meter change preserves notes; Escape cancels; custom meter survives reload');
 for(const width of [1536,1120]){await page.setViewportSize({width,height:864});const boxes=await page.locator('.studio-toolbar .project-timing,.studio-toolbar .window-controls').evaluateAll(nodes=>nodes.map(n=>({left:n.getBoundingClientRect().left,right:n.getBoundingClientRect().right})));assert(boxes.length===2);assert(boxes[0].right<=boxes[1].left,'Timing overlaps window controls');assert(boxes[1].right<=width+1,'Toolbar exceeds viewport');await page.screenshot({path:path.join(f.report,`timing-${width}.png`)});}
 assert.equal((await call('jobs.list',{project_id:p.id})).length,0);assert.deepEqual(report.errors,[]);
 report.checks.push('4K and 1120px toolbar boundaries; no music-model or render jobs');
}finally{await context.close();await browser.close();await writeFile(path.join(f.report,'report.json'),JSON.stringify(report,null,2));}
