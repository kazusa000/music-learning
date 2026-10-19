import {sandboxAudition} from './audition-sandbox.mjs';
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import path from 'node:path';
const [tokenFile,output]=process.argv.slice(2),base='http://127.0.0.1:8765';
await mkdir(output,{recursive:false});
const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[],writes=[];
page.setDefaultTimeout(15000);
page.on('pageerror',e=>errors.push(String(e)));
page.on('request',r=>{if(r.url().endsWith('/v1/commands')){const op=r.postDataJSON().operation;if(['edits.apply','jobs.submit','dialogue.submit','revisions.restore'].includes(op))writes.push(op);}});
async function drag(label,dx,dy){const handle=page.getByRole('separator',{name:label,exact:true});await handle.scrollIntoViewIfNeeded();const b=await handle.boundingBox();await page.mouse.move(b.x+b.width/2,b.y+b.height/2);await page.mouse.down();await page.mouse.move(b.x+b.width/2+dx,b.y+b.height/2+dy,{steps:8});await page.mouse.up();}
const height=async selector=>(await page.locator(selector).boundingBox()).height;
try{
  await sandboxAudition(page,base,(await readFile(tokenFile,'ascii')).trim());await page.goto(base+'/#token='+(await readFile(tokenFile,'ascii')).trim());await page.getByRole('button',{name:'打开工程',exact:true}).waitFor();
  await drag('调整歌曲列表宽度',70,0);assert.ok((await page.locator('.layout>aside').boundingBox()).width>=320);
  await page.getByRole('button',{name:'打开工程',exact:true}).click();await page.getByLabel('制作编辑器',{exact:true}).waitFor();
  const ai=(await page.locator('.inspector').boundingBox()).width;await drag('调整 AI 侧栏宽度',-170,0);assert.ok((await page.locator('.inspector').boundingBox()).width>ai+150);
  await drag('调整声部栏宽度',60,0);assert.ok((await page.locator('.track-panel').boundingBox()).width>240);
  const inputHeight=await height('.discussion-compose textarea'),replyHeight=await height('.discussion-history');await drag('调整输入框高度',0,-60);assert.ok(await height('.discussion-compose textarea')>=inputHeight+50);assert.ok(await height('.discussion-history')<replyHeight-45);
  const content=await height('.daw-view-content');await drag('调整制作区域高度',0,70);assert.ok(await height('.daw-view-content')>content+60);
  await page.getByRole('button',{name:'钢琴卷帘',exact:true}).click();await page.locator('.daw-piano-grid [data-note-id]').first().waitFor();
  const before=await height('.daw-piano-scroll');await drag('调整卷帘高度',0,100);assert.ok(await height('.daw-piano-scroll')>before+85);
  await drag('调整力度栏高度',0,45);assert.ok(await height('.daw-velocity')>=165);
  const scroll=page.locator('.daw-piano-scroll');await scroll.scrollIntoViewIfNeeded();const b=await scroll.boundingBox();
  await page.mouse.move(b.x+b.width*.5,b.y+b.height*.5);const old=await scroll.evaluate(n=>n.scrollTop);await page.mouse.wheel(0,110);await page.waitForFunction(v=>document.querySelector('.daw-piano-scroll').scrollTop>v,old);
  const row=+(await page.getByLabel('音高缩放',{exact:true}).inputValue()),at=await scroll.evaluate(n=>n.scrollTop+n.clientHeight/2);
  await page.keyboard.down('Alt');await page.mouse.wheel(0,-70);await page.keyboard.up('Alt');await page.waitForFunction(v=>+document.querySelector('input[aria-label="音高缩放"]').value>v,row);
  const newRow=+(await page.getByLabel('音高缩放',{exact:true}).inputValue()),after=await scroll.evaluate(n=>n.scrollTop+n.clientHeight/2);
  assert.ok(Math.abs((at-26)/row-(after-26)/newRow)<2,'pitch anchor retained');
  await page.getByRole('button',{name:'适配音域',exact:true}).click();await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const navigator=page.getByLabel('卷帘音高位置',{exact:true});await navigator.focus();await page.keyboard.press('End');await page.waitForFunction(()=>{const n=document.querySelector('.daw-piano-scroll');return n.scrollTop>n.scrollHeight-n.clientHeight-50;},{timeout:10000});
  await navigator.focus();await page.keyboard.press('Home');await page.waitForFunction(()=>document.querySelector('.daw-piano-scroll').scrollTop<1);
  const slider=await navigator.boundingBox();await page.mouse.click(slider.x+slider.width/2,slider.y+slider.height*.8);await page.waitForFunction(()=>document.querySelector('.daw-piano-scroll').scrollTop>100);
  const savedAI=(await page.locator('.inspector').boundingBox()).width,savedPiano=await height('.daw-piano-scroll');await page.waitForTimeout(200);
  await page.reload();await page.getByLabel('制作编辑器',{exact:true}).waitFor();assert.ok(Math.abs((await page.locator('.inspector').boundingBox()).width-savedAI)<2);
  await page.getByRole('button',{name:'钢琴卷帘',exact:true}).click();assert.ok(Math.abs(await height('.daw-piano-scroll')-savedPiano)<2);
  const handle=page.getByRole('separator',{name:'调整 AI 侧栏宽度',exact:true});await handle.focus();await page.keyboard.press('ArrowLeft');assert.ok((await page.locator('.inspector').boundingBox()).width>savedAI+8);await handle.dblclick();assert.ok(Math.abs((await page.locator('.inspector').boundingBox()).width-340)<2);
  await page.getByRole('separator',{name:'调整卷帘高度',exact:true}).dblclick();await page.getByRole('separator',{name:'调整力度栏高度',exact:true}).dblclick();
  await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
  for(const width of [1000,390]){await page.setViewportSize({width,height:900});await page.waitForTimeout(200);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));}
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
  const report={status:'succeeded',library_width:true,track_width:true,ai_width:true,input_height:true,piano_height:true,content_height:true,velocity_height:true,pitch_scroll:true,alt_wheel_pitch_zoom:true,pitch_anchor:true,pitch_navigator:true,persist_reload:true,keyboard_resize:true,double_click_reset:true,responsive:true,page_errors:errors,musical_writes:writes};await writeFile(path.join(output,'summary.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(e){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});throw e;}finally{await browser.close();}
