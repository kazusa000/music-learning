import {sandboxAudition} from './audition-sandbox.mjs';
// Complete existing song view regression. Never edits or renders the user's project.
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const [tokenFile,projectId,output]=process.argv.slice(2);await mkdir(output,{recursive:false});
const token=(await readFile(tokenFile,'ascii')).trim(),base='http://127.0.0.1:8765';
async function api(operation,args={}){const r=await fetch(base+'/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({operation,arguments:args})});const body=await r.json();assert.ok(body.ok);return body.result;}
const before=await api('revisions.get',{project_id:projectId}),versions=await api('revisions.list',{project_id:projectId,limit:1000});
const browser=await chromium.launch({headless:true}),errors=[],writes=[];
try{
 const page=await browser.newPage({viewport:{width:1680,height:1120}});page.on('pageerror',e=>errors.push(String(e)));
 page.on('request',r=>{if(r.url().endsWith('/v1/commands')){const b=r.postDataJSON();if(['edits.apply','revisions.restore','jobs.submit','dialogue.submit'].includes(b.operation))writes.push(b.operation);}});
 await sandboxAudition(page,base,token);await page.goto(base+`/?project=${projectId}&view=workbench#token=${token}`);await page.getByLabel('制作编辑器',{exact:true}).waitFor();
 assert.equal(await page.locator('.daw-playlist-row').count(),before.snapshot.tracks.length);
 assert.ok(await page.locator('.daw-playlist-ruler text').count()<45);
 await page.screenshot({path:path.join(output,'playlist.png'),fullPage:true});
 const piano=before.snapshot.tracks.find(t=>t.role!=='drums'&&t.instrument_candidates.includes('GM:0'))??before.snapshot.tracks[0];
 await page.getByLabel('编辑声部',{exact:true}).selectOption(piano.id);await page.getByRole('button',{name:'钢琴卷帘',exact:true}).click();
 const count=before.snapshot.notes.filter(n=>n.track_id===piano.id).length;assert.equal(await page.locator('.daw-piano-grid g[data-note-id]').count(),count);
 const zoom=page.getByLabel('时间轴缩放',{exact:true});await zoom.focus();await zoom.press('Home');for(let i=0;i<23;i++)await zoom.press('ArrowRight');
 await page.getByLabel('时间轴平移',{exact:true}).focus();await page.getByLabel('时间轴平移',{exact:true}).press('End');
 await page.screenshot({path:path.join(output,'piano-roll-ending.png'),fullPage:true});
 await page.getByLabel('循环起点秒',{exact:true}).fill('60');await page.getByLabel('循环终点秒',{exact:true}).fill('75');
 await page.getByRole('tab',{name:'版本',exact:true}).click();const baseline=versions.find(v=>v.id!==before.id);
 if(baseline){await page.getByLabel('工程版本',{exact:true}).selectOption(baseline.id);await page.waitForFunction(id=>document.querySelector('.studio')?.dataset.revision===id,baseline.id);}
 await page.getByLabel('工程版本',{exact:true}).selectOption('head');await page.waitForFunction(id=>document.querySelector('.studio')?.dataset.revision===id,before.id);
 assert.equal(await page.getByLabel('循环起点秒',{exact:true}).inputValue(),'60');assert.equal(await page.getByLabel('循环终点秒',{exact:true}).inputValue(),'75');
 await page.getByRole('tab',{name:'讨论',exact:true}).click();await page.getByLabel('上下文与设置',{exact:true}).click();await page.getByLabel('讨论上下文',{exact:true}).selectOption('selection');await page.getByLabel('上下文与设置',{exact:true}).click();assert.equal(await page.getByLabel('讨论上下文',{exact:true}).inputValue(),'selection');
 await page.getByRole('button',{name:'混音台',exact:true}).click();assert.equal(await page.locator('.daw-mixer-strip').count(),before.snapshot.tracks.length);
 await page.screenshot({path:path.join(output,'mixer.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);const after=await api('revisions.get',{project_id:projectId});assert.equal(after.id,before.id);assert.equal(after.snapshot_sha256,before.snapshot_sha256);
 const report={status:'succeeded',project_id:projectId,revision:before.id,duration_seconds:before.snapshot.duration_samples/before.snapshot.sample_rate,total_notes:before.snapshot.notes.length,selected_piano_notes:count,tracks:before.snapshot.tracks.length,head_unchanged:true,snapshot_unchanged:true,selection_preserved:true,page_errors:errors,production_writes:writes,provider_calls:0,mobile_overflow:false};
 await writeFile(path.join(output,'summary.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}
