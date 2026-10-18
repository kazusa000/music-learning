// Usage: node probe.mjs <Python with psutil> <player-assets.json> <new output dir>
import {chromium} from 'playwright';
import {spawn,execFileSync} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';

const [python,manifest,output]=process.argv.slice(2);
if(!output)throw new Error('Arguments: python, asset manifest, output directory');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
await mkdir(output,{recursive:false});
const server=spawn(python,[path.join(root,'scripts/serve_player_probe.py'),manifest],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
let browser;let errors=[];const memory=[];let failure;
try{
  const url=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Probe server did not start')),15000);
    server.once('exit',code=>{clearTimeout(timer);reject(new Error(`Server exit ${code}`))});
    server.stdout.once('data',data=>{clearTimeout(timer);try{resolve(JSON.parse(data.toString()).url)}catch(e){reject(e)}});
  });
  browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required']});
  const context=await browser.newContext();const page=await context.newPage();
  page.on('pageerror',error=>errors.push(String(error)));
  const session=await browser.newBrowserCDPSession();
  async function sampleMemory(stage){
    const {processInfo}=await session.send('SystemInfo.getProcessInfo');
    const ids=processInfo.map(p=>p.id);
    const rss=Number(execFileSync(python,['-c','import psutil,sys; print(sum(psutil.Process(int(p)).memory_info().rss for p in sys.argv[1:] if psutil.pid_exists(int(p))))',...ids.map(String)],{encoding:'utf8',windowsHide:true}).trim());
    memory.push({stage,browser_process_tree_rss_bytes:rss});
  }
  await page.goto(url);await page.waitForFunction(()=>Boolean(window.probe));await sampleMemory('initial');
  const range=await page.request.get(url+'audio/original',{headers:{Range:'bytes=0-63'}});
  assert.equal(range.status(),206);assert.equal((await range.body()).length,64);
  await page.evaluate(()=>probe.load('original'));
  assert.equal((await page.evaluate(()=>probe.state())).duration,194);
  await sampleMemory('original-decoded');
  for(const target of [0,97,193.5]){
    await page.evaluate(t=>probe.seek(t),target);
    assert.ok(Math.abs((await page.evaluate(()=>probe.state())).seconds-target)<1/48000);
  }
  await page.evaluate(async()=>{await probe.seek(120);await probe.play()});
  await page.waitForTimeout(300);
  const playing=await page.evaluate(()=>probe.state());
  assert.equal(playing.context_state,'running');assert.ok(playing.seconds>120.15&&playing.seconds<120.6);
  await page.evaluate(()=>probe.pause());
  const paused=await page.evaluate(()=>probe.state());await page.waitForTimeout(100);
  assert.equal((await page.evaluate(()=>probe.state())).seconds,paused.seconds);
  await page.evaluate(async()=>{await probe.setLoop(19.2,19.7);await probe.play()});
  await page.waitForTimeout(1150);const loop=await page.evaluate(()=>probe.state());
  assert.ok(loop.seconds>=19.2&&loop.seconds<19.7);
  await page.evaluate(async()=>{probe.pause();await probe.setLoop(null);await probe.load('model-reconstruction')});
  assert.equal((await page.evaluate(()=>probe.state())).duration,196);
  await sampleMemory('original-plus-reconstruction');
  await page.evaluate(()=>probe.load('hybrid-sparse-bass'));
  assert.equal((await page.evaluate(()=>probe.state())).duration,194);
  await sampleMemory('hybrid-decoded');
  await page.evaluate(()=>probe.load('pulses'));
  const pulseResults=await page.evaluate(async()=>{
    const {startBuffer}=await import('./player.js');
    const buffer=probe.buffer;
    const findImpulses=buffer=>{const hits=[];const data=buffer.getChannelData(0);for(let i=0;i<data.length;i++)if(data[i]>.25)hits.push(i);return hits;};
    const impulses=findImpulses(buffer);
    async function offline(offset,loop){
      const context=new OfflineAudioContext(2,48000,48000);
      startBuffer(context,buffer,0,offset,loop);
      const rendered=await context.startRendering();
      return findImpulses(rendered);
    }
    return {decoded_positions:impulses,seek_120_seconds:await offline(120,null),loop:await offline(1.7,{start:1.7,end:1.9})};
  });
  assert.deepEqual(pulseResults.decoded_positions,[5904,85296,5765904,9288000]);
  assert.deepEqual(pulseResults.seek_120_seconds,[5904]);
  const expected=[3696,13296,22896,32496,42096];
  const maxLoopError=Math.max(...pulseResults.loop.map((x,i)=>Math.abs(x-expected[i])));
  assert.equal(pulseResults.loop.length,expected.length);assert.ok(maxLoopError<=1);
  await sampleMemory('all-four-assets');
  const state=await page.evaluate(()=>probe.state());
  await page.screenshot({path:path.join(output,'player.png'),fullPage:true});
  await page.evaluate(()=>probe.close());assert.deepEqual(errors,[]);
  await writeFile(path.join(output,'summary.json'),JSON.stringify({status:'completed',browser:browser.version(),
    duration_original:194,duration_model_reconstruction:196,range_status:206,playing,paused,loop,
    pulse_results:pulseResults,max_loop_error_samples:maxLoopError,state,memory,
    memory_sampling:'milestone RSS snapshots, not a continuous peak',
    limitations:['Headless Web Audio and OfflineAudioContext checks; no speaker or human listening test',
      'Whole files decoded into RAM; streaming/large-library memory strategy remains future work']},null,2));
  console.log(JSON.stringify({output,status:'completed',maxLoopError,decoded_pcm_bytes:state.decoded_pcm_bytes}));
}catch(error){failure=error;await writeFile(path.join(output,'failure.json'),JSON.stringify({error:String(error),errors,memory},null,2));}
finally{
  if(browser)await browser.close();
  if(server.exitCode===null){
    if(process.platform==='win32')execFileSync('taskkill',['/PID',String(server.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
    else server.kill();
  }
}
if(failure)throw failure;
