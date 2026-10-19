// Real current-song playback; deliberately slow persistence to prove audio
// switching does not wait for SQLite/API. No edits or provider calls.
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const [tokenFile,output]=process.argv.slice(2),base='http://127.0.0.1:8765';
await mkdir(output,{recursive:false});
const token=(await readFile(tokenFile,'ascii')).trim();
const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1600,height:1000}});
const errors=[],writes=[],assets=[];
await page.addInitScript(()=>{
  window.audioEvents=[];
  const create=AudioContext.prototype.createGain;
  AudioContext.prototype.createGain=function(){const node=create.call(this),set=node.gain.setTargetAtTime.bind(node.gain);
    node.gain.setTargetAtTime=(value,...args)=>{window.audioEvents.push({at:performance.now(),value});return set(value,...args);};return node;};
});
page.on('pageerror',error=>errors.push(String(error)));
page.on('console',m=>{if(['error','warning'].includes(m.type()))console.log('console',m.text());});page.on('requestfailed',r=>console.log('failed',r.url().split('/').slice(-2).join('/'),r.failure()));
page.on('request',r=>{if(r.url().includes('/v1/assets/'))assets.push(r.url());if(r.url().endsWith('/v1/commands')){const op=r.postDataJSON().operation;if(['edits.apply','dialogue.submit','jobs.submit','revisions.restore'].includes(op))writes.push(op);}});
await page.route('**/v1/commands',async route=>{if(route.request().postDataJSON().operation==='audition.set')await new Promise(resolve=>setTimeout(resolve,800));await route.continue();});
try{
  await page.goto(base+'/#token='+token);await page.getByRole('button',{name:'打开工程',exact:true}).waitFor();
  const homeGains=[];
  for(const name of ['原曲试听','重建试听']){
    await page.evaluate(()=>{window.audioEvents=[];});await page.getByRole('button',{name:'播放'+name,exact:true}).click();
    await page.waitForFunction(()=>window.audioEvents.some(e=>e.value>0));homeGains.push(await page.evaluate(()=>window.audioEvents.find(e=>e.value>0).value));
    await page.getByRole('button',{name:'暂停'+name,exact:true}).click();
  }
  assert.ok(Math.abs((-10.85+20*Math.log10(homeGains[0]))-(-24.25+20*Math.log10(homeGains[1])))<.02);
  await page.getByRole('button',{name:'打开工程',exact:true}).click();
  await page.getByRole('button',{name:'钢琴卷帘',exact:true}).click();await page.locator('.daw-piano-grid [data-note-id]').first().waitFor();
  for(let i=0;i<5;i++)await page.getByLabel('放大时间轴',{exact:true}).click();
  const pan=page.getByLabel('时间轴平移',{exact:true});await pan.focus();await page.keyboard.press('End');
  const before=+(await pan.inputValue());await page.getByLabel('缩小时间轴',{exact:true}).click();assert.ok(+(await pan.inputValue())>0);assert.ok(before>0);
  await page.getByLabel('循环起点秒',{exact:true}).fill('60');await page.getByLabel('循环终点秒',{exact:true}).fill('75');
  await page.getByRole('button',{name:'选区',exact:true}).click();assert.ok(Math.abs(+(await pan.inputValue())-60)<.01);
  const frame=page.getByLabel('制作编辑器',{exact:true}),bounds=await frame.boundingBox();
  const z=2**+(await page.getByLabel('时间轴缩放',{exact:true}).inputValue()),start=+(await pan.inputValue());
  const px=bounds.x+48+(bounds.width-58)*.7,py=bounds.y+180;
  await page.mouse.move(px,py);await page.keyboard.down('Control');await page.mouse.wheel(0,-80);await page.keyboard.up('Control');
  await page.waitForFunction(old=>2**+document.querySelector('input[aria-label="时间轴缩放"]').value>old,z);
  const z2=2**+(await page.getByLabel('时间轴缩放',{exact:true}).inputValue()),s2=+(await pan.inputValue());
  assert.ok(Math.abs(start+305/z*.7-(s2+305/z2*.7))<.1,'pointer anchor preserved');
  await page.keyboard.down('Shift');await page.mouse.wheel(0,120);await page.keyboard.up('Shift');
  await page.waitForFunction(old=>+document.querySelector('input[aria-label="时间轴平移"]').value>old,s2);
  const pitch=page.getByLabel('音高缩放',{exact:true});await pitch.focus();await page.keyboard.press('End');assert.equal(await pitch.inputValue(),'30');
  await page.getByRole('button',{name:'整曲',exact:true}).click();assert.equal(await pan.inputValue(),'0');
  const narrow=(await frame.boundingBox()).width;await page.getByRole('button',{name:'专注编辑',exact:true}).click();assert.ok((await frame.boundingBox()).width>narrow+300);await page.getByRole('button',{name:'恢复侧栏',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.stem-preload')?.textContent==='声部 6/6',null,{timeout:60000});
  await page.locator('.mode-switch button').filter({hasText:'重建'}).click();await page.getByRole('button',{name:'播放',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.studio')?.getAttribute('data-playing')==='true');
  const fetched=assets.length,latencies=[];
  for(const label of ['独奏 1','独奏 2','试听静音 3']){
    const mark=await page.evaluate(()=>{window.audioEvents=[];return performance.now();});
    await page.getByLabel(label,{exact:true}).click();
    await page.waitForFunction(()=>window.audioEvents.some(e=>e.value>0));
    const latency=await page.evaluate(at=>window.audioEvents.find(e=>e.value>0).at-at,mark);latencies.push(latency);assert.ok(latency<250,`local switch ${latency}ms`);
  }
  assert.equal(assets.length,fetched,'prepared switches do not fetch audio again');
  await page.getByLabel('试听电平匹配',{exact:true}).uncheck();await page.getByLabel('试听电平匹配',{exact:true}).check();
  await page.locator('.mode-switch button').filter({hasText:'重建'}).click();await page.getByRole('button',{name:'暂停',exact:true}).click();
  await page.waitForFunction(async()=>{const response=await fetch('/v1/commands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({schema_version:1,operation:'audition.get',arguments:{project_id:'4012a10f50574b2395d4ec4a9d8f27b5'},expected_revision:null,idempotency_key:null})});const data=await response.json();return data.result.desired.playing===false&&data.result.desired.mode==='mix';},null,{timeout:15000});await page.screenshot({path:path.join(output,'workbench.png'),fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
  const report={status:'succeeded',zoom_anchor:true,zoom_keeps_position:true,pitch_zoom:true,selection_fit:true,pan:true,home_matched_lufs:-10.85+20*Math.log10(homeGains[0]),focus_editor:true,preloaded_stems:6,api_delay_ms:800,switch_ms:latencies,refetch_on_switch:0,page_errors:errors,musical_writes:writes};
  await writeFile(path.join(output,'summary.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(error){console.log('failed state',await page.locator('.stem-preload').innerText(),errors);await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});throw error;}finally{await browser.close();}
