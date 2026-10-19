// Isolated fixture only. Real API transactions/native render; no music model or Codex provider.
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const [fixtureFile,output]=process.argv.slice(2),fixture=JSON.parse(await readFile(fixtureFile,'utf8'));
const config=JSON.parse(await readFile(fixture.config_path,'utf8')),base=`http://127.0.0.1:${config.port}`;
const token=(await readFile(path.join(config.data_root,'session-token.local'),'ascii')).trim();
await mkdir(output,{recursive:false});
async function api(operation,args={},expected){const response=await fetch(base+'/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({operation,arguments:args,expected_revision:expected??null,idempotency_key:expected?crypto.randomUUID().replaceAll('-',''):null})});const body=await response.json();assert.ok(body.ok,JSON.stringify(body.error));return body.result;}
const read=()=>api('revisions.get',{project_id:fixture.project_id});
const before=await read();
if(before.id!==fixture.seed_revision)await api('revisions.restore',{project_id:fixture.project_id,revision_id:fixture.seed_revision,description:'Reset isolated UI verification to its retained seed'},before.id);
const starting=await read();await api('audition.set',{project_id:fixture.project_id,state:{revision_id:starting.id,position_sample:0,mode:'original',track_ids:[],loop_start_sample:0,loop_end_sample:starting.snapshot.duration_samples,loop:false,playing:false}},starting.id);
const browser=await chromium.launch({headless:true}),errors=[],writes=[],steps=[];
const page=await browser.newPage({viewport:{width:1680,height:1120}});page.setDefaultTimeout(15000);
page.on('pageerror',e=>errors.push(String(e)));
page.on('request',request=>{if(request.url().endsWith('/v1/commands')){const body=request.postDataJSON();if(['edits.apply','revisions.restore','jobs.submit','dialogue.submit'].includes(body.operation))writes.push({operation:body.operation,expected_revision:body.expected_revision,idempotency:!!body.idempotency_key});}});
const note=async id=>(await read()).snapshot.notes.find(n=>n.id===id);
const editor=page.getByLabel('制作编辑器',{exact:true});
async function saved(previous){await page.waitForFunction(id=>document.querySelector('.daw-editor')?.dataset.editorRevision!==id,previous);await page.waitForFunction(()=>document.querySelector('.daw-edit-state')?.textContent.includes('可编辑'));return await read();}
async function edit(action){const previous=(await read()).id;await action();return await saved(previous);}
async function drag(locator,dx,dy=0){await locator.scrollIntoViewIfNeeded();const b=await locator.boundingBox();assert.ok(b);await page.mouse.move(b.x+b.width/2,b.y+b.height/2);await page.mouse.down();await page.mouse.move(b.x+b.width/2+dx,b.y+b.height/2+dy,{steps:5});await page.mouse.up();}
const rect=id=>page.locator(`.daw-piano-grid g[data-note-id="${id}"]>rect`).first();
const clipRect=id=>page.locator(`g[data-clip-id="${id}"]>rect`).first();
try{
  await page.goto(base+`/?project=${fixture.project_id}&view=workbench#token=${token}`);
  await editor.waitFor();await page.getByRole('button',{name:'钢琴卷帘',exact:true}).click();
  await page.getByLabel('编辑声部',{exact:true}).selectOption(fixture.melody);
  const n1=fixture.note_ids[0],n2=fixture.note_ids[1],n3=fixture.note_ids[2];
  // Cancelling a preview must not save a revision.
  const cancelHead=(await read()).id;await rect(n1).scrollIntoViewIfNeeded();let cancelBox=await rect(n1).boundingBox();
  await page.mouse.move(cancelBox.x+cancelBox.width/2,cancelBox.y+cancelBox.height/2);await page.mouse.down();await page.mouse.move(cancelBox.x+cancelBox.width/2+20,cancelBox.y+cancelBox.height/2);await page.keyboard.press('Escape');await page.mouse.up();assert.equal((await read()).id,cancelHead);
  let old=await note(n1);await edit(()=>drag(rect(n1),16,-14));let next=await note(n1);
  assert.equal(next.pitch,old.pitch+1);assert.ok(next.start_sample>old.start_sample);assert.equal(next.end_sample-next.start_sample,old.end_sample-old.start_sample);assert.equal(next.provenance.origin,'user');
  steps.push('note drag/time/pitch');
  old=next;await edit(()=>drag(page.locator(`.daw-piano-grid rect[data-action=resize][data-note-id="${n1}"]`),24));next=await note(n1);assert.ok(next.end_sample>old.end_sample);assert.equal(next.start_sample,old.start_sample);
  await rect(n1).click();await page.getByLabel('音符音高',{exact:true}).fill('65');await page.getByLabel('音符力度百分比',{exact:true}).fill('60');await edit(()=>page.getByRole('button',{name:'应用音符属性',exact:true}).click());assert.equal((await note(n1)).pitch,65);assert.equal((await note(n1)).velocity,.6);
  const velocity=page.locator(`.daw-velocity rect[data-note-id="${n1}"]`);await velocity.scrollIntoViewIfNeeded();const velocityBox=await velocity.boundingBox();
  await edit(async()=>{await page.mouse.move(velocityBox.x+2,velocityBox.y+2);await page.mouse.down();await page.mouse.move(velocityBox.x+2,velocityBox.y-12,{steps:3});await page.mouse.up();});assert.ok((await note(n1)).velocity>.6);
  await rect(n2).click();await rect(n3).click({modifiers:['Shift']});const p2=(await note(n2)).pitch,p3=(await note(n3)).pitch;
  await edit(()=>drag(rect(n2),0,-14));assert.equal((await note(n2)).pitch,p2+1);assert.equal((await note(n3)).pitch,p3+1);steps.push('resize/properties/multi-select');
  await page.getByRole('button',{name:'画笔',exact:true}).click();await page.locator('.daw-piano-scroll').evaluate(e=>{e.scrollTop=26+(127-72)*14-100;});
  const ids=(await read()).snapshot.notes.map(n=>n.id),grid=page.getByLabel('可编辑钢琴卷帘',{exact:true}),b=await grid.boundingBox();
  await edit(async()=>{const px=b.x+48+8/12*(b.width-58),py=b.y+26+(127-72)*14+7;await page.mouse.move(px,py);await page.mouse.down();await page.mouse.move(px+20,py,{steps:4});await page.mouse.up();});
  const drawn=(await read()).snapshot.notes.find(n=>!ids.includes(n.id));assert.ok(drawn);assert.equal(drawn.pitch,72);
  await page.getByRole('button',{name:'橡皮',exact:true}).click();await edit(()=>rect(drawn.id).click());assert.ok(!(await note(drawn.id)));
  await edit(()=>page.getByRole('button',{name:'撤销',exact:true}).click());assert.ok(await note(drawn.id));
  await edit(()=>page.getByRole('button',{name:'重做',exact:true}).click());assert.ok(!(await note(drawn.id)));steps.push('draw/erase/undo/redo');
  await page.getByRole('button',{name:'选择 / 拖动',exact:true}).click();await rect(n2).click();await editor.focus();await page.keyboard.press('Control+c');
  await page.getByRole('button',{name:'回到开头',exact:true}).click();await editor.focus();const count=(await read()).snapshot.notes.length;
  await edit(()=>page.keyboard.press('Control+v'));assert.equal((await read()).snapshot.notes.length,count+1);
  await editor.focus();await edit(()=>page.keyboard.press('Delete'));assert.equal((await read()).snapshot.notes.length,count);assert.ok(await note(n2));steps.push('copy/paste/delete');
  await page.screenshot({path:path.join(output,'piano-roll.png'),fullPage:true});
  // A note cannot be dragged outside the clip it belongs to. Rejection leaves the head intact.
  await rect(n1).click();const bounded=await note(n1),boundedHead=(await read()).id;await page.getByLabel('音符起点秒',{exact:true}).fill('0');await page.getByRole('button',{name:'应用音符属性',exact:true}).click();await page.getByRole('alert').filter({hasText:'owned note lies outside its clip'}).waitFor();assert.equal((await read()).id,boundedHead);await page.getByLabel('音符起点秒',{exact:true}).fill(String(bounded.start_sample/44100));await page.getByRole('button',{name:'关闭编辑提示',exact:true}).click();
  await page.getByRole('button',{name:'编排',exact:true}).click();await edit(()=>drag(clipRect(fixture.midi_clip),24));
  let current=await read(),midi=current.snapshot.clips.find(c=>c.id===fixture.midi_clip);assert.ok(midi.start_sample>44100*2);assert.ok((await note(n2)).start_sample>44100*3);
  assert.equal((await note(n2)).timing_mode,'sample');assert.equal((await note(n2)).notation_beat,null);
  const audioClip=current.snapshot.clips.find(c=>c.track_id===fixture.audio);await edit(()=>drag(clipRect(audioClip.id),16));current=await read();assert.ok(current.snapshot.clips.find(c=>c.id===audioClip.id).start_sample>audioClip.start_sample);
  const resize=page.locator(`g[data-clip-id="${audioClip.id}"] rect[data-action=resize]`);await edit(()=>drag(resize,-26));assert.ok((await read()).snapshot.clips.find(c=>c.id===audioClip.id).duration_samples<audioClip.duration_samples);steps.push('MIDI/audio clip move and audio trim');
  await page.getByLabel('编辑声部',{exact:true}).selectOption(fixture.harmony);await edit(()=>page.getByRole('button',{name:'区间音符编组',exact:true}).click());current=await read();const harmonyClip=current.snapshot.clips.find(c=>c.track_id===fixture.harmony);assert.ok(harmonyClip);assert.ok(current.snapshot.notes.filter(n=>n.track_id===fixture.harmony).every(n=>n.clip_id===harmonyClip.id));
  await clipRect(fixture.midi_clip).click();const oldClips=(await read()).snapshot.clips.map(c=>c.id);await edit(()=>page.getByRole('button',{name:'复制到后方',exact:true}).click());current=await read();const duplicate=current.snapshot.clips.find(c=>!oldClips.includes(c.id));assert.ok(duplicate);
  await clipRect(duplicate.id).click();const copiedNotes=current.snapshot.notes.filter(n=>n.clip_id===duplicate.id).sort((a,b)=>a.start_sample-b.start_sample);const split=(copiedNotes[1].end_sample+copiedNotes[2].start_sample)/2;
  const ruler=await page.locator('.daw-playlist-ruler svg').boundingBox();await page.mouse.click(ruler.x+48+split/(12*44100)*(ruler.width-58),ruler.y+10);
  await page.waitForFunction(p=>Math.abs(Number(document.querySelector('.studio')?.dataset.position)-p)<.05,split/44100);
  await edit(()=>page.getByRole('button',{name:'播放头分割',exact:true}).click());assert.equal((await read()).snapshot.clips.length,current.snapshot.clips.length+1);
  await clipRect(duplicate.id).click();await edit(()=>page.getByRole('button',{name:'删除片段',exact:true}).click());assert.ok(!(await read()).snapshot.clips.some(c=>c.id===duplicate.id));steps.push('MIDI grouping/duplicate/split/delete');
  await page.getByLabel('编辑声部',{exact:true}).selectOption(fixture.melody);await edit(()=>page.getByRole('button',{name:'轨道下移',exact:true}).click());assert.equal((await read()).snapshot.track_order[0],fixture.harmony);
  await edit(()=>page.getByRole('button',{name:'编辑轨道 主旋律',exact:true}).dragTo(page.getByRole('button',{name:'编辑轨道 和声',exact:true})));assert.equal((await read()).snapshot.track_order[0],fixture.melody);
  const originalTracks=(await read()).snapshot.tracks.map(t=>t.id);await edit(()=>page.getByRole('button',{name:'＋ 乐器轨道',exact:true}).click());current=await read();const added=current.snapshot.tracks.find(t=>!originalTracks.includes(t.id));assert.ok(added);
  await edit(()=>page.getByRole('button',{name:'删除轨道',exact:true}).click());assert.equal((await read()).snapshot.tracks.length,originalTracks.length);steps.push('track reorder/add/delete');
  await page.getByRole('button',{name:'混音台',exact:true}).click();await page.getByLabel('轨道名称 '+fixture.melody,{exact:true}).fill('手动主旋律');
  const fader=page.getByLabel('音量推子 主旋律',{exact:true});await fader.focus();await fader.press('End');
  await page.getByLabel('音量数值 主旋律',{exact:true}).fill('-6');await page.getByLabel('声像 主旋律',{exact:true}).focus();await page.getByLabel('声像 主旋律',{exact:true}).press('End');
  await page.getByRole('button',{name:'工程静音 主旋律',exact:true}).click();await page.getByRole('button',{name:'工程独奏 和声',exact:true}).click();
  await edit(()=>page.getByRole('button',{name:'保存混音',exact:true}).click());current=await read();const mixed=current.snapshot.tracks.find(t=>t.id===fixture.melody);
  assert.equal(mixed.name,'手动主旋律');assert.equal(mixed.gain_db,-6);assert.equal(mixed.pan,1);assert.equal(mixed.mute,true);assert.equal(current.snapshot.tracks.find(t=>t.id===fixture.harmony).solo,true);steps.push('track rename/fader/pan/mute/solo');
  await page.screenshot({path:path.join(output,'mixer.png'),fullPage:true});
  const retained=(await read()).snapshot;await page.getByRole('button',{name:'编排',exact:true}).click();await page.getByLabel('编辑声部',{exact:true}).selectOption(fixture.harmony);
  await edit(()=>page.getByRole('button',{name:'删除轨道',exact:true}).click());current=await read();assert.ok(!current.snapshot.plugins.some(p=>p.track_id===fixture.harmony));assert.ok(!current.snapshot.notes.some(n=>n.track_id===fixture.harmony));assert.ok(!current.snapshot.routing.nodes.includes(fixture.harmony));
  await edit(()=>page.getByRole('button',{name:'撤销',exact:true}).click());current=await read();assert.deepEqual(current.snapshot.notes,retained.notes);assert.deepEqual(current.snapshot.plugins,retained.plugins);steps.push('dependent track removal and complete-state undo');
  // A second client changes the head after the UI prepares a request. Original UI write must fail.
  await page.getByRole('button',{name:'钢琴卷帘',exact:true}).click();await page.getByLabel('编辑声部',{exact:true}).selectOption(fixture.melody);await rect(n1).click();
  old=await note(n1);await page.getByLabel('音符音高',{exact:true}).fill(String(old.pitch+2));let conflictInjected=false;
  await page.route('**/v1/commands',async route=>{const body=route.request().postDataJSON();if(!conflictInjected&&body.operation==='edits.apply'){
    conflictInjected=true;await api('edits.apply',{project_id:fixture.project_id,scope:{track_ids:[fixture.melody],start_sample:0,end_sample:12*44100},operations:[{op:'patch_note',id:n1,velocity:.55}],author:'second-client',description:'Isolated optimistic concurrency check'},body.expected_revision);
  }await route.continue();});
  await page.getByRole('button',{name:'应用音符属性',exact:true}).click();await page.getByRole('alert').filter({hasText:'Expected revision differs'}).waitFor();current=await read();assert.equal(current.snapshot.notes.find(n=>n.id===n1).pitch,old.pitch);assert.equal(current.snapshot.notes.find(n=>n.id===n1).velocity,.55);await page.unroute('**/v1/commands');steps.push('revision conflict refused');
  await page.waitForFunction(id=>document.querySelector('.daw-editor')?.dataset.editorRevision===id,current.id);
  await page.getByRole('button',{name:'关闭编辑提示',exact:true}).click();
  await page.getByRole('button',{name:'版本',exact:true}).click();await page.getByLabel('工程版本',{exact:true}).selectOption(fixture.seed_revision);await page.waitForFunction(()=>document.querySelector('.daw-edit-state')?.textContent.includes('历史版本'));
  assert.ok(await page.getByRole('button',{name:'应用音符属性',exact:true}).isDisabled());assert.equal((await read()).id,current.id);
  await page.getByLabel('工程版本',{exact:true}).selectOption('head');await page.waitForFunction(()=>document.querySelector('.daw-edit-state')?.textContent.includes('可编辑'));steps.push('cancel/clip-boundary/history protection');
  // A later head must also win when another client commits just AFTER our successful write.
  await rect(n1).click();await page.getByLabel('音符力度百分比',{exact:true}).fill('65');let postCommit=null;
  await page.route('**/v1/commands',async route=>{const body=route.request().postDataJSON();if(!postCommit&&body.operation==='edits.apply'){
    const response=await route.fetch(),own=await response.json();assert.ok(own.ok);
    postCommit=await api('edits.apply',{project_id:fixture.project_id,scope:{track_ids:[fixture.melody],start_sample:0,end_sample:12*44100},operations:[{op:'patch_note',id:n1,velocity:.7}],author:'second-client',description:'Isolated post-commit head advance'},own.result.id);
    await route.fulfill({response});return;
  }await route.continue();});
  await page.getByRole('button',{name:'应用音符属性',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.daw-edit-state')?.textContent.includes('可编辑'));
  for(let i=0;i<80&&!postCommit;i++)await new Promise(r=>setTimeout(r,50));assert.ok(postCommit);
  await page.waitForFunction(id=>document.querySelector('.daw-editor')?.dataset.editorRevision===id,postCommit.id);await page.unroute('**/v1/commands');assert.equal((await note(n1)).velocity,.7);steps.push('authoritative post-commit head update');
  await page.getByRole('tab',{name:'音色与效果',exact:true}).click();await page.getByRole('button',{name:'总览',exact:true}).click();
  const renderBefore=(await api('jobs.list',{project_id:fixture.project_id,limit:1000})).map(j=>j.id);
  await page.getByRole('button',{name:'生成重建试听',exact:true}).click();
  let rendered;for(let i=0;i<120;i++){const tasks=await api('jobs.list',{project_id:fixture.project_id,limit:1000});rendered=tasks.find(j=>j.kind==='project.render'&&!renderBefore.includes(j.id));if(rendered&&['succeeded','failed','cancelled'].includes(rendered.status))break;await new Promise(r=>setTimeout(r,250));}
  assert.equal(rendered?.status,'succeeded',JSON.stringify(rendered?.error));assert.ok(rendered.result.measurements.mix.rms>1e-6);
  const reconstruction=page.locator('.mode-switch button').filter({hasText:'重建'});await reconstruction.waitFor();await page.waitForFunction(()=>![...document.querySelectorAll('.mode-switch button')].find(b=>b.textContent.includes('重建')).disabled);
  await reconstruction.click();await page.getByRole('button',{name:'播放',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.clock strong')?.textContent!=='00:00');await page.getByRole('button',{name:'暂停',exact:true}).click();steps.push('real sfizz full fixture render and browser playback');
  await page.getByRole('button',{name:'混音台',exact:true}).click();await page.screenshot({path:path.join(output,'studio.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  current=await read();assert.equal(current.snapshot.source_asset_id,fixture.source_sha256);assert.deepEqual(errors,[]);assert.ok(!writes.some(w=>w.operation==='dialogue.submit'));assert.ok(writes.every(w=>w.idempotency&&w.expected_revision));
  await writeFile(path.join(output,'summary.json'),JSON.stringify({status:'succeeded',project_id:fixture.project_id,final_revision:current.id,render_job_id:rendered.id,source_unchanged:true,provider_calls:0,page_errors:errors,mobile_overflow:false,steps,writes},null,2));console.log(JSON.stringify({status:'succeeded',steps:steps.length,provider_calls:0,final_revision:current.id}));
}catch(e){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{});await writeFile(path.join(output,'failure.json'),JSON.stringify({status:'failed',steps,error:String(e),page_errors:errors},null,2));throw e;}finally{await browser.close();}
