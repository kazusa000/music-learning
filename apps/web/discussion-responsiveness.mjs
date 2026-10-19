// Browser transport simulation: exercises loading/stream/cancel/retry without provider spend.
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const [tokenFile,projectId,output]=process.argv.slice(2),base='http://127.0.0.1:8765';
await mkdir(output,{recursive:false});
const token=(await readFile(tokenFile,'ascii')).trim();
const response=await fetch(base+'/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({operation:'revisions.get',arguments:{project_id:projectId}})});
const before=(await response.json()).result;
const makeTurn=(id,status,text)=>({id,project_id:projectId,revision_id:before.id,status,text,payload:{text:'模拟讨论'},context:{start_sample:0,end_sample:before.snapshot.duration_samples,sample_rate:before.snapshot.sample_rate},tools:[],citations:[],error:null,created_at:1,updated_at:1});
const old=makeTurn('a'.repeat(32),'succeeded','已保存的讨论'),history=[old];
old.tools=[{arguments:{action:'summary'},success:true,result:{detail:'LAZY_TOOL_EVIDENCE_MARKER'}}];
let polls=0,finish=false,current=null;
const writes=[],errors=[],browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage({viewport:{width:1680,height:1120}});page.on('pageerror',e=>errors.push(String(e)));
 await page.route('**/v1/commands',async route=>{
  const b=route.request().postDataJSON();let value;
  if(b.operation==='dialogue.list')value=history;
  else if(b.operation==='conversations.list')value=[];
  else if(b.operation==='dialogue.submit'||b.operation==='dialogue.retry'){
   current=makeTurn((b.operation==='dialogue.submit'?'b':'c').repeat(32),'queued','');history.push(current);polls=0;value=current;
  }else if(b.operation==='dialogue.cancel'){
   current.status='cancelled';current.updated_at++;value=current;
  }else if(b.operation==='dialogue.get'){
   polls++;current.status=finish&&polls>=2?'succeeded':'running';current.text=current.status==='succeeded'?'模拟回复完成，原版本保留。':'模拟流式内容 '+polls;current.updated_at++;value=current;
  }else{
   if(['edits.apply','revisions.restore','jobs.submit','arrangements.apply'].includes(b.operation))writes.push(b.operation);
   return route.continue();
  }
  await route.fulfill({json:{schema_version:1,ok:true,result:value}});
 });
 await page.goto(base+`/?project=${projectId}&view=workbench#token=${token}`);
 await page.getByText('已保存的讨论',{exact:true}).waitFor();
 assert.equal(await page.locator('pre').filter({hasText:'LAZY_TOOL_EVIDENCE_MARKER'}).count(),0);
 await page.locator('.tool-history>summary').click();await page.locator('.tool-history details>summary').click();
 await page.locator('pre').filter({hasText:'LAZY_TOOL_EVIDENCE_MARKER'}).waitFor();
 await page.locator('.discussion-settings>summary').click();await page.getByLabel('Codex 登录方式',{exact:true}).selectOption('existing');await page.reload();
 await page.getByText('已保存的讨论',{exact:true}).waitFor();
 assert.equal(await page.getByLabel('Codex 登录方式',{exact:true}).inputValue(),'existing');
 await page.getByLabel('讨论问题',{exact:true}).fill('模拟测试，不访问真实供应商');
 await page.getByRole('button',{name:'发送 ↗',exact:true}).click();
 const first=page.locator('[data-turn="'+ 'b'.repeat(32)+'"]');
 await first.getByText(/模拟流式内容/).waitFor();
 await first.getByRole('button',{name:'停止',exact:true}).click();await first.locator('[class="turn-meta"]').getByText(/已停止/).waitFor();
 assert.match(await first.innerText(),/模拟流式内容/);
 finish=true;await first.getByRole('button',{name:'重试此问题',exact:true}).click();
 const second=page.locator('[data-turn="'+ 'c'.repeat(32)+'"]');
 await second.getByText('模拟回复完成，原版本保留。',{exact:true}).waitFor();
 const endedPolls=polls;await page.waitForTimeout(1800);assert.equal(polls,endedPolls);
 assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);
 const report={status:'succeeded',login_remembered:true,history_loaded:true,tool_details_lazy:true,partial_text:true,cancel_preserved_text:true,retry_completed:true,terminal_polling_stopped:true,production_writes:writes,provider_calls:0,page_errors:errors};
 await writeFile(path.join(output,'summary.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}
