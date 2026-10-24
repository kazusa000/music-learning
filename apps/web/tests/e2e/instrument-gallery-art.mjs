import {readFile,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
const {chromium}=createRequire(path.join(process.env.MUSIC_LEARNING_TEST_RESOURCE_ROOT,'apps/web/package.json'))('playwright');
const f=JSON.parse(await readFile(path.join(process.argv[2],'atlas.json'),'utf8'));
const browser=await chromium.launch({channel:'msedge',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1800}});
const files=[];
try{
 for(let i=0;i<f.images.length;i+=24){
  const html=('<style>body{margin:0;padding:16px;background:#151412;color:#cbbca8;font:12px Arial}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card{background:#24211d;border-radius:8px;padding:9px;min-width:0}img{display:block;width:100%;height:225px;object-fit:contain}.name{height:24px;overflow:hidden}</style><div class="grid">'+f.images.slice(i,i+24).map(x=>'<div class="card"><img src="'+pathToFileURL(x.file)+'"><div class="name">'+x.id+'</div></div>').join('')+'</div>');
  const document=path.join(process.argv[2],'art-page.html');await writeFile(document,html);await page.goto(pathToFileURL(document).href);
  await page.waitForFunction(()=>[...document.images].every(i=>i.complete&&i.naturalWidth>0));
  const file=path.join(f.report,'art-'+String(1+i/24).padStart(2,'0')+'.png');
  await page.screenshot({path:file,fullPage:true});files.push(file);
 }
 await writeFile(path.join(f.report,'atlas-report.json'),JSON.stringify({status:'succeeded',images:f.images.length,files},null,2));
}finally{await browser.close();}
