// Isolated fixture: a new revision must never silently play the original recording.
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const [fixtureFile,output]=process.argv.slice(2),fixture=JSON.parse(await readFile(fixtureFile,'utf8'));
const config=JSON.parse(await readFile(fixture.config_path,'utf8'));assert.equal(config.port,8767);
const base=`http://127.0.0.1:${config.port}`,token=(await readFile(path.join(config.data_root,'session-token.local'),'ascii')).trim();
await mkdir(output,{recursive:false});
async function api(operation,args={},expected){const r=await fetch(base+'/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({operation,arguments:args,expected_revision:expected,idempotency_key:expected?crypto.randomUUID():null})});const b=await r.json();assert.ok(b.ok,JSON.stringify(b.error));return b.result;}
const before=await api('revisions.get',{project_id:fixture.project_id});
await api('audition.set',{project_id:fixture.project_id,state:{revision_id:before.id,position_sample:0,mode:'original',track_ids:[],loop_start_sample:0,loop_end_sample:before.snapshot.duration_samples,loop:false,playing:false}},before.id);
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage({viewport:{width:1600,height:980}});
 await page.goto(base+`/?project=${fixture.project_id}&view=workbench#token=${token}`);await page.getByLabel('制作编辑器',{exact:true}).waitFor();
 const note=before.snapshot.notes[0];
 const changed=await api('edits.apply',{project_id:fixture.project_id,author:'user',description:'Independent reconstruction-mode regression',scope:{track_ids:[note.track_id],start_sample:0,end_sample:before.snapshot.duration_samples,structural:false},operations:[{op:'patch_note',id:note.id,velocity:note.velocity===.7?.6:.7}]},before.id);
 await page.waitForFunction(id=>document.querySelector('.studio')?.dataset.revision===id,changed.id);
 const mix=page.locator('.mode-switch button').filter({hasText:'重建'});assert.equal(await mix.getAttribute('aria-pressed'),'true');
 await page.getByRole('button',{name:'播放',exact:true}).click();await page.getByText('此版本还没有重建音频，请先生成重建试听。',{exact:false}).waitFor();
 assert.equal(await page.locator('.studio').getAttribute('data-playing'),'false');
 await page.locator('.mode-switch button').filter({hasText:'原曲'}).click();
 await page.getByRole('button',{name:'↗ 生成重建试听',exact:true}).click();assert.equal(await mix.getAttribute('aria-pressed'),'true');
 await page.getByRole('button',{name:'分析',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('[data-render-status]')?.getAttribute('data-render-status')==='succeeded',null,{timeout:60000});
 assert.equal(await mix.getAttribute('aria-pressed'),'true');
 const report={status:'succeeded',new_revision_targets_reconstruction:true,missing_render_does_not_play_original:true,render_button_selects_reconstruction:true,completed_render_keeps_reconstruction:true,source_unchanged:changed.snapshot.source_asset_id===before.snapshot.source_asset_id,provider_calls:0};
 await writeFile(path.join(output,'summary.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}
