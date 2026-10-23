import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
// The source under test may be an immutable release without node_modules.
// Resolve only the installed test dependency from the runner's resource root.
const dependencyAnchor=process.env.MUSIC_LEARNING_TEST_RESOURCE_ROOT
 ?path.join(process.env.MUSIC_LEARNING_TEST_RESOURCE_ROOT,'apps/web/package.json'):import.meta.url;
const {chromium}=createRequire(dependencyAnchor)('playwright');
const root=path.resolve(process.argv[2]),f=JSON.parse(await readFile(path.join(root,'fixture.json'),'utf8'));
const base='http://127.0.0.1:'+f.port,report={checks:[],errors:[]},out=f.report;
async function call(operation,args={},revision){const r=await fetch(base+'/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+f.token,'Content-Type':'application/json'},body:JSON.stringify({operation,arguments:args,expected_revision:revision??null,idempotency_key:crypto.randomUUID().replaceAll('-','')})});const data=await r.json();assert(data.ok,JSON.stringify(data.error));return data.result;}
async function until(fn,message){const stop=Date.now()+12000;while(Date.now()<stop){if(await fn())return;await new Promise(r=>setTimeout(r,60));}throw Error(message);}
const browser=await chromium.launch({channel:'msedge',headless:true});
const context=await browser.newContext({viewport:{width:1536,height:864},deviceScaleFactor:2.5}),page=await context.newPage();page.setDefaultTimeout(10000);
page.on('pageerror',e=>report.errors.push(e.message));
async function aligned(){const pairs=await page.evaluate(()=>[...document.querySelectorAll('.track-card')].map(card=>{const row=document.querySelector(`[data-track-id="${card.dataset.track}"]`);return row?{id:card.dataset.track,left:card.getBoundingClientRect().top,right:row.getBoundingClientRect().top,lh:card.getBoundingClientRect().height,rh:row.getBoundingClientRect().height}:null;}).filter(Boolean));assert(pairs.length);for(const p of pairs){assert(Math.abs(p.left-p.right)<1.1,JSON.stringify(p));assert(Math.abs(p.lh-p.rh)<1.1,JSON.stringify(p));}}
try{
 await page.goto(base+'/#token='+f.token);await page.getByRole('heading',{name:'工作室',exact:true}).waitFor();
 await page.screenshot({path:path.join(out,'home-4k.png')});
 await page.getByRole('button',{name:'从零编曲',exact:false}).click();
 await page.getByLabel('作品名称',{exact:true}).fill('全新作品');await page.getByLabel('初始速度').fill('96');await page.getByLabel('初始小节数').fill('16');
 await page.getByRole('button',{name:'进入编曲',exact:true}).click();await page.locator('.studio').waitFor();
 const project=(await call('projects.list',{limit:1000})).find(p=>p.title==='全新作品');assert(project);
 let rev=await call('revisions.get',{project_id:project.id});assert.equal(rev.snapshot.source_asset_id,null);assert.equal(rev.snapshot.timing_map.meters[0].numerator,4);
 assert.equal(await page.getByRole('button',{name:'原曲',exact:true}).isVisible(),false);
 assert.equal(await page.getByRole('tab',{name:'分析',exact:true}).count(),0);await page.getByRole('tab',{name:'笔记',exact:true}).click();
 await page.getByLabel('笔记内容').fill('# 开头\n钢琴与贝斯，留出旋律空间。');await page.getByRole('button',{name:'保存笔记',exact:true}).click();
 await until(async()=>(await call('notes.list',{project_id:project.id})).length===1,'Notebook not saved');
 await page.getByRole('button',{name:'预览',exact:true}).click();await page.locator('.notebook-preview h1').waitFor();
 await page.getByRole('tab',{name:'讨论',exact:true}).click();assert.equal(await page.getByRole('button',{name:'改编',exact:true}).count(),0);
 const mode=page.getByLabel('思考强度',{exact:true}),fast=page.getByRole('button',{name:'Codex 快速模式',exact:true});assert.equal(await mode.inputValue(),'low');
 const beforeHeight=await page.locator('.discussion-compose').evaluate(n=>n.getBoundingClientRect().height);
 await mode.selectOption('medium');await until(async()=>(await call('application.settings.get')).preferences.reasoning_effort==='medium','Reasoning preference not saved');
 await fast.click();await until(async()=>(await call('application.settings.get')).preferences.fast_mode===true,'Codex Fast mode not saved');assert.equal(await mode.inputValue(),'medium');
 let submitted;const intercept=async route=>{const body=route.request().postDataJSON();if(body.operation==='dialogue.submit'){submitted=body.arguments;await route.fulfill({json:{ok:true,result:{}}});}else await route.continue();};
 await page.route('**/v1/commands',intercept);await page.getByLabel('讨论问题',{exact:true}).fill('用思考模式检查配器');await page.getByRole('button',{name:'发送',exact:false}).click();
 await until(async()=>!!submitted,'No dialogue request');assert.equal(submitted.reasoning_effort,'medium');assert.equal(submitted.fast_mode,true);await page.unroute('**/v1/commands',intercept);
 assert.equal(await page.locator('.discussion-compose').evaluate(n=>n.getBoundingClientRect().height),beforeHeight);
 await page.screenshot({path:path.join(out,'reasoning-4k.png')});
 report.checks.push('Create composition from home; no original; Markdown notebook persists; unified AI');
 await until(async()=>(await call('engine.status')).status==='ready','Empty timeline not ready');
 await page.getByRole('button',{name:'播放',exact:true}).click();await until(async()=>{const s=await call('engine.status');return s.playing&&s.position_sample>0;},'Empty timeline clock failed');
 await page.getByRole('button',{name:'暂停',exact:true}).click();
 await until(async()=>(await call('audition.get',{project_id:project.id})).desired?.playing===false,'Pause not persisted');
 await page.getByLabel('添加乐器轨道',{exact:true}).click();await until(async()=>page.locator('.track-card').count().then(n=>n===2),'Add track failed');
 await until(async()=>{const head=await call('revisions.get',{project_id:project.id});return await page.locator('.studio').getAttribute('data-revision')===head.id&&await page.locator('.track-select[draggable=true]').count()===2;},'Track edit not settled');
 await aligned();
 const first=page.locator('.track-card').nth(0),second=page.locator('.track-card').nth(1),ids=(await call('revisions.get',{project_id:project.id})).snapshot.track_order;
 await first.locator('.track-select').dragTo(second,{targetPosition:{x:80,y:110}});
 await until(async()=>(await call('revisions.get',{project_id:project.id})).snapshot.track_order[0]===ids[1],'Track drag not persisted');
 await aligned();await page.getByLabel('编排轨道高度').fill('192');await aligned();
 await page.getByRole('button',{name:'专注编辑',exact:true}).click();assert.equal(await page.locator('.track-panel').isVisible(),false);
 await page.getByRole('button',{name:'恢复侧栏',exact:true}).click();await aligned();
 await page.screenshot({path:path.join(out,'composition-4k.png')});
 await page.reload();await page.locator('.studio').waitFor();await until(async()=>page.locator('.track-card').count().then(n=>n===2),'Composition reopen failed');
 await until(async()=>page.getByLabel('思考强度',{exact:true}).inputValue().then(v=>v==='medium'),'Reasoning mode not restored');
 assert.equal(await fast.getAttribute('aria-pressed'),'true');await fast.click();await until(async()=>(await call('application.settings.get')).preferences.fast_mode===false,'Fast mode off not saved');assert.equal(await mode.inputValue(),'medium');
 report.checks.push('Independent reasoning and Codex Fast controls; both sent and restored; composer height unchanged');
 assert.deepEqual((await call('revisions.get',{project_id:project.id})).snapshot.track_order,[ids[1],ids[0]]);
 await page.getByRole('tab',{name:'笔记',exact:true}).click();await page.locator('.notebook-list button').first().click();await until(async()=>page.getByLabel('笔记内容').inputValue().then(s=>s.includes('钢琴与贝斯')),'Notes reopen failed');
 report.checks.push('Empty native transport; add track; drag persists on reopen; aligned vertical zoom');
 // Attach the already-inspected E-drive fixture to the newly created composition.
 rev=await call('revisions.get',{project_id:project.id});const tid=rev.snapshot.track_order[0],provenance={origin:'rule',source_id:'studio-ui'};
 for(const sha of [f.template.state_asset_id,f.template.descriptor_asset_id]){
   const bytes=await (await fetch(base+'/v1/assets/'+sha,{headers:{Authorization:'Bearer '+f.token}})).arrayBuffer();
   const uploaded=await (await fetch(base+'/v1/uploads',{method:'PUT',headers:{Authorization:'Bearer '+f.token,'Content-Type':'application/octet-stream','Idempotency-Key':crypto.randomUUID()},body:bytes})).json();assert(uploaded.ok);
   await call('assets.register',{project_id:project.id,upload_id:uploaded.result.upload_id,provenance},rev.id);
 }
 const plugin={...f.template,id:crypto.randomUUID().replaceAll('-',''),track_id:tid};
 rev=await call('edits.apply',{project_id:project.id,scope:{track_ids:[tid],start_sample:0,end_sample:rev.snapshot.duration_samples,structural:true},operations:[{op:'put',collection:'plugins',entity:plugin},{op:'put',collection:'notes',entity:{id:crypto.randomUUID().replaceAll('-',''),track_id:tid,pitch:60,start_sample:0,end_sample:44100*10,velocity:.5,provenance}},{op:'put',collection:'tracks',entity:{...rev.snapshot.tracks.find(t=>t.id===tid),gain_db:-90}}],description:'Test real MIDI on a fresh composition'},rev.id);
 await until(async()=>(await call('engine.status')).applied_revision===rev.id,'New composition MIDI not ready');await page.getByRole('button',{name:'播放',exact:true}).click();
 await until(async()=>{const s=await call('engine.status');return s.playing&&s.meters[tid]>0;},'New composition did not produce actual audio');
 await page.getByLabel('音轨音量数值 1',{exact:true}).fill('-80');await page.getByLabel('音轨音量数值 1',{exact:true}).press('Tab');
 await page.getByLabel('试听静音 1',{exact:true}).click();await until(async()=>(await call('engine.status')).meters[tid]===0,'Mute during playback failed');
 await page.getByLabel('试听静音 1',{exact:true}).click();await until(async()=>(await call('engine.status')).meters[tid]>0,'Unmute failed');
 assert.equal((await call('jobs.list',{project_id:project.id})).length,0);
 await page.getByRole('button',{name:'暂停',exact:true}).click();
 await until(async()=>(await call('audition.get',{project_id:project.id})).desired?.playing===false,'Pause not saved');
 await page.getByRole('tab',{name:'版本',exact:true}).click();await page.getByRole('button',{name:'保存版本',exact:true}).click();await page.locator('.version-card').first().waitFor();
 await page.getByRole('button',{name:'设为 A',exact:true}).click();await until(async()=>(await call('engine.status')).playing===false,'Version comparison failed');
 assert.equal(await page.getByRole('button',{name:'原曲',exact:true}).isVisible(),false);await page.getByRole('button',{name:/编曲 B/}).click();
 report.checks.push('Fresh composition real native MIDI audio and live volume/mute; no renders');
 await page.goto(base+'/?project='+f.project.id+'&view=workbench');await page.locator('.studio').waitFor();await page.locator('.track-card').nth(9).waitFor();await aligned();
 assert.equal(await page.locator('.daw-playlist-row svg text').count(),0,'No writing over clips or notes');
 await page.locator('.daw-playlist-scroll').evaluate(n=>n.scrollTop=450);await page.waitForTimeout(100);await aligned();
 await page.evaluate(()=>{
   window.__linkedFrames={count:0,maxDrift:0,running:true};
   const sample=()=>{const result=window.__linkedFrames;if(!result.running)return;const card=document.querySelector('.track-card'),row=document.querySelector(`[data-track-id="${card.dataset.track}"]`);result.count++;result.maxDrift=Math.max(result.maxDrift,Math.abs(card.getBoundingClientRect().top-row.getBoundingClientRect().top));requestAnimationFrame(sample);};requestAnimationFrame(sample);
 });
 for(const selector of ['.track-cards','.daw-playlist-scroll']){const rect=await page.locator(selector).boundingBox();await page.mouse.move(rect.x+rect.width/2,rect.y+rect.height/2);for(const dy of [90,180,-60,250,-180,500,500,500,-160,-500,-500,-500]){await page.mouse.wheel(0,dy);await page.waitForTimeout(18);}await page.waitForTimeout(260);await aligned();}
 const frames=await page.evaluate(()=>{window.__linkedFrames.running=false;return window.__linkedFrames;});assert(frames.count>=20);assert(frames.maxDrift<1.1,JSON.stringify(frames));report.linkedScroll=frames;
 assert.equal(await page.locator('.track-cards').evaluate(n=>n.scrollTop),0,'Track controls have no independent scroll position');
 const height=await page.getByLabel('编排轨道高度').inputValue();const b=await page.locator('.daw-playlist-scroll').boundingBox();await page.mouse.move(b.x+b.width/2,b.y+100);await page.keyboard.down('Alt');await page.mouse.wheel(0,-70);await page.keyboard.up('Alt');await page.waitForTimeout(100);assert(+await page.getByLabel('编排轨道高度').inputValue()>+height);await aligned();
 await page.screenshot({path:path.join(out,'reconstruction-4k.png')});
 await page.getByRole('button',{name:'原曲',exact:true}).click();await page.locator('.source-arrangement').waitFor();
 const alignment=await page.evaluate(()=>({left:document.querySelector('.track-card').getBoundingClientRect().top,right:document.querySelector('.source-wave-surface').getBoundingClientRect().top}));assert(Math.abs(alignment.left-alignment.right)<1.1,JSON.stringify(alignment));
 await page.getByRole('tab',{name:'笔记',exact:true}).click();await page.getByLabel('笔记内容').fill('原曲研究笔记');await page.getByRole('button',{name:'保存笔记',exact:true}).click();await until(async()=>(await call('notes.list',{project_id:f.project.id})).length===1,'Original notebook failed');
 await page.screenshot({path:path.join(out,'original-4k.png')});report.checks.push('Reconstruction scrolling and Alt zoom aligned; original rows and notebook');
 await page.setViewportSize({width:1120,height:720});await page.getByRole('button',{name:/重建/}).click();await page.locator('.daw-playlist-row').first().waitFor();await aligned();
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'Narrow studio overflows horizontally');
 await page.getByRole('tab',{name:'讨论',exact:true}).click();
 const layout=await page.locator('.compose-actions').evaluate(n=>({width:n.clientWidth,scroll:n.scrollWidth,boxes:[...n.children].map(c=>({top:c.getBoundingClientRect().top,bottom:c.getBoundingClientRect().bottom}))}));
 assert(layout.scroll<=layout.width+1,JSON.stringify(layout));assert(layout.boxes.every(b=>Math.abs((b.top+b.bottom)/2-(layout.boxes[0].top+layout.boxes[0].bottom)/2)<2),JSON.stringify(layout));
 await page.screenshot({path:path.join(out,'reasoning-narrow.png')});
 assert.deepEqual(report.errors,[]);report.status='succeeded';
}catch(e){report.status='failed';report.failure=String(e.stack??e);await page.screenshot({path:path.join(out,'failure.png')}).catch(()=>{});throw e;}
finally{await writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));await call('engine.close').catch(()=>{});await browser.close();}
console.log(JSON.stringify(report));
