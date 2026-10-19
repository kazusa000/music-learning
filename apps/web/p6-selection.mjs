// Selection must not silently expand when an edit or version switch changes the displayed revision.
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const [tokenFile,flowFile,output]=process.argv.slice(2);
if(!output||output.startsWith('\\\\'))throw new Error('New local output required');
await mkdir(output,{recursive:false});
const flow=JSON.parse(await readFile(flowFile,'utf8')),token=(await readFile(tokenFile,'ascii')).trim();
const browser=await chromium.launch({headless:true});const errors=[];
try{
 const page=await browser.newPage({viewport:{width:1600,height:1120}});page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(`http://127.0.0.1:8765/?project=${flow.project_id}&view=workbench#token=${token}`);
 await page.getByRole('region',{name:'音乐工作台'}).waitFor();
 await page.getByLabel('循环起点秒',{exact:true}).fill('60');await page.getByLabel('循环终点秒',{exact:true}).fill('75');
 await page.getByRole('button',{name:'版本',exact:true}).click();
 for(const [id,expected] of [[flow.initial_revision,flow.initial_revision],['head',flow.final_revision]]){
  await page.getByLabel('工程版本',{exact:true}).selectOption(id);
  await page.waitForFunction(value=>document.querySelector('.studio')?.dataset.revision===value,expected);
  assert.equal(await page.getByLabel('循环起点秒',{exact:true}).inputValue(),'60');
  assert.equal(await page.getByLabel('循环终点秒',{exact:true}).inputValue(),'75');
 }
 await page.getByRole('button',{name:'讨论',exact:true}).click();
 await page.getByRole('button',{name:'允许改编模式',exact:true}).click();
 assert.match(await page.locator('.discussion-compose>small').innerText(),/01:00.0–01:15.0/);
 await page.waitForFunction(()=>Number(document.querySelector('.piano-roll')?.dataset.waveformBuckets)>0);
 await page.screenshot({path:path.join(output,'workbench.png'),fullPage:true});
 assert.deepEqual(errors,[]);
 await writeFile(path.join(output,'summary.json'),JSON.stringify({status:'succeeded',selection_start_seconds:60,selection_end_seconds:75,versions_tested:2,model_calls:0,page_errors:errors},null,2));
 console.log(JSON.stringify({status:'succeeded',selection_preserved:true,model_calls:0}));
}finally{await browser.close();}
