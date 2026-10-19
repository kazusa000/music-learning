import {chromium} from 'playwright';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';

const [tokenFile,projectId,output]=process.argv.slice(2);
if(!output||output.startsWith('\\\\'))throw new Error('Use token file, project id and a new local output directory');
await mkdir(output,{recursive:false});
const token=(await readFile(tokenFile,'ascii')).trim();
const browser=await chromium.launch({headless:true});
const errors=[];
try{
  const page=await browser.newPage({viewport:{width:1520,height:1060}});
  page.on('pageerror',error=>errors.push(String(error)));
  await page.goto(`http://127.0.0.1:8765/?project=${projectId}&view=workbench#token=${token}`);
  await page.getByRole('region',{name:'音乐工作台'}).waitFor();
  await page.getByRole('img',{name:'整曲波形与钢琴卷帘'}).waitFor();
  await page.waitForTimeout(1800);
  await page.waitForFunction(()=>Number(document.querySelector('.piano-roll')?.dataset.waveformBuckets)>0);
  await page.screenshot({path:path.join(output,'workbench.png'),fullPage:true});
  await page.getByRole('button',{name:'返回音乐库',exact:true}).click();
  await page.screenshot({path:path.join(output,'homepage.png'),fullPage:true});
  await writeFile(path.join(output,'preview.json'),JSON.stringify({project_id:projectId,page_errors:errors},null,2));
  if(errors.length)throw new Error(errors.join('\n'));
  console.log(JSON.stringify({screenshots:output,page_errors:errors}));
}finally{await browser.close();}
