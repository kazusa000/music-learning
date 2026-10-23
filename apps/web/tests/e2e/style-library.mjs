import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const dependencyAnchor=process.env.MUSIC_LEARNING_TEST_RESOURCE_ROOT?path.join(process.env.MUSIC_LEARNING_TEST_RESOURCE_ROOT,'apps/web/package.json'):import.meta.url;
const {chromium}=createRequire(dependencyAnchor)('playwright');
const root=path.resolve(process.argv[2]),f=JSON.parse(await readFile(path.join(root,'fixture.json'),'utf8')),base='http://127.0.0.1:'+f.port;
const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1536,height:864},deviceScaleFactor:2.5}),page=await context.newPage();page.setDefaultTimeout(10000);
const report={checks:[],errors:[],externalRequests:[]};
page.on('pageerror',e=>report.errors.push(e.message));
await page.route('**/*',route=>{if(!route.request().url().startsWith(base)){report.externalRequests.push(route.request().url());return route.abort();}return route.continue();});
const until=async(fn,message)=>{const deadline=Date.now()+10000;while(Date.now()<deadline){if(await fn())return;await new Promise(r=>setTimeout(r,60));}throw Error(message);};
async function call(operation,args={}){return (await (await page.request.post(base+'/v1/commands',{data:{operation,arguments:args}})).json()).result;}
try{
 await page.goto(base+'/#token='+f.token);await page.getByRole('button',{name:'风格库',exact:false}).click();
 await page.getByRole('heading',{name:/风格库 STYLE ATLAS/}).waitFor();await page.locator('.style-card').first().waitFor();await page.locator('.style-dossier h2').waitFor();
 assert.equal((await call('styles.index')).total,1023);assert.equal(await page.locator('.style-dossier h2').innerText(),'城市流行');
 const city=await call('styles.get',{style_id:'asi03'});
 assert.equal(await page.locator('.style-intro').innerText(),city.intro);
 assert(city.intro.length>=45);
 assert(await page.locator('.style-production-note').count()>=2);
 assert(await page.locator('.style-note-evidence').count()>=2);
 assert(!(await page.locator('.style-dossier').innerText()).includes('给主唱留出中频与乐句空隙'));
 await page.screenshot({path:path.join(f.report,'library-4k.png')});
 await page.getByLabel('搜索音乐风格',{exact:true}).fill('City Pop');await until(()=>page.locator('.style-card').first().getAttribute('data-style').then(id=>id==='asi03'),'Exact English style match not ranked first');
 assert.equal(await page.locator('.style-card[data-style="asi03"]').count(),1);
 await page.getByLabel('搜索音乐风格',{exact:true}).fill('DX7');await page.locator('.style-card[data-style="asi03"]').waitFor();
 await page.getByLabel('搜索音乐风格',{exact:true}).fill('City Pop');
 await page.getByRole('button',{name:'收藏风格',exact:true}).click();await page.getByRole('button',{name:/我的收藏/}).click();assert.equal(await page.locator('.style-card').count(),1);
 await page.reload();await page.getByRole('button',{name:'取消收藏风格',exact:true}).waitFor();await page.getByRole('button',{name:/我的收藏/}).click();assert.equal(await page.locator('.style-card').count(),1);
 await page.getByRole('button',{name:/全部风格/}).click();await page.getByLabel('搜索音乐风格',{exact:true}).fill('绝不可能的风格xyz');await page.getByRole('heading',{name:'没有找到匹配的风格'}).waitFor();await page.getByRole('button',{name:'查看全部风格',exact:true}).click();
 await page.getByLabel('风格年代',{exact:true}).selectOption('1970');await page.getByLabel('风格年代',{exact:true}).selectOption('all');
 report.checks.push('Offline full catalog, Chinese/English search, empty state, favorites and reload persistence');
 // Compare a percussion-led tradition with an electronic style, so the page
 // must render the selected dossier rather than a generic shared paragraph.
 for(const id of ['woB02','ind01']){
  await page.goto(base+'/?view=styles&style='+id+'#token='+f.token);
  const item=await call('styles.get',{style_id:id});
  await until(()=>page.locator('.style-intro').innerText().then(t=>t===item.intro),'Specific style introduction not displayed');
  assert(await page.locator('.style-production-note').count()>=2);
  assert.notEqual(item.intro,city.intro);
  for(const notes of Object.values(item.sections))for(const note of notes)assert((await page.locator('.style-dossier').innerText()).includes(note.text));
 }
 report.checks.push('Source-specific City Pop, Bomba and Krautrock introductions and complete arranging notes');
 await page.goto(base+'/?view=styles&style=asi03#token='+f.token);await page.locator('.style-card').first().waitFor();await page.locator('.style-dossier h2').waitFor();
 await page.getByRole('button',{name:'关系地图',exact:true}).click();await page.getByLabel('风格关系地图',{exact:true}).waitFor();await until(()=>page.locator('.style-map path').count().then(n=>n>2),'Relationship lines not loaded');
 const svg=page.getByLabel('风格关系地图',{exact:true}),before=await svg.getAttribute('viewBox');await page.getByRole('button',{name:'放大风格地图',exact:true}).click();assert.notEqual(await svg.getAttribute('viewBox'),before);
 await page.getByRole('button',{name:'层次',exact:true}).click();await page.screenshot({path:path.join(f.report,'map-4k.png')});
 await page.getByRole('button',{name:'浏览',exact:true}).click();
 await page.setViewportSize({width:1120,height:720});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(f.report,'library-narrow.png')});
 await page.setViewportSize({width:760,height:720});await page.locator('.style-card').first().click();assert(await page.locator('.style-dossier').isVisible());await page.getByRole('button',{name:'返回风格列表',exact:true}).click();assert(await page.locator('.style-browser').isVisible());
 report.checks.push('Relationship map, independent zoom and narrow drawer layout');
 await page.setViewportSize({width:1536,height:864});await page.getByRole('button',{name:'返回工作室',exact:true}).click();await page.getByRole('button',{name:'打开编曲',exact:true}).click();await page.locator('.studio').waitFor();
 await page.getByRole('button',{name:'打开风格库',exact:true}).click();await page.locator('.style-library-dialog[open]').waitFor();await page.locator('.style-dossier h2').waitFor();
 const rev=await page.locator('.studio').getAttribute('data-revision');await page.keyboard.press('Escape');await page.locator('.style-library-dialog').waitFor({state:'detached'});assert.equal(await page.locator('.studio').getAttribute('data-revision'),rev);
 report.checks.push('Workspace opens and closes reference library without replacing the editor or revision');
 assert.deepEqual(report.externalRequests,[]);assert.deepEqual(report.errors,[]);report.status='succeeded';
}catch(e){report.status='failed';report.failure=String(e.stack??e);await page.screenshot({path:path.join(f.report,'failure.png')}).catch(()=>{});throw e;}
finally{await writeFile(path.join(f.report,'report.json'),JSON.stringify(report,null,2));await page.request.post(base+'/v1/commands',{data:{operation:'engine.close',arguments:{}}}).catch(()=>{});await browser.close();}
console.log(JSON.stringify(report));
