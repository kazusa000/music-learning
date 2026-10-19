// Actual production browser transport, no fake playback or vendor UI operations.
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const[tokenFile,projectId,output]=process.argv.slice(2);
if(!output||output.startsWith('\\\\'))throw new Error('Local token file, project id and new output required');
await mkdir(output,{recursive:false});
const token=(await readFile(tokenFile,'ascii')).trim();
const browser=await chromium.launch({headless:true});
const errors=[];
try{
  const page=await browser.newPage({viewport:{width:1520,height:1060}});
  page.on('pageerror',error=>errors.push(String(error)));
  await page.addInitScript(()=>{
    const Original=window.AudioContext;window.__starts=[];window.__clocks=[];
    window.AudioContext=class extends Original{
      constructor(...args){super(...args);window.__clocks.push(this);}
      createBufferSource(){const source=super.createBufferSource();const start=source.start.bind(source);
        source.start=(when=0,offset=0,...rest)=>{window.__starts.push({when,offset,rate:this.sampleRate,duration:source.buffer?.duration,loop:source.loop,loopStart:source.loopStart,loopEnd:source.loopEnd,contextIndex:window.__clocks.indexOf(this)});return start(when,offset,...rest);};return source;}
    };
  });
  await page.goto(`http://127.0.0.1:8765/?project=${projectId}&view=workbench#token=${token}`);
  const studio=page.getByRole('region',{name:'音乐工作台'});
  await studio.waitFor();await page.getByRole('img',{name:'整曲波形与钢琴卷帘'}).waitFor();
  const api=async(operation,args={},expected)=>{
    const response=await page.request.post('http://127.0.0.1:8765/v1/commands',{data:{operation,arguments:args,expected_revision:expected??null,idempotency_key:expected?crypto.randomUUID():null}});
    const body=await response.json();assert.equal(body.ok,true,JSON.stringify(body));return body.result;
  };
  const project=await api('projects.get',{project_id:projectId});
  const revision=await api('revisions.get',{project_id:projectId});
  const rate=revision.snapshot.sample_rate,duration=revision.snapshot.duration_samples/rate;
  await api('audition.set',{project_id:projectId,state:{revision_id:revision.id,position_sample:150*rate,mode:'original',track_ids:[],loop:false,loop_start_sample:0,loop_end_sample:revision.snapshot.duration_samples,playing:false}},project.current_revision);
  await page.waitForFunction(()=>Math.abs(Number(document.querySelector('.studio')?.dataset.position)-150)<.001);
  await page.getByRole('button',{name:'播放',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.studio')?.dataset.playing==='true',{timeout:90000});
  await page.waitForTimeout(400);
  const original=await studio.getAttribute('data-position');assert.ok(+original>150);
  await page.getByRole('button',{name:/^重建/}).click();
  await page.waitForFunction(()=>window.__starts.length>=2,{timeout:90000});
  await page.waitForTimeout(400);
  const measureClock=()=>page.evaluate(()=>{
    const last=window.__starts.at(-1),context=window.__clocks[last.contextIndex];
    const stamp=context.getOutputTimestamp();
    const clock=stamp.contextTime&&stamp.performanceTime?stamp.contextTime+Math.max(0,performance.now()-stamp.performanceTime)/1000:Math.max(0,context.currentTime-context.baseLatency-(context.outputLatency??0));
    let expected=last.offset+Math.max(0,clock-last.when);
    if(last.loop&&expected>=last.loopEnd)expected=last.loopStart+(expected-last.loopStart)%(last.loopEnd-last.loopStart);
    const displayed=Number(document.querySelector('.studio').dataset.position);
    const raw=Math.abs(displayed-expected);
    const error=last.loop?Math.min(raw,Math.abs(raw-(last.loopEnd-last.loopStart))):raw;
    return{displayed,expected,error_seconds:error,raw_wrapped_difference_seconds:raw,base_latency:context.baseLatency,output_latency:context.outputLatency??null,schedule:last};
  });
  const ab=await measureClock();assert.ok(ab.error_seconds<=.05,JSON.stringify(ab));assert.ok(ab.displayed>+original);
  assert.ok(Math.abs(ab.schedule.duration-(duration+2))<.001);
  await page.getByRole('button',{name:'独奏 3',exact:true}).click();
  await page.waitForTimeout(1400);
  const count=await page.evaluate(()=>window.__starts.length);
  await page.getByRole('button',{name:'试听静音 1',exact:true}).click();
  await page.waitForFunction(n=>window.__starts.length>=n+2,count,{timeout:90000});
  const pair=await page.evaluate(()=>window.__starts.slice(-2));assert.equal(pair[0].when,pair[1].when);assert.equal(pair[0].offset,pair[1].offset);
  await page.getByLabel('循环起点秒',{exact:true}).fill('10');
  await page.getByLabel('循环终点秒',{exact:true}).fill('10.2');
  await page.getByRole('button',{name:'应用循环',exact:true}).click();
  await page.waitForFunction(()=>window.__starts.at(-1)?.loop===true);
  await page.waitForTimeout(1650);
  const looping=await measureClock();assert.ok(looping.displayed>=10&&looping.displayed<10.2);
  assert.ok(looping.error_seconds<=.05||Math.abs(looping.error_seconds-.2)<=.05,JSON.stringify(looping));
  await page.getByRole('button',{name:'暂停',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.studio').dataset.playing==='false');
  await page.getByRole('button',{name:'循环',exact:true}).click();
  const range=page.getByLabel('时间轴缩放',{exact:true});await range.focus();await range.press('End');assert.equal(await range.inputValue(),'24');
  await page.getByLabel('时间轴平移',{exact:true}).focus();await page.getByLabel('时间轴平移',{exact:true}).press('End');
  await range.focus();await range.press('Home');
  await page.getByRole('tab',{name:'音色与效果',exact:true}).click();await page.getByLabel('音色与效果的音轨',{exact:true}).selectOption({index:1});await page.locator('.plugin-card').first().waitFor();
  assert.ok((await page.locator('.plugin-card').first().innerText()).includes('VST3'));
  await page.getByRole('tab',{name:'版本',exact:true}).click();
  await page.getByLabel('工程版本',{exact:true}).waitFor();
  const options=await page.getByLabel('工程版本',{exact:true}).locator('option').evaluateAll(nodes=>nodes.map(n=>n.value));
  if(options.length>2){await page.getByLabel('工程版本',{exact:true}).selectOption(options[2]);await page.waitForFunction(id=>document.querySelector('.studio').dataset.revision===id,options[2]);}
  await page.getByLabel('工程版本',{exact:true}).selectOption('head');
  await page.waitForFunction(id=>document.querySelector('.studio').dataset.revision===id,project.current_revision);
  await page.getByRole('tab',{name:'分析',exact:true}).click();
  await page.waitForFunction(()=>Number(document.querySelector('.piano-roll')?.dataset.waveformBuckets)>0);
  await page.screenshot({path:path.join(output,'workbench.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(150);
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth);assert.ok(overflow<=1,`overflow ${overflow}`);
  await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
  await page.setViewportSize({width:1520,height:1060});
  await page.reload();await studio.waitFor();
  await page.getByRole('button',{name:'返回音乐库',exact:true}).click();
  await page.screenshot({path:path.join(output,'homepage.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  const summary={project_id:projectId,duration,original_position:+original,ab,multiple_sources:pair,looping,mobile_overflow_pixels:overflow,page_errors:errors,nas_accessed:false};
  await writeFile(path.join(output,'summary.json'),JSON.stringify(summary,null,2));console.log(JSON.stringify(summary));
}finally{await browser.close();}
