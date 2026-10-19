import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const [tokenFile,output]=process.argv.slice(2),base='http://127.0.0.1:8765';
await mkdir(output,{recursive:false});const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1600,height:1000}});page.setDefaultTimeout(15000);
const errors=[],writes=[];page.on('pageerror',e=>errors.push(String(e)));page.on('request',r=>{if(r.url().endsWith('/v1/commands')){const op=r.postDataJSON().operation;if(['edits.apply','dialogue.submit','jobs.submit','revisions.restore','audition.set'].includes(op))writes.push(op);}});
const moveOut=()=>page.mouse.move(600,850);
const expanded=()=>page.locator('.toolbar-dock').getAttribute('data-expanded');
try{
  await page.route('**/v1/commands',async route=>{if(route.request().postDataJSON().operation==='dialogue.list')await route.fulfill({json:{schema_version:1,ok:true,result:[]}});else await route.continue();});
  await page.goto(base+'/#token='+(await readFile(tokenFile,'ascii')).trim());await page.getByRole('button',{name:'打开工程',exact:true}).click();await page.getByLabel('制作编辑器',{exact:true}).waitFor();
  assert.equal(await page.locator('.studio-top').count(),0);assert.equal(await page.getByLabel('返回音乐库',{exact:true}).count(),1);await moveOut();assert.equal(await expanded(),'false');assert.ok((await page.locator('.toolbar-dock').boundingBox()).height<40);
  await page.locator('.toolbar-peek').hover();assert.equal(await expanded(),'true');assert.equal(await page.getByRole('button',{name:'生成重建试听',exact:true}).count(),1);assert.ok(await page.getByRole('button',{name:'播放',exact:true}).isVisible()||await page.getByRole('button',{name:'暂停',exact:true}).isVisible());
  await moveOut();await page.waitForFunction(()=>document.querySelector('.toolbar-dock').dataset.expanded==='false');
  await page.getByLabel('展开或收起工具栏',{exact:true}).focus();await page.keyboard.press('Tab');await page.waitForTimeout(550);assert.equal(await expanded(),'true');
  await page.keyboard.press('Escape');assert.equal(await expanded(),'false');
  await page.locator('.toolbar-peek').hover();await page.getByLabel('锁定顶部工具栏',{exact:true}).focus();await page.keyboard.press('Space');await moveOut();await page.waitForTimeout(550);assert.equal(await expanded(),'true');
  await page.waitForTimeout(200);await page.reload();await page.getByLabel('制作编辑器',{exact:true}).waitFor();assert.equal(await page.getByLabel('锁定顶部工具栏',{exact:true}).getAttribute('aria-pressed'),'true');
  await page.getByLabel('锁定顶部工具栏',{exact:true}).click();await moveOut();await page.waitForFunction(()=>document.querySelector('.toolbar-dock').dataset.expanded==='false');
  await page.getByRole('tab',{name:'讨论',exact:true}).focus();await page.keyboard.press('ArrowRight');assert.equal(await page.getByRole('tab',{name:'音色与效果',exact:true}).getAttribute('aria-selected'),'true');await page.keyboard.press('Home');assert.equal(await page.getByRole('tab',{name:'讨论',exact:true}).getAttribute('aria-selected'),'true');
  await page.getByRole('button',{name:'分析整曲结构',exact:true}).click();assert.match(await page.getByLabel('讨论问题',{exact:true}).inputValue(),/查询整曲摘要/);
  await page.getByRole('button',{name:'聊选中片段',exact:true}).click();assert.equal(await page.getByLabel('讨论范围',{exact:true}).inputValue(),'selection');
  await page.getByRole('button',{name:'试一个改编',exact:true}).click();assert.equal(await page.getByLabel('允许改编模式',{exact:true}).getAttribute('aria-pressed'),'true');
  await page.getByLabel('讨论问题',{exact:true}).fill('测试输入时工具栏保持收起');assert.equal(await expanded(),'false');
  const settings=page.locator('.discussion-settings');assert.equal(await settings.getAttribute('open'),null);await settings.locator('summary').click();await page.getByLabel('Codex 登录方式',{exact:true}).selectOption('existing');await settings.locator('summary').click();
  assert.equal(await page.locator('.track-card').count(),6);assert.ok((await page.locator('.track-card').first().boundingBox()).height<100);assert.equal(await page.locator('.source-stems').getAttribute('open'),null);
  const split=page.getByRole('separator',{name:'调整 AI 侧栏宽度',exact:true});await split.scrollIntoViewIfNeeded();const b=await split.boundingBox(),w=(await page.locator('.inspector').boundingBox()).width;await page.mouse.move(b.x+3,b.y+30);await page.mouse.down();await page.mouse.move(b.x-150,b.y+30,{steps:8});await page.mouse.up();assert.ok((await page.locator('.inspector').boundingBox()).width>w+130);
  await page.getByLabel('讨论问题',{exact:true}).fill('');await page.getByLabel('仅讨论模式',{exact:true}).click();
  await page.screenshot({path:path.join(output,'collapsed.png'),fullPage:true});await page.locator('.toolbar-peek').hover();await page.screenshot({path:path.join(output,'expanded.png'),fullPage:true});
  for(const width of [1000,390]){await page.setViewportSize({width,height:900});await moveOut();await page.waitForTimeout(550);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));}
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);const report={status:'succeeded',auto_hide:true,pin_persisted:true,hover_reveal:true,keyboard_focus_retained:true,escape:true,compact_track_rows:true,source_stems_collapsed:true,suggestions_only_fill_draft:true,connection_settings_available:true,sidebar_resize:true,responsive:true,page_errors:errors,production_or_transport_commands:writes};await writeFile(path.join(output,'summary.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(e){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});throw e;}finally{await browser.close();}
