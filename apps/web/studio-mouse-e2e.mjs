// Destructive gestures are restricted to a caller-created independent fixture.
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const fixture=path.resolve(process.argv[2]??''),output=path.resolve(process.argv[3]??'');
if(!fixture.includes(path.join('data','t'))||!process.argv[3])throw Error('Independent fixture and evidence paths required');
const config=JSON.parse(await readFile(path.join(fixture,'app.local.json'),'utf8'));
assert(config.port!==8765&&path.resolve(config.data_root).startsWith(fixture));
const info=JSON.parse(await readFile(path.join(fixture,'fixture.json'),'utf8'));
const token=(await readFile(path.join(config.data_root,'session-token.local'),'ascii')).trim();
await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const page=await browser.newPage({viewport:{width:1520,height:980},deviceScaleFactor:2.5}),errors=[],commands=[],checks=[];
page.on('pageerror',e=>errors.push(String(e)));
page.on('request',r=>{if(r.url().endsWith('/v1/commands')){try{commands.push(r.postDataJSON());}catch{}}});
const check=(name,value)=>{assert(value,name);checks.push(name);};
async function api(op,args={}){return page.evaluate(async({op,args})=>{const r=await fetch('/v1/commands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:op,arguments:args})});const d=await r.json();if(!d.ok)throw Error(JSON.stringify(d.error));return d.result;},{op,args});}
const revision=()=>api('revisions.get',{project_id:info.project_id});
async function until(test){const deadline=Date.now()+15000;while(Date.now()<deadline){if(await test())return;await new Promise(r=>setTimeout(r,80));}throw Error('Timed out: '+test.toString());}
async function noteCount(n){await until(async()=>((await revision()).snapshot.notes??[]).length===n);await until(async()=>(await page.locator('.daw-editor').getAttribute('data-editor-revision'))===(await revision()).id);}
async function center(selector){const r=await selector.boundingBox();assert(r);return{x:r.x+r.width/2,y:r.y+r.height/2};}
const edits=()=>commands.filter(c=>c.operation==='edits.apply').length;
try{
  await page.addInitScript(()=>{window.__desktopActions=[];window.mlDesktop={post:a=>window.__desktopActions.push(a)};window.__contextPrevented=[];window.addEventListener('contextmenu',e=>window.__contextPrevented.push(e.defaultPrevented));});
  await page.goto(`http://127.0.0.1:${config.port}/?project=${info.project_id}&view=workbench#token=${token}`);
  await page.locator('.daw-editor').waitFor();
  const baseline=await revision(),baseNotes=baseline.snapshot.notes.length;
  check('workspace branding hidden',await page.locator('main > header').isHidden());
  check('loop and chord in top transport',await page.locator('.studio-toolbar .transport-chord').count()===1&&await page.locator('.studio-toolbar .transport-popover').count()===1);
  check('removed track command row',await page.locator('.daw-playlist-tools').count()===0);
  check('main workspace has no page scrolling',await page.locator('.arrangement').evaluate(n=>n.scrollHeight<=n.clientHeight+1));
  const ruler=page.locator('.daw-playlist-ruler'),rulerY=(await ruler.boundingBox()).y;
  const scroll=page.locator('.daw-playlist-scroll');await scroll.evaluate(n=>n.scrollTop=700);
  check('fixed ruler while lanes scroll',Math.abs((await ruler.boundingBox()).y-rulerY)<1);
  check('no artificial horizontal overflow',await scroll.evaluate(n=>n.scrollWidth<=n.clientWidth+1));
  await scroll.evaluate(n=>n.scrollTop=0);
  await page.getByRole('button',{name:'放大时间轴',exact:true}).click();
  let p=await center(scroll);await page.mouse.move(p.x,p.y);await page.mouse.down({button:'middle'});await page.mouse.move(p.x-80,p.y-90,{steps:6});await page.mouse.up({button:'middle'});
  check('middle-drag playlist in both axes',await scroll.evaluate(n=>n.scrollTop>60)&&Number(await page.locator('.timeline-pan').inputValue())>0);
  await page.getByRole('button',{name:'整曲',exact:true}).click();
  await page.locator('.track-select').first().dblclick();await page.locator('.daw-piano-grid').waitFor();await page.getByRole('button',{name:'适配音域',exact:true}).click();
  let before=edits();p=await center(page.locator('.daw-piano-grid g[data-note-id] > rect').first());
  await page.mouse.click(p.x,p.y,{button:'right'});await noteCount(baseNotes-1);
  check('right-click erases note with one edit',edits()===before+1);
  await page.getByRole('button',{name:'撤销',exact:true}).click();await noteCount(baseNotes);
  let notes=page.locator('.daw-piano-grid g[data-note-id] > rect:first-child');const a=await center(notes.nth(0)),b=await center(notes.nth(1));before=edits();
  await page.mouse.move(a.x,a.y);await page.mouse.down({button:'right'});await page.mouse.move(b.x,b.y,{steps:2});await page.mouse.up({button:'right'});await noteCount(baseNotes-2);
  check('right sweep is one undoable transaction',edits()===before+1);
  await page.getByRole('button',{name:'撤销',exact:true}).click();await noteCount(baseNotes);
  p=await center(page.locator('.daw-piano-grid g[data-note-id] > rect').first());before=edits();
  await page.mouse.move(p.x,p.y);await page.mouse.down({button:'right'});await page.keyboard.press('Escape');await page.mouse.up({button:'right'});
  check('Escape cancels erase without writing',edits()===before&&(await revision()).snapshot.notes.length===baseNotes);
  await page.getByRole('button',{name:'放大时间轴',exact:true}).click();
  const piano=page.locator('.daw-piano-scroll'),oldScroll=await piano.evaluate(n=>n.scrollTop);p=await center(piano);before=edits();
  await page.mouse.move(p.x,p.y);await page.mouse.down({button:'middle'});await page.mouse.move(p.x-55,p.y-65,{steps:5});await page.mouse.up({button:'middle'});
  check('middle-drag piano does not edit music',edits()===before&&(await piano.evaluate(n=>n.scrollTop))>oldScroll&&Number(await page.locator('.timeline-pan').inputValue())>0);
  p=await center(piano);await page.mouse.move(p.x,p.y);await page.mouse.down({button:'middle'});await page.mouse.down({button:'right'});
  await page.getByRole('menu').waitFor();await page.mouse.up({button:'right'});await page.mouse.up({button:'middle'});
  await page.getByRole('menuitem',{name:'选择',exact:true}).click();check('middle plus right opens editor tools',edits()===before);
  await page.keyboard.down('Control');await page.mouse.click(p.x,p.y,{button:'right'});await page.keyboard.up('Control');check('Ctrl right fits entire song',Number(await page.locator('.timeline-pan').inputValue())===0);
  const initialOrder=(await revision()).snapshot.track_order;
  await page.locator('.track-card').first().click({button:'right',position:{x:5,y:5}});await page.getByRole('menuitem',{name:'下移轨道',exact:true}).click();
  await until(async()=>(await revision()).snapshot.track_order[0]===initialOrder[1]);check('track menu moves actual track order',true);
  await page.getByRole('button',{name:'添加乐器轨道',exact:true}).click();await until(async()=>(await revision()).snapshot.tracks.length===baseline.snapshot.tracks.length+1);
  await page.locator('.track-panel').evaluate(n=>n.scrollTop=n.scrollHeight);const added=page.locator('.track-card').last();await added.getByRole('button',{name:/轨道菜单/}).click();await page.getByRole('menuitem',{name:'删除轨道',exact:true}).click();
  await until(async()=>(await revision()).snapshot.tracks.length===baseline.snapshot.tracks.length);check('add and delete stay with tracks',true);
  await page.getByRole('button',{name:'编排',exact:true}).click();await scroll.evaluate(n=>n.scrollTop=0);
  const clip=page.locator(`[data-clip-id="${info.midi_clip}"] > rect`).first();before=edits();p=await center(clip);await page.mouse.click(p.x,p.y,{button:'right'});
  await until(async()=>!(await revision()).snapshot.clips.some(c=>c.id===info.midi_clip));check('right-click MIDI clip removes its owned notes atomically',edits()===before+1);
  await page.getByRole('button',{name:'撤销',exact:true}).click();await noteCount(baseNotes);
  await page.locator('.track-panel').evaluate(n=>n.scrollTop=0);
  await page.getByRole('button',{name:/^重建/}).click();await page.getByRole('button',{name:'播放',exact:true}).click();await until(async()=>(await page.locator('.studio').getAttribute('data-playing'))==='true');
  const playingHead=(await revision()).id;await page.getByRole('spinbutton',{name:'音轨音量数值 1',exact:true}).fill('-8');await page.getByRole('button',{name:'试听静音 1',exact:true}).click();await page.getByRole('button',{name:'试听静音 1',exact:true}).click();await page.getByRole('button',{name:'独奏 2',exact:true}).click();await page.getByRole('button',{name:'独奏 2',exact:true}).click();
  check('playing gain mute solo keep head',playingHead===(await revision()).id&&await page.locator('.studio').getAttribute('data-playing')==='true');
  await page.getByRole('button',{name:'暂停',exact:true}).click();
  await page.getByRole('button',{name:'总览',exact:true}).click();
  await until(()=>page.locator('canvas.piano-roll').evaluate(n=>Math.abs(n.width-n.getBoundingClientRect().width*devicePixelRatio)<3));
  check('canvas raster matches 250 percent display scale',await page.locator('canvas.piano-roll').evaluate(n=>Math.abs(n.width-n.getBoundingClientRect().width*devicePixelRatio)<3));
  const cdp=await page.context().newCDPSession(page);
  for(const scale of [1,1.5,2,2.5]){
    await cdp.send('Emulation.setDeviceMetricsOverride',{width:1520,height:980,deviceScaleFactor:scale,mobile:false});
    // CDP changes DPR silently; send the same resize notification as the native DPI handler.
    await page.evaluate(()=>window.dispatchEvent(new Event('resize')));
    await until(()=>page.locator('canvas.piano-roll').evaluate(n=>Math.abs(n.width-n.getBoundingClientRect().width*devicePixelRatio)<3));
  }
  check('canvas re-rasterizes on monitor scale change without CSS resize',true);
  await cdp.send('Emulation.clearDeviceMetricsOverride');await cdp.detach();
  await page.getByRole('button',{name:'编排',exact:true}).click();
  await page.setViewportSize({width:1100,height:740});
  check('compact viewport has no document overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight));
  check('window controls fit compact transport',await page.locator('.studio-toolbar .window-controls').evaluate(n=>n.getBoundingClientRect().right<=innerWidth&&n.getBoundingClientRect().top<48));
  for(const name of ['最小化窗口','最大化窗口','关闭窗口'])await page.getByRole('button',{name,exact:true}).click();
  check('window buttons send native bridge actions',await page.evaluate(()=>JSON.stringify(window.__desktopActions.filter(a=>a!=='drag'))===JSON.stringify(['minimize','maximize','close'])));
  await page.screenshot({path:path.join(output,'workbench-1100.png')});
  await page.setViewportSize({width:1520,height:980});await page.screenshot({path:path.join(output,'workbench-1520.png')});
  check('native context menus suppressed on music surfaces',await page.evaluate(()=>window.__contextPrevented.length>0&&window.__contextPrevented.every(Boolean)));
  check('no browser popup tabs',page.context().pages().length===1);
  check('no page errors',errors.length===0);
  const prohibited=commands.filter(c=>['versions.save','mixer.set','mixing.save','dialogue.submit'].includes(c.operation)||c.operation==='jobs.submit'&&c.arguments.kind==='project.render');
  check('no generated renders models or unsolicited saves',prohibited.length===0);
  await writeFile(path.join(output,'report.json'),JSON.stringify({checks,errors,edits:edits(),commands:commands.map(c=>({operation:c.operation,kind:c.arguments?.kind})),final_revision:(await revision()).id},null,2));
  console.log(JSON.stringify({passed:checks.length,errors}));
}catch(error){await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});await writeFile(path.join(output,'failure.json'),JSON.stringify({checks,errors,error:String(error)},null,2));throw error;}
finally{await browser.close();}
