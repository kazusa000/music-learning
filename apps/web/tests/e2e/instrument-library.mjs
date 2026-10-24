import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {chromium}=createRequire(path.join(process.env.MUSIC_LEARNING_TEST_RESOURCE_ROOT,'apps/web/package.json'))('playwright');
const f=JSON.parse(await readFile(path.join(process.argv[2],'fixture.json'),'utf8')),base='http://127.0.0.1:'+f.port;
const browser=await chromium.launch({channel:'msedge',headless:true});
const context=await browser.newContext({viewport:{width:1536,height:864},deviceScaleFactor:2.5}),page=await context.newPage();
const report={checks:[],errors:[],external:[],native:'mock telemetry; real native validation is separate'};
const commands=[],personal=[];let opened=0,session=null;
page.on('pageerror',e=>report.errors.push(e.message));
page.setDefaultTimeout(9000);
await page.route('**/*',async route=>{
 const request=route.request(),url=request.url();
 if(!url.startsWith(base)){report.external.push(url);return route.abort();}
 if(url!==base+'/v1/commands')return route.continue();
 const body=request.postDataJSON(),op=body.operation,args=body.arguments??{};commands.push({op,args});
 let result;
 if(op==='instruments.gallery')result=f.gallery;
 else if(op==='instruments.personal.list')result={items:personal};
 else if(op==='instruments.studio.open'){opened++;result=session={session_id:String(opened).padStart(32,'a'),status:'ready',editor_available:true,draft_saved_at:Date.now()/1000};}
 else if(op==='instruments.studio.status')result=session;
 else if(op==='instruments.studio.save'){
  if(args.preset_id){const old=personal.find(x=>x.id===args.preset_id);assert.equal(args.expected_version,old.version);old.version++;result={...old};}
  else {result={id:'user-'+String(personal.length).padStart(32,'b'),name:args.name,version:1};personal.push(result);}
 }else if(op.startsWith('instruments.studio.'))result={ok:true,items:[]};
 else return route.continue();
 return route.fulfill({json:{ok:true,result}});
});
try{
 await page.goto(base+'/#token='+f.token);await page.getByRole('button',{name:/^乐器库/}).click();
 await page.locator('.instrument-card').last().waitFor();assert.equal(await page.locator('.instrument-card').count(),Math.min(24,f.gallery.items.length));
 assert(f.gallery.items.every(x=>/[\u3400-\u9fff]/u.test(x.title)&&/[\u3400-\u9fff]/u.test(x.eyebrow)&&x.variants.every(p=>/[\u3400-\u9fff]/u.test(p.name))));
 assert.deepEqual(await page.locator('.instrument-card h3').allTextContents(),f.gallery.items.slice(0,24).map(x=>x.title));
 assert.deepEqual(await page.locator('.instrument-card-caption>p').allTextContents(),f.gallery.items.slice(0,24).map(x=>x.english_name));
 assert(!/INSTRUMENT COLLECTION|YOUR SOUND, YOUR WAY/.test(await page.locator('.instrument-library').innerText()));
 await page.locator('.instrument-collection').evaluate(e=>{e.scrollTop=e.scrollHeight;});
 await page.waitForFunction(()=>[...document.querySelectorAll('.instrument-card img')].every(i=>i.complete&&i.naturalWidth>0));
 await page.locator('.instrument-collection').evaluate(e=>{e.scrollTop=0;});
 await page.screenshot({path:path.join(f.report,'gallery-4k.png')});
 if(f.gallery.items.length>24){
  await page.getByRole('button',{name:'下一页',exact:true}).click();
  assert.deepEqual(await page.locator('.instrument-card h3').allTextContents(),f.gallery.items.slice(24,48).map(x=>x.title));
  await page.getByRole('button',{name:'上一页',exact:true}).click();
 }
 const source=f.gallery.items[0].source.display_name;
 await page.getByLabel('筛选音源来源',{exact:true}).selectOption(source);
 assert.deepEqual(await page.locator('.instrument-card h3').allTextContents(),f.gallery.items.filter(x=>x.source.display_name===source).slice(0,24).map(x=>x.title));
 await page.getByLabel('筛选音源来源',{exact:true}).selectOption('全部来源');
 const family=f.gallery.items.find(x=>x.category==='键盘');
 if(family){
  await page.getByRole('navigation',{name:'乐器分类'}).getByRole('button',{name:/^键盘/}).click();
  await page.getByLabel('细分乐器',{exact:true}).getByRole('button',{name:family.eyebrow,exact:true}).click();
  assert.deepEqual(await page.locator('.instrument-card h3').allTextContents(),f.gallery.items.filter(x=>x.category==='键盘'&&x.eyebrow===family.eyebrow).slice(0,24).map(x=>x.title));
  await page.getByRole('navigation',{name:'乐器分类'}).getByRole('button',{name:/^全部/}).click();
 }
 await page.getByLabel('搜索乐器',{exact:true}).fill('罗兹');assert(await page.locator('.instrument-card').count()>0);
 await page.getByLabel('搜索乐器',{exact:true}).fill('Rhodes');assert(await page.locator('.instrument-card').count()>0);
 await page.getByRole('button',{name:/^收藏 /}).first().click();await page.getByLabel('搜索乐器',{exact:true}).fill('');
 await page.getByRole('button',{name:/我的收藏/}).click();assert.equal(await page.locator('.instrument-card').count(),1);
 await page.reload();await page.getByRole('button',{name:/我的收藏/}).click();assert.equal(await page.locator('.instrument-card').count(),1);
 await page.getByRole('button',{name:/我的收藏/}).click();await page.getByLabel('搜索乐器').fill('xyz no instrument');
 await page.getByRole('heading',{name:'没有找到匹配的乐器'}).waitFor();await page.getByRole('button',{name:'查看全部乐器'}).click();
 report.checks.push('Homepage-only entry, paged local images decoded offline, model search/favorites/reload/empty state');
 report.checks.push('Chinese catalogue model/variant names and category labels, English originals secondary; search in either language');
 await page.getByRole('button',{name:'打开乐器 '+f.gallery.items[0].title,exact:true}).click();
 await page.getByText('可以试弹',{exact:true}).waitFor();await page.screenshot({path:path.join(f.report,'instrument-4k.png')});
 await page.getByLabel('试弹音符 60',{exact:true}).dispatchEvent('pointerdown',{pointerId:1});
 await page.getByLabel('试弹音符 60',{exact:true}).dispatchEvent('pointerup',{pointerId:1});
 await page.keyboard.press('a');await page.keyboard.press('Space');
 await page.getByRole('button',{name:'保存预设',exact:true}).click();
 await page.getByLabel('预设名称').fill('C7 · 温暖');await page.locator('.instrument-save-dialog').getByRole('button',{name:'保存预设',exact:true}).click();
 await page.getByText('已保存「C7 · 温暖」',{exact:true}).waitFor();assert.equal(opened,1);
 await page.getByRole('button',{name:'保存预设',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.instrument-save-dialog')===null);
 await page.waitForTimeout(180);assert.equal(personal.length,1);assert.equal(personal[0].version,2);assert.equal(opened,1);
 assert(commands.some(x=>x.op==='instruments.studio.note'&&x.args.on));assert(commands.some(x=>x.op==='instruments.studio.note'&&!x.args.on));
 report.checks.push('Keyboard MIDI/sustain actions, personal save/update creates one preset without restarting sound');
 await page.getByRole('button',{name:'插件',exact:true}).click();await page.getByText('原生插件界面',{exact:true}).waitFor();
 await page.screenshot({path:path.join(f.report,'plugin-browser-fallback.png')});
 await page.getByRole('button',{name:'乐器',exact:true}).click();
 for(const size of [{width:1120,height:720},{width:760,height:720}]){
  await page.setViewportSize(size);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  const box=await page.getByLabel('试弹钢琴').boundingBox();assert(box.y+box.height<=size.height+1);
  await page.screenshot({path:path.join(f.report,'instrument-'+size.width+'.png')});
 }
 await page.getByRole('button',{name:'乐器库',exact:true}).click();
 await page.screenshot({path:path.join(f.report,'gallery-narrow.png')});
 await page.getByLabel('关闭乐器库').click();await page.getByRole('button',{name:/^乐器库/}).waitFor();
 assert(commands.some(x=>x.op==='instruments.studio.close'));assert.deepEqual(report.errors,[]);assert.deepEqual(report.external,[]);
 await context.clearCookies();await page.route(base+'/v1/session',async route=>{await new Promise(r=>setTimeout(r,350));return route.continue();});
 await page.goto(base+'/?view=instruments#token='+f.token);await page.locator('.instrument-card').first().waitFor();await page.waitForFunction(()=>{const i=document.querySelector('.instrument-card img');return i?.complete&&i.naturalWidth>0;});report.checks.push('Fresh desktop/session entry waits for authentication before local image/gallery requests');
 report.checks.push('4K 2.5x and narrow layouts; fixed keyboard; browser fallback identified; close releases audition');report.status='succeeded';
}catch(e){report.status='failed';report.failure=String(e.stack??e);await page.screenshot({path:path.join(f.report,'failure.png')}).catch(()=>{});throw e;}
finally{await writeFile(path.join(f.report,'report.json'),JSON.stringify(report,null,2));await browser.close();}
