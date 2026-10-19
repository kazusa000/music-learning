// Full current product flow: local song -> real seven-stage models -> actual discussion/edit/render.
import {chromium} from 'playwright';
import {readFile,stat,mkdir,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import assert from 'node:assert/strict';
const [tokenFile,source,output]=process.argv.slice(2);
if(!output||[source,output].some(v=>v.startsWith('\\\\')))throw new Error('Use previously copied local music and a new local output');
await mkdir(output,{recursive:false});
const originalStat=await stat(source),sourceHash=createHash('sha256').update(await readFile(source)).digest('hex');
const token=(await readFile(tokenFile,'ascii')).trim();
const browser=await chromium.launch({headless:true});
const errors=[];const report={phase:'P6',status:'running',source_sha256:sourceHash,nas_accessed:false,raw_audio_sent_to_agent:false};
const save=async(name,value)=>writeFile(path.join(output,name),JSON.stringify(value,null,2));
try{
 const page=await browser.newPage({viewport:{width:1600,height:1120}});
 page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(`http://127.0.0.1:8765/#token=${token}`);
 await page.getByLabel('选择音乐',{exact:true}).waitFor();
 const api=async(operation,args={},expected)=>{const r=await page.request.post('http://127.0.0.1:8765/v1/commands',{data:{operation,arguments:args,expected_revision:expected??null,idempotency_key:expected?crypto.randomUUID():null}});const d=await r.json();assert.ok(d.ok,JSON.stringify(d));return d.result;};
 const created=page.waitForResponse(r=>r.url().endsWith('/v1/commands')&&r.request().postDataJSON()?.operation==='projects.create');
 await page.getByLabel('选择音乐',{exact:true}).setInputFiles(source);
 const project=(await (await created).json()).result;report.project_id=project.id;
 await save('owner.json',report);
 const began=Date.now();let previous='',pipeline;
 while(true){
  const jobs=await api('jobs.list',{project_id:project.id,limit:1000});
  const failed=jobs.find(j=>['failed','cancelled'].includes(j.status));assert.ok(!failed,JSON.stringify(failed?.error));
  pipeline=jobs.find(j=>j.kind==='project.preprocess');
  if(pipeline){
   const stages=await api('jobs.stages',{job_id:pipeline.id});
   const state=stages.map(s=>`${s.stage}:${s.status}${s.progress?.completed?':'+s.progress.completed:''}`).join(',');
   if(state!==previous){previous=state;console.log(JSON.stringify({pipeline:state}));await save('pipeline-progress.json',{job:pipeline,stages});}
   if(pipeline.status==='succeeded'){report.preprocess_job_id=pipeline.id;report.preprocess_seconds=(Date.now()-began)/1000;break;}
  }
  assert.ok(Date.now()-began<3600000,'Whole-song preprocessing exceeded bounded probe time');
  await new Promise(r=>setTimeout(r,2000));
 }
 const revision=await api('revisions.get',{project_id:project.id});report.initial_revision=revision.id;
 assert.equal((await api('projects.get',{project_id:project.id})).status,'ready');
 assert.equal(revision.snapshot.source_asset_id,sourceHash);
 const stages=await api('jobs.stages',{job_id:pipeline.id});assert.equal(stages.length,7);assert.ok(stages.every(s=>s.status==='succeeded'));
 await page.getByRole('button',{name:/打开音乐工作台/}).click();
 const studio=page.getByRole('region',{name:'音乐工作台'});await studio.waitFor();
 await page.waitForFunction(()=>Number(document.querySelector('.piano-roll')?.dataset.waveformBuckets)>0);
 await page.getByLabel('Codex 登录方式',{exact:true}).selectOption('existing');
 await page.getByLabel('讨论问题',{exact:true}).fill('请查询整曲summary，并按需读取至多12条annotations。概括主要配器候选、结构和一处值得核对的和声，说明模型候选与理论解释的区别，附实际工程位置引用。不修改，250字以内。');
 await page.getByRole('button',{name:'发送 ↗',exact:true}).click();
 await page.waitForFunction(()=>document.querySelectorAll('.discussion-turn').length===1);
 const discussionId=await page.locator('.discussion-turn').last().getAttribute('data-turn');
 await page.waitForFunction(id=>['succeeded','failed','cancelled'].includes(document.querySelector(`[data-turn="${id}"]`)?.dataset.status),discussionId,{timeout:240000});
 const discussion=await api('dialogue.get',{project_id:project.id,turn_id:discussionId});await save('discussion.json',discussion);assert.equal(discussion.status,'succeeded',JSON.stringify(discussion.error));
 const bassIndex=revision.snapshot.tracks.findIndex(t=>t.instrument_candidates?.includes('GM:33'));assert.ok(bassIndex>=0);
 await page.locator('.track-select').nth(bassIndex).click();
 await page.getByLabel('循环起点秒',{exact:true}).fill('60');await page.getByLabel('循环终点秒',{exact:true}).fill('75');
 await page.getByRole('button',{name:'允许改编模式',exact:true}).click();
 await page.getByLabel('讨论问题',{exact:true}).fill('查询该贝斯声部选区内events（limit=12），只把按起音顺序最早三个完整位于60–75秒内的音符升一个半音、力度乘0.9，起音/时值和其他事件不动。提交一次编辑并回读，给出新版本实际引用，说明渲染排队状态。');
 await page.getByRole('button',{name:'改编 ↗',exact:true}).click();
 await page.waitForFunction(()=>document.querySelectorAll('.discussion-turn').length===2);
 const editId=await page.locator('.discussion-turn').last().getAttribute('data-turn');
 await page.waitForFunction(id=>['succeeded','failed','cancelled'].includes(document.querySelector(`[data-turn="${id}"]`)?.dataset.status),editId,{timeout:240000});
 const edit=await api('dialogue.get',{project_id:project.id,turn_id:editId});await save('edit.json',edit);assert.equal(edit.status,'succeeded',JSON.stringify(edit.error));
 const receipt=edit.context.arrangement;assert.ok(receipt);report.edit_turn_id=editId;report.discussion_turn_id=discussionId;report.final_revision=receipt.revision_id;report.render_job_id=receipt.render_job_id;
 let rendered;const renderStart=Date.now();
 while(true){rendered=await api('jobs.get',{job_id:receipt.render_job_id});if(['succeeded','failed','cancelled'].includes(rendered.status))break;assert.ok(Date.now()-renderStart<600000);await new Promise(r=>setTimeout(r,1000));}
 await save('render.json',rendered);assert.equal(rendered.status,'succeeded',JSON.stringify(rendered.error));
 const after=await api('revisions.get',{project_id:project.id}),old=new Map(revision.snapshot.notes.map(n=>[n.id,n])),next=new Map(after.snapshot.notes.map(n=>[n.id,n]));
 const changed=[...old.keys()].filter(id=>JSON.stringify(old.get(id))!==JSON.stringify(next.get(id)));
 assert.equal(changed.length,3);assert.equal(next.size,old.size);
 const rate=revision.snapshot.sample_rate;
 for(const id of changed){const a=old.get(id),b=next.get(id);assert.equal(b.pitch,a.pitch+1);assert.ok(Math.abs(b.velocity-a.velocity*.9)<1e-7);assert.equal(a.start_sample,b.start_sample);assert.equal(a.end_sample,b.end_sample);assert.equal(a.track_id,revision.snapshot.tracks[bassIndex].id);assert.ok(a.start_sample>=60*rate&&a.end_sample<=75*rate);}
 assert.equal(after.id,receipt.revision_id);
 await page.waitForFunction(id=>document.querySelector('.studio')?.dataset.revision===id,after.id);
 await page.locator(`[data-arrangement-revision="${after.id}"]`).getByText('● 试听已生成').waitFor({timeout:30000});
 await page.screenshot({path:path.join(output,'workbench.png'),fullPage:true});
 assert.deepEqual(errors,[]);
 const endStat=await stat(source);assert.equal(endStat.size,originalStat.size);assert.equal(endStat.mtimeMs,originalStat.mtimeMs);assert.equal(createHash('sha256').update(await readFile(source)).digest('hex'),sourceHash);
 report.status='succeeded';report.initial_notes=old.size;report.final_notes=next.size;report.changed_notes=changed.length;report.source_unchanged=true;report.page_errors=errors;
 await save('summary.json',report);console.log(JSON.stringify(report));
}catch(error){await save('failure.json',{...report,error:String(error)});throw error;}finally{await browser.close();}
