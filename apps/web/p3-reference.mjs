import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const[tokenFile,ownerFile,output]=process.argv.slice(2);
const owner=JSON.parse(await readFile(ownerFile,'utf8')),token=(await readFile(tokenFile,'ascii')).trim();
await mkdir(output,{recursive:false});
const browser=await chromium.launch({headless:true,args:['--autoplay-policy=document-user-activation-required']});
const page=await browser.newPage({viewport:{width:1520,height:1060}}),errors=[];
try{
  page.on('pageerror',error=>errors.push(String(error)));
  const api=async(operation,args={},expected)=>{
    const response=await fetch('http://127.0.0.1:8765/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({operation,arguments:args,expected_revision:expected??null,idempotency_key:expected?crypto.randomUUID():null})});
    const body=await response.json();assert.equal(body.ok,true,JSON.stringify(body));return body.result;
  };
  const project=await api('projects.get',{project_id:owner.project_id});
  const revision=await api('revisions.get',{project_id:owner.project_id});
  const rate=revision.snapshot.sample_rate;
  await api('audition.set',{project_id:owner.project_id,state:{revision_id:revision.id,position_sample:70*rate,mode:'mix',playing:true,loop:false,loop_start_sample:0,loop_end_sample:revision.snapshot.duration_samples}},project.current_revision);
  await page.goto(`http://127.0.0.1:8765/?project=${owner.project_id}&view=workbench#token=${token}`);
  // Locator/evaluate automation can itself grant transient activation. Poll
  // through Node HTTP until the untouched document reports its real cold state.
  let observed;
  for(let i=0;i<60;i++){
    observed=await api('audition.get',{project_id:owner.project_id});
    if(observed.observed?.status==='needs_activation')break;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  assert.equal(observed.observed?.status,'needs_activation');
  await page.getByRole('region',{name:'音乐工作台'}).waitFor();
  assert.equal(await page.getByRole('button',{name:'播放',exact:true}).isDisabled(),false);
  await page.getByRole('button',{name:'播放',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.studio').dataset.playing==='true',null,{timeout:90000});
  await page.waitForTimeout(250);
  const before=Number(await page.locator('.studio').getAttribute('data-position'));
  await page.getByRole('tab',{name:'版本',exact:true}).click();
  const reference=(await api('renders.list',{project_id:owner.project_id,revision_id:owner.initial_revision})).at(-1);
  await Promise.all([
    page.waitForRequest(request=>request.url().endsWith('/v1/assets/'+reference.mix_asset_id)),
    page.locator('.version-card').filter({hasText:owner.initial_revision.slice(0,8)}).getByRole('button',{name:'设为 A',exact:true}).click()
  ]);
  await page.waitForFunction(()=>document.querySelector('.listening-label')?.textContent.startsWith('REFERENCE'));
  await page.waitForTimeout(300);
  const after=Number(await page.locator('.studio').getAttribute('data-position'));
  assert.ok(after>=before);
  assert.equal(await page.locator('.studio').getAttribute('data-revision'),revision.id);
  const referenceCue=await api('audition.get',{project_id:owner.project_id});
  assert.equal(referenceCue.desired.reference_revision_id,owner.initial_revision);
  await page.getByRole('button',{name:/^重建/}).click();
  await page.waitForTimeout(300);
  const back=Number(await page.locator('.studio').getAttribute('data-position'));assert.ok(back>=after);
  await page.getByRole('button',{name:'暂停',exact:true}).click();
  const restored=await api('revisions.restore',{project_id:owner.project_id,revision_id:owner.initial_revision,description:'P3 browser verification: restore baseline as a new version'},project.current_revision);
  await page.waitForFunction(id=>document.querySelector('.studio').dataset.revision===id,restored.id);
  assert.equal((await api('renders.list',{project_id:owner.project_id,revision_id:restored.id})).length,0);
  assert.ok((await api('renders.list',{project_id:owner.project_id,revision_id:revision.id})).length>0);
  assert.equal(await page.getByRole('button',{name:/^重建/}).isDisabled(),true);
  await page.screenshot({path:path.join(output,'restored.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  const summary={project_id:owner.project_id,activation_before_gesture:'needs_activation',reference_revision:owner.initial_revision,before,reference_position:after,back_to_current_position:back,restored_revision:restored.id,old_audio_retained:true,restored_audio_not_retargeted:true,page_errors:errors};
  await writeFile(path.join(output,'summary.json'),JSON.stringify(summary,null,2));console.log(JSON.stringify(summary));
}catch(error){
  const diagnostic=await page.evaluate(()=>({studio:document.querySelector('.studio')?.dataset,message:document.querySelector('.studio-message')?.textContent,activation:{active:navigator.userActivation.isActive,hasBeenActive:navigator.userActivation.hasBeenActive}}));
  const state=await (await page.request.post('http://127.0.0.1:8765/v1/commands',{data:{operation:'audition.get',arguments:{project_id:owner.project_id}}})).json();
  await writeFile(path.join(output,'failure.json'),JSON.stringify({error:String(error),diagnostic,state,page_errors:errors},null,2));
  await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});
  console.log(JSON.stringify({diagnostic,state}));throw error;
}finally{await browser.close();}
