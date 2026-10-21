import {chromium} from 'playwright';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=path.resolve(process.argv[2]),config=JSON.parse(await readFile(path.join(root,'app.local.json'),'utf8'));
assert.equal(config.port,8792);assert(config.data_root.startsWith(root));
const project=JSON.parse(await readFile(path.join(root,'instrumental-real-project.json'),'utf8'));
const token=(await readFile(path.join(config.data_root,'session-token.local'),'ascii')).trim(),base='http://127.0.0.1:8792';
async function call(operation,args={}){const d=await(await fetch(base+'/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({operation,arguments:args})})).json();assert(d.ok,JSON.stringify(d.error));return d.result;}
const rev=await call('revisions.get',{project_id:project.id});
assert(!rev.snapshot.source_reference.has_vocals);assert.equal(rev.snapshot.source_reference.tracks.length,1);
const jobs=await call('jobs.list',{project_id:project.id}),preprocess=jobs.find(j=>j.kind==='project.preprocess'&&j.status==='succeeded');assert(preprocess);
const stages=await call('jobs.stages',{job_id:preprocess.id}),separated=stages.find(s=>s.stage==='separate');
assert(separated.manifest.summary.separation_skipped);assert.deepEqual(separated.manifest.files.map(f=>f.name),['decision.json']);
const browser=await chromium.launch({channel:'msedge',headless:true,args:['--autoplay-policy=no-user-gesture-required']});
const context=await browser.newContext({viewport:{width:1536,height:864},deviceScaleFactor:2.5}),page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(30000);
try{
 await page.goto(base+'/?project='+project.id+'&view=workbench#token='+token);
 await page.locator('.mode-switch').getByRole('button',{name:'原曲',exact:true}).click();
 await page.getByLabel('原曲编排',{exact:true}).waitFor();
 assert.equal(await page.locator('.track-card').count(),1);
 assert.equal(await page.getByLabel('原曲音频波形与人声旋律').getAttribute('data-tracks'),'1');
 await page.getByRole('button',{name:'播放',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.studio').dataset.playing==='true');
 await page.waitForFunction(()=>Object.entries(JSON.parse(document.querySelector('.studio').dataset.liveLevels)).some(([id,v])=>id.startsWith('original:')&&v>-59));
 assert.equal(+(await page.locator('.studio').getAttribute('data-live-voices')),1);
 const starts=await page.locator('.studio').getAttribute('data-audio-starts');
 await page.getByLabel('试听静音 1',{exact:true}).click();
 await page.waitForFunction(()=>Object.entries(JSON.parse(document.querySelector('.studio').dataset.liveLevels)).filter(([id])=>id.startsWith('original:')).every(([,v])=>v<=-59));
 await page.getByLabel('试听静音 1',{exact:true}).click();
 await page.getByLabel('音轨音量数值 1',{exact:true}).fill('-6');await page.getByLabel('音轨音量数值 1',{exact:true}).press('Tab');
 assert.equal(await page.locator('.studio').getAttribute('data-audio-starts'),starts);
 await page.getByRole('button',{name:'暂停',exact:true}).click();
 assert.equal((await call('revisions.get',{project_id:project.id})).id,rev.id);
 assert.deepEqual(await call('jobs.list',{project_id:project.id}),jobs);
 assert.equal(errors.length,0,errors.join('\n'));
 await page.screenshot({path:path.join(root,'instrumental-4k.png')});
 await writeFile(path.join(root,'pure-browser.json'),JSON.stringify({status:'passed',oneWaveTrack:true,separationNotCalled:true,nonzeroPlayback:true,muteVolumeWhilePlaying:true,noNewJobsOrRevision:true,errors},null,2));
 console.log('Pure original workspace passed');
}catch(error){await page.screenshot({path:path.join(root,'pure-failure.png')});await writeFile(path.join(root,'pure-browser-error.json'),JSON.stringify({message:error.message,errors},null,2));throw error;}
finally{await browser.close();}
