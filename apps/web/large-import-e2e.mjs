import {chromium} from 'playwright';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=path.resolve(process.argv[2]);
const config=JSON.parse(await readFile(path.join(root,'accept.local.json'),'utf8'));
assert(config.port!==8765&&config.data_root.startsWith(root));
const token=(await readFile(path.join(config.data_root,'session-token.local'),'ascii')).trim();
const owner=JSON.parse(await readFile(path.join(root,'accept-owner.json'),'utf8'));
const browser=await chromium.launch({channel:'msedge',headless:true});
const results=[];
try{
 for(const viewport of [{width:1440,height:900},{width:1536,height:864}]){
  const context=await browser.newContext({viewport,deviceScaleFactor:viewport.width===1536?2.5:1});
  const page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${config.port}/?project=${owner.project_id}#token=${token}`);
  const progress=page.getByRole('region',{name:'整曲导入进度'});
  await progress.waitFor({timeout:30000});
  const bounds=await progress.boundingBox();assert(bounds&&bounds.width>350&&bounds.x>=0);
  const value=await progress.getByRole('progressbar').getAttribute('aria-valuenow');
  assert(Number(value)>=0&&Number(value)<=100);
  assert.equal(await page.locator('.import-steps li').count(),7);
  assert.equal(await page.getByText('音频分轨',{exact:true}).count(),0);
  await page.waitForTimeout(400);
  const fill=await progress.locator('.import-progress-track').evaluate(node=>node.firstElementChild.getBoundingClientRect().width/node.getBoundingClientRect().width);
  const now=Number(await progress.getByRole('progressbar').getAttribute('aria-valuenow'));
  assert(Math.abs(fill-now/100)<.025);
  await page.screenshot({path:path.join(root,`import-${viewport.width}.png`)});
  await page.reload();await progress.waitFor({timeout:30000});
  assert.equal(errors.length,0,errors.join('\n'));
  results.push({viewport,dpr:await page.evaluate(()=>devicePixelRatio),progress:value,refreshRestored:true,errors});
  await context.close();
 }
 await writeFile(path.join(root,'import-browser.json'),JSON.stringify(results,null,2));
 console.log(JSON.stringify(results));
}finally{await browser.close();}
