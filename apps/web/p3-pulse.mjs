// Measure actual stereo pulses in the browser's running audio graph.
import {chromium} from 'playwright';
import {readFile,mkdir,writeFile,unlink} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const[tokenFile,ownerFile,output]=process.argv.slice(2);
await mkdir(output,{recursive:false});
const token=(await readFile(tokenFile,'ascii')).trim(),owner=JSON.parse(await readFile(ownerFile,'utf8'));
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1400,height:950}});
const errors=[];
const probeName='__p3_audio_probe_'+crypto.randomUUID().replaceAll('-','')+'.js';
const probeFile=path.join(path.dirname(fileURLToPath(import.meta.url)),'dist',probeName);
try{
  page.on('pageerror',error=>errors.push(String(error)));
  const worklet=`class PulseTap extends AudioWorkletProcessor{constructor(){super();this.last=[false,false];}process(inputs){const input=inputs[0];for(let channel=0;channel<2;channel++){const values=input[channel];if(!values){this.last[channel]=false;continue;}for(let i=0;i<values.length;i++){const on=Math.abs(values[i])>.1;if(on&&!this.last[channel])this.port.postMessage({channel,frame:currentFrame+i,rate:sampleRate});this.last[channel]=on;}}return true;}}registerProcessor('pulse-tap',PulseTap);`;
  await writeFile(probeFile,worklet,{flag:'wx'});
  await page.addInitScript(probeName=>{
    const Original=window.AudioContext;window.__pulseEdges=[];window.__pulseStarts=[];window.__pulseContexts=[];window.__probeErrors=[];
    window.AudioContext=class extends Original{
      constructor(...args){super(...args);
        window.__pulseContexts.push(this);
        this.audioWorklet.addModule('/'+probeName).then(()=>{this.__tap=new AudioWorkletNode(this,'pulse-tap',{numberOfInputs:1,numberOfOutputs:1,channelCount:2,channelCountMode:'explicit',outputChannelCount:[2]});this.__tap.port.onmessage=event=>window.__pulseEdges.push(event.data);this.__tap.connect(this.destination);}).catch(error=>window.__probeErrors.push(String(error)));
      }
      createBufferSource(){const source=super.createBufferSource(),connect=source.connect.bind(source),start=source.start.bind(source);
        source.connect=(target,...rest)=>{if(target===this.destination){if(!this.__tap)throw new Error('Pulse tap was not ready');connect(this.__tap);}return connect(target,...rest);};
        source.start=(when,offset,...rest)=>{window.__pulseStarts.push({when,offset,frames:source.buffer.length,rate:source.buffer.sampleRate,loopStart:source.loopStart,loopEnd:source.loopEnd});return start(when,offset,...rest);};return source;}
    };
  },probeName);
  await page.goto(`http://127.0.0.1:8765/?project=${owner.project_id}&view=workbench#token=${token}`);
  await page.getByRole('region',{name:'音乐工作台'}).waitFor();
  const response=await page.request.post('http://127.0.0.1:8765/v1/commands',{data:{operation:'audition.set',arguments:{project_id:owner.project_id,state:{revision_id:owner.revision_id,position_sample:298*owner.sample_rate,mode:'tracks',track_ids:owner.track_ids,playing:false,loop:true,loop_start_sample:298*owner.sample_rate,loop_end_sample:299*owner.sample_rate}},expected_revision:owner.revision_id,idempotency_key:crypto.randomUUID()}});
  assert.equal((await response.json()).ok,true);
  await page.waitForFunction(()=>Number(document.querySelector('.studio').dataset.position)===298);
  await page.getByRole('button',{name:'播放',exact:true}).click();
  await page.waitForFunction(()=>window.__pulseEdges.length>=10,null,{timeout:30000});
  await page.getByRole('button',{name:'暂停',exact:true}).click();
  const evidence=await page.evaluate(()=>({edges:window.__pulseEdges,starts:window.__pulseStarts}));
  const left=evidence.edges.filter(e=>e.channel===0),right=evidence.edges.filter(e=>e.channel===1);
  const count=Math.min(left.length,right.length);
  assert.ok(count>=5);
  const deltas=Array.from({length:count},(_,i)=>Math.abs(left[i].frame-right[i].frame));
  const periods=left.slice(1).map((e,i)=>e.frame-left[i].frame);
  assert.ok(deltas.every(delta=>delta<=1));
  assert.ok(periods.every(period=>period===left[0].rate));
  assert.ok(evidence.starts.every(s=>Math.abs(s.frames/s.rate-300)<1e-6));
  assert.equal(evidence.starts[0].when,evidence.starts[1].when);
  assert.equal(evidence.starts.length,2,'one cue must start each of the two sources exactly once');
  assert.deepEqual(errors,[]);
  const summary={project_id:owner.project_id,decoded_duration_seconds:300,loops_observed:count,max_track_delta_samples:Math.max(...deltas),loop_period_samples:periods,audio_context_sample_rate:left[0].rate,evidence,measurement:'actual running Web Audio graph tap; no acoustic speaker recording',page_errors:errors};
  await writeFile(path.join(output,'summary.json'),JSON.stringify(summary,null,2));
  console.log(JSON.stringify(summary));
}catch(error){
  const diagnostic=await page.evaluate(()=>({edges:window.__pulseEdges,starts:window.__pulseStarts,contexts:window.__pulseContexts?.map(c=>({state:c.state,time:c.currentTime,rate:c.sampleRate,tap:!!c.__tap})),probe_errors:window.__probeErrors,studio:document.querySelector('.studio')?.dataset,message:document.querySelector('.studio-message')?.textContent}));
  await writeFile(path.join(output,'failure.json'),JSON.stringify({error:String(error),page_errors:errors,diagnostic},null,2));
  console.log(JSON.stringify(diagnostic));throw error;
}finally{await browser.close();await unlink(probeFile).catch(()=>{});}
