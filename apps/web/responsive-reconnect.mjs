// Owned isolated fixture only: real server restart and disconnected-client recovery.
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';
import assert from 'node:assert/strict';
const [fixtureFile,output]=process.argv.slice(2),fixture=JSON.parse(await readFile(fixtureFile,'utf8'));
const config=JSON.parse(await readFile(fixture.config_path,'utf8')),base=`http://127.0.0.1:${config.port}`;
assert.equal(config.port,8767);assert.match(config.data_root,/responsive-ui-fixture-v1/);
await mkdir(output,{recursive:false});
const token=(await readFile(path.join(config.data_root,'session-token.local'),'ascii')).trim(),root=path.resolve('../..');
async function api(operation,args={},expected){const r=await fetch(base+'/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({operation,arguments:args,expected_revision:expected,idempotency_key:expected?crypto.randomUUID():null})});const b=await r.json();assert.ok(b.ok);return b.result;}
const manage=command=>promisify(execFile)(path.join(root,'runtime/tooling/Scripts/python.exe'),['scripts/app.py',command],{cwd:root,env:{...process.env,MUSIC_APP_CONFIG:fixture.config_path},windowsHide:true});
const browser=await chromium.launch({headless:true}),context=await browser.newContext(),page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(String(e)));
try{
 await page.goto(base+`/?project=${fixture.project_id}&view=workbench#token=${token}`);
 await page.getByLabel('讨论问题',{exact:true}).waitFor();
 await context.setOffline(true);await manage('stop');await manage('start');
 const before=await api('projects.get',{project_id:fixture.project_id});
 const renamed=await api('projects.rename',{project_id:fixture.project_id,title:'Reconnect fixture'},before.current_revision);
 const revision=await api('revisions.get',{project_id:fixture.project_id});
 await api('audition.set',{project_id:fixture.project_id,state:{revision_id:revision.id,position_sample:0,mode:'original',track_ids:[],loop_start_sample:0,loop_end_sample:revision.snapshot.duration_samples,loop:false,playing:false}},renamed.current_revision);
 const began=Date.now();await context.setOffline(false);
 await page.waitForFunction(id=>document.querySelector('.studio')?.getAttribute('data-revision')===id,renamed.current_revision,{timeout:10000});
 const recoveryMs=Date.now()-began;assert.deepEqual(errors,[]);
 const report={status:'succeeded',recovery_ms:recoveryMs,revision:renamed.current_revision,server_restart:true,missed_head_recovered:true,page_errors:errors,provider_calls:0};
 await writeFile(path.join(output,'summary.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}
