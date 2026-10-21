import {chromium} from 'playwright';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=path.resolve(process.argv[2]),config=JSON.parse(await readFile(path.join(root,'app.local.json'),'utf8'));
assert.equal(config.port,8792);assert(config.data_root.startsWith(root));
const token=(await readFile(path.join(config.data_root,'session-token.local'),'ascii')).trim(),base='http://127.0.0.1:8792';
async function call(operation,args={},revision){const d=await(await fetch(base+'/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({operation,arguments:args,expected_revision:revision??null,idempotency_key:crypto.randomUUID().replaceAll('-','')})})).json();assert(d.ok,JSON.stringify(d.error));return d.result;}
const legacy=JSON.parse(await readFile(path.join(root,'instrumental-project.json'),'utf8')),before=await call('revisions.get',{project_id:legacy.id});
const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(base+'/?project='+legacy.id+'&view=workbench#token='+token);
 await page.locator('.mode-switch').getByRole('button',{name:'原曲',exact:true}).click();
 await page.getByRole('button',{name:'按新流程建立副本',exact:true}).waitFor();
 assert.equal((await call('revisions.get',{project_id:legacy.id})).id,before.id);
 await page.getByRole('button',{name:'返回音乐库',exact:true}).click();
 await page.getByLabel('导入人声类型').selectOption('instrumental');
 const submitted=page.waitForResponse(response=>{if(!response.url().endsWith('/v1/commands'))return false;try{const d=response.request().postDataJSON();return d.operation==='jobs.submit'&&d.arguments.kind==='asset.import';}catch{return false;}});
 await page.getByLabel('选择音乐',{exact:true}).setInputFiles(path.join(root,'upload-probe.wav'));
 const response=await submitted,request=response.request().postDataJSON(),data=await response.json();assert(data.ok,JSON.stringify(data));
 assert.equal(request.arguments.payload.vocal_mode,'instrumental');assert.equal(request.arguments.payload.preprocess,true);
 const projectId=request.arguments.project_id;await page.locator('.import-progress').waitFor({timeout:30000});assert.equal(await page.locator('.import-steps li').count(),9);
 if(await page.locator('.import-progress').getByRole('button',{name:'取消',exact:true}).count())await page.locator('.import-progress').getByRole('button',{name:'取消',exact:true}).click();
 let jobs=[];for(let i=0;i<30;i++){jobs=await call('jobs.list',{project_id:projectId});const active=jobs.filter(j=>['queued','running','cancel_requested'].includes(j.status));if(!active.length)break;const current=await call('projects.get',{project_id:projectId});for(const j of active)if(j.status!=='cancel_requested')await call('jobs.cancel',{job_id:j.id},current.current_revision);await new Promise(r=>setTimeout(r,100));}
 assert(!jobs.some(j=>['queued','running','cancel_requested'].includes(j.status)));
 assert.equal((await call('revisions.get',{project_id:legacy.id})).snapshot_sha256,before.snapshot_sha256);
 assert.equal(errors.length,0,errors.join('\n'));
 await writeFile(path.join(root,'import-browser.json'),JSON.stringify({status:'passed',projectId,vocalMode:'instrumental',preprocess:true,progressStages:9,legacyCopyEntry:true,legacyUnchanged:true,jobs:jobs.map(j=>({kind:j.kind,status:j.status})),errors},null,2));
 console.log('Frontend import mode, progress and legacy copy entry passed');
}finally{await browser.close();}
