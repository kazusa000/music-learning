// Simulated responses exercise the real UI without inference or test chats in
// the user's database. SQLite persistence and history isolation have own tests.
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const [tokenFile,output]=process.argv.slice(2),base='http://127.0.0.1:8765',pid='4012a10f50574b2395d4ec4a9d8f27b5';
await mkdir(output,{recursive:false});const token=(await readFile(tokenFile,'ascii')).trim();
const before=await fetch(base+'/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({operation:'revisions.get',arguments:{project_id:pid}})}).then(r=>r.json()).then(d=>d.result);
const citation={id:'f'.repeat(32),project_id:pid,revision_id:before.id,start_sample:60*44100,end_sample:75*44100,track_id:null,entity_ids:[],label:'60–75秒',source:'project_events_and_candidates'};
const text='# 和声分析\n\n**属和弦**连接到主和弦。\n\n- 贝斯支撑根音\n- 弦乐延长共同音\n\n| 和弦 | 功能 |\n|---|---|\n| G7 | V7 |\n\n```text\nG7 → C\n```\n\n[片段](music://citation/'+citation.id+')\n\n[坏链接](javascript:alert(1))\n<script>window.markdownXss=1</script>';
const makeTurn=(id,session_id,body)=>({id,session_id,project_id:pid,revision_id:before.id,status:'succeeded',text:body,payload:{text:'测试问题'},context:{start_sample:0,end_sample:before.snapshot.duration_samples,sample_rate:44100},tools:[],citations:[citation],error:null,created_at:1,updated_at:1});
const chats=new Map([['legacy',[makeTurn('a'.repeat(32),null,'**依据工程候选标注的和声解读**\n\n原有讨论保留。')]]]),sessions=[],submits=[],writes=[];
const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1600,height:1100}}),errors=[];page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(String(e)));
await page.route('**/v1/commands',async route=>{const b=route.request().postDataJSON(),a=b.arguments;let result;
  if(b.operation==='dialogue.sessions.list')result=sessions;
  else if(b.operation==='dialogue.sessions.create'){const s={id:String(sessions.length+1).repeat(32),project_id:pid,title:a.title,created_at:1};sessions.unshift(s);chats.set(s.id,[]);result=s;}
  else if(b.operation==='dialogue.models')result={models:[{id:'test-a',name:'测试 A',is_default:true},{id:'test-b',name:'测试 B',is_default:false}],login_mode:a.login_mode};
  else if(b.operation==='dialogue.list')result=chats.get(a.session_id??'legacy')??[];
  else if(b.operation==='conversations.list')result=[];
  else if(b.operation==='dialogue.submit'){submits.push(a);result=makeTurn('b'.repeat(32),a.session_id,text);chats.get(a.session_id).push(result);}
  else {if(['edits.apply','jobs.submit','revisions.restore'].includes(b.operation))writes.push(b.operation);return route.continue();}
  await route.fulfill({json:{ok:true,result}});
});
try{
  await page.goto(base+'/#token='+token);await page.getByRole('button',{name:'打开工程',exact:true}).click();
  await page.getByText('原有讨论保留。',{exact:true}).waitFor();assert.equal(await page.getByText('依据工程候选标注的和声解读',{exact:true}).count(),0);
  await page.getByRole('button',{name:'＋ 新会话',exact:true}).click();await page.waitForFunction(()=>document.querySelector('select[aria-label="会话"]').value!== '');assert.equal(await page.getByText('原有讨论保留。',{exact:true}).count(),0);
  const a=await page.getByLabel('会话',{exact:true}).inputValue();await page.getByLabel('对话模型',{exact:true}).focus();await page.getByLabel('对话模型',{exact:true}).selectOption('test-b');
  await page.getByLabel('讨论问题',{exact:true}).fill('测试 Markdown');await page.getByRole('button',{name:'发送 ↗',exact:true}).click();await page.locator('.discussion-reply table').waitFor();
  assert.equal(submits[0].model,'test-b');assert.equal(submits[0].session_id,a);assert.equal(await page.locator('.discussion-reply h1').innerText(),'和声分析');assert.equal(await page.locator('.discussion-reply li').count(),2);assert.equal(await page.locator('.discussion-reply strong').innerText(),'属和弦');assert.match(await page.locator('.discussion-reply pre code').innerText(),/G7/);
  assert.equal(await page.locator('a[href^="javascript:"]').count(),0);assert.equal(await page.evaluate(()=>window.markdownXss),undefined);
  await page.getByRole('button',{name:'＋ 新会话',exact:true}).click();await page.waitForFunction(old=>document.querySelector('select[aria-label="会话"]').value!==old,a);assert.equal(await page.locator('.discussion-reply table').count(),0);
  await page.getByLabel('会话',{exact:true}).selectOption(a);await page.locator('.discussion-reply table').waitFor();await page.reload();await page.locator('.discussion-reply table').waitFor();assert.equal(await page.getByLabel('对话模型',{exact:true}).inputValue(),'test-b');
  await page.locator('.discussion-reply .inline-citation').click();assert.equal(await page.getByLabel('循环起点秒',{exact:true}).inputValue(),'60');assert.equal(await page.getByLabel('循环终点秒',{exact:true}).inputValue(),'75');
  await page.screenshot({path:path.join(output,'chat.png'),fullPage:true});assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
  const report={status:'succeeded',new_session:true,session_history_isolated:true,history_preserved:true,model_forwarded:true,reload_persisted:true,markdown_heading_list_table_code:true,citation_locates:true,raw_html_disabled:true,unsafe_links_disabled:true,page_errors:errors,production_writes:writes,provider_calls:0};await writeFile(path.join(output,'summary.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(e){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});throw e;}finally{await browser.close();}
