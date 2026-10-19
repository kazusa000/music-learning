// Read-only full-song loading/idle probe. Does not submit discussion or production jobs.
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const [tokenFile,projectId,output,base='http://127.0.0.1:8765']=process.argv.slice(2);
await mkdir(output,{recursive:false});
const token=(await readFile(tokenFile,'ascii')).trim();
const browser=await chromium.launch({headless:true});
const requests=[],errors=[],writes=[];
try{
 const page=await browser.newPage({viewport:{width:1680,height:1120}});
 page.on('pageerror',e=>errors.push(String(e)));
 page.on('request',request=>{
  if(!request.url().endsWith('/v1/commands'))return;
  const b=request.postDataJSON();
  if(['edits.apply','revisions.restore','jobs.submit','dialogue.submit'].includes(b.operation))writes.push(b.operation);
 });
 page.on('response',response=>{
  if(!response.url().endsWith('/v1/commands'))return;
  const b=response.request().postDataJSON();
  requests.push({operation:b.operation,at:Date.now()});
 });
 const began=Date.now();
 await page.goto(base+`/?project=${projectId}&view=workbench#token=${token}`);
 await page.getByLabel('制作编辑器',{exact:true}).waitFor();
 const editorMs=Date.now()-began;
 await page.getByLabel('讨论问题',{exact:true}).waitFor();
 const composerMs=Date.now()-began;
 // Observe idle polling after initial requests settle; no artificial network-idle wait on SSE.
 await page.waitForTimeout(2000);
 const settled=Date.now();
 await page.waitForTimeout(6000);
 const idle=requests.filter(r=>r.at>=settled);
 const counts={};for(const r of idle)counts[r.operation]=(counts[r.operation]??0)+1;
 const bytes=await page.evaluate(()=>performance.getEntriesByType('resource').filter(r=>r.name.includes('/v1/')).reduce((n,r)=>n+r.decodedBodySize,0));
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 const report={status:'succeeded',editor_ms:editorMs,composer_ms:composerMs,api_decoded_bytes:bytes,idle_seconds:6,idle_commands:idle.length,idle_by_operation:counts,page_errors:errors,production_writes:writes,provider_calls:0};
 await writeFile(path.join(output,'summary.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify(report));
}finally{await browser.close();}
