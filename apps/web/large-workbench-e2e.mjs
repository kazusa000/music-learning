// Independent service only. Reproduce the actual connected Web Audio graph
// offline, stream PCM directly to FFmpeg, and retain measurements rather than WAVs.
import {chromium} from 'playwright';
import {readFile,writeFile} from 'node:fs/promises';
import {spawn} from 'node:child_process';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=path.resolve(process.argv[2]);
const config=JSON.parse(await readFile(path.join(root,'accept.local.json'),'utf8'));
assert(config.port!==8765&&config.data_root.startsWith(root));
const token=(await readFile(path.join(config.data_root,'session-token.local'),'ascii')).trim();
const owner=JSON.parse(await readFile(path.join(root,'accept-owner.json'),'utf8'));
const ffmpeg=(await readFile(path.join(root,'ffmpeg-path.txt'),'utf8')).trim();
const base=`http://127.0.0.1:${config.port}`;
async function call(operation,args){const r=await fetch(base+'/v1/commands',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify({operation,arguments:args})});const d=await r.json();assert(d.ok,JSON.stringify(d.error));return d.result;}
let job;
for(let i=0;i<360;i++){job=await call('jobs.get',{job_id:owner.preprocess_job_id});if(['succeeded','failed','cancelled'].includes(job.status))break;await new Promise(r=>setTimeout(r,5000));}
assert.equal(job.status,'succeeded',JSON.stringify(job.error));
const before=await call('revisions.get',{project_id:owner.project_id});
const jobsBefore=(await call('jobs.list',{project_id:owner.project_id,limit:1000})).length;
const browser=await chromium.launch({channel:'msedge',headless:true,args:['--autoplay-policy=no-user-gesture-required']});
const context=await browser.newContext({viewport:{width:1536,height:864},deviceScaleFactor:2.5});
const page=await context.newPage();let measurement=null;const observed=[];const errors=[];
page.on('pageerror',e=>errors.push(e.message));
await page.addInitScript(()=>{
 const Native=window.AudioContext;window.__graphs=[];
 window.AudioContext=class extends Native{
  constructor(...args){super(...args);const graph={context:this,nodes:[],edges:[]};window.__graphs.push(graph);
   for(const [method,kind] of [['createBufferSource','source'],['createGain','gain'],['createDynamicsCompressor','compressor'],['createChannelSplitter','splitter'],['createChannelMerger','merger'],['createAnalyser','analyser'],['createWaveShaper','shaper']]){
    const make=this[method].bind(this);this[method]=(...args)=>{const node=make(...args),record={node,kind,active:false};graph.nodes.push(record);
     const connect=node.connect.bind(node),disconnect=node.disconnect.bind(node);
     node.connect=(dest,...args)=>{graph.edges.push({source:node,dest,args});return connect(dest,...args);};
     node.disconnect=(...args)=>{graph.edges=graph.edges.filter(e=>e.source!==node||(args.length&&e.dest!==args[0]));return disconnect(...args);};
     if(kind==='source'){const start=node.start.bind(node),stop=node.stop.bind(node);node.start=(...args)=>{record.active=true;return start(...args);};node.stop=(...args)=>{record.active=false;return stop(...args);};node.addEventListener('ended',()=>record.active=false);}
     return node;
    };
   }
  }
 };
 window.__measureConnectedGraph=async()=>{
  const graph=window.__graphs.find(g=>g.nodes.some(r=>r.kind==='source'&&r.active&&r.node.buffer?.duration>300));
  if(!graph)throw new Error('No whole-song sources actually playing');
  const sources=graph.nodes.filter(r=>r.kind==='source'&&r.active&&r.node.buffer?.duration>300);
  const sr=graph.context.sampleRate,length=Math.ceil(Math.max(...sources.map(r=>r.node.buffer.duration))*sr);
  const offline=new OfflineAudioContext(2,length,sr),map=new Map();
  for(const r of graph.nodes){let node;
   if(r.kind==='source'){if(!sources.includes(r))continue;node=offline.createBufferSource();node.buffer=r.node.buffer;}
   if(r.kind==='gain'){node=offline.createGain();node.gain.value=r.node.gain.value;}
   if(r.kind==='compressor'){node=offline.createDynamicsCompressor();for(const key of ['threshold','knee','ratio','attack','release'])node[key].value=r.node[key].value;}
   if(r.kind==='splitter')node=offline.createChannelSplitter(r.node.numberOfOutputs);
   if(r.kind==='merger')node=offline.createChannelMerger(r.node.numberOfInputs);
   if(r.kind==='analyser')node=offline.createGain();
   if(r.kind==='shaper'){node=offline.createWaveShaper();node.curve=r.node.curve;node.oversample=r.node.oversample;}
   if(node)map.set(r.node,node);
  }
  for(const edge of graph.edges){const a=map.get(edge.source),b=edge.dest===graph.context.destination?offline.destination:map.get(edge.dest);if(a&&b)a.connect(b,...edge.args);}
  for(const source of sources)map.get(source.node).start(0);
  const rendered=await offline.startRendering();await window.__startMeasure(sr);
  const left=rendered.getChannelData(0),right=rendered.getChannelData(1);let clipped=0,peak=0;
  for(let offset=0;offset<left.length;offset+=65536){const count=Math.min(65536,left.length-offset),pcm=new Float32Array(count*2);
   for(let i=0;i<count;i++){pcm[2*i]=left[offset+i];pcm[2*i+1]=right[offset+i];peak=Math.max(peak,Math.abs(pcm[2*i]),Math.abs(pcm[2*i+1]));if(Math.abs(pcm[2*i])>=1||Math.abs(pcm[2*i+1])>=1)clipped++;}
   const bytes=new Uint8Array(pcm.buffer);let binary='';for(let j=0;j<bytes.length;j+=8192)binary+=String.fromCharCode(...bytes.subarray(j,j+8192));await window.__consumePCM(btoa(binary));
  }
  return {...await window.__finishMeasure(),sampleRate:sr,sourceCount:sources.length,clipped,peak};
 };
});
await page.exposeFunction('__startMeasure',sr=>{assert(!measurement);const child=spawn(ffmpeg,['-v','info','-f','f32le','-ar',String(sr),'-ac','2','-i','pipe:0','-af','loudnorm=I=-23:TP=-1:LRA=7:print_format=json','-f','null','-'],{windowsHide:true});let stderr='';child.stderr.on('data',b=>stderr+=b.toString());const done=new Promise((resolve,reject)=>{child.on('error',reject);child.on('close',code=>{if(code)return reject(new Error(stderr.slice(-1500)));const raw=JSON.parse(stderr.slice(stderr.lastIndexOf('{'),stderr.lastIndexOf('}')+1));resolve({lufs:Number(raw.input_i),truePeak:Number(raw.input_tp)});});});measurement={child,done};});
await page.exposeFunction('__consumePCM',data=>new Promise((resolve,reject)=>{measurement.child.stdin.write(Buffer.from(data,'base64'),error=>error?reject(error):resolve());}));
await page.exposeFunction('__finishMeasure',async()=>{const m=measurement;m.child.stdin.end();const result=await m.done;measurement=null;return result;});
async function measure(name){await page.waitForTimeout(150);const r=await page.evaluate(()=>window.__measureConnectedGraph());observed.push({name,...r});await writeFile(path.join(root,'workbench-audio.json'),JSON.stringify(observed,null,2));console.log(name,JSON.stringify(r));return r;}
try{
 await page.goto(base+`/?project=${owner.project_id}#token=${token}`);
 await page.getByRole('button',{name:'打开工程',exact:true}).click({timeout:30000});
 await page.locator('.studio').waitFor();
 assert.equal(await page.locator('.section-lane,.transport-chord').count(),0);
 assert.equal(await page.locator('.track-card').count(),before.snapshot.tracks.length);
 for(let i=1;i<=before.snapshot.tracks.length;i++)assert.equal(await page.getByRole('spinbutton',{name:`音轨音量数值 ${i}`,exact:true}).inputValue(),'-12');
 if(await page.getByRole('button',{name:'循环',exact:true}).getAttribute('aria-pressed')!=='true')await page.getByRole('button',{name:'循环',exact:true}).click();
 await page.getByRole('button',{name:'播放',exact:true}).click({timeout:90000});
 await page.waitForFunction(()=>document.querySelector('.studio')?.dataset.playing==='true');
 await page.getByRole('button',{name:'原曲',exact:true}).click();
 await page.waitForTimeout(500);const reference=await measure('original');
 await page.getByRole('button',{name:/^重建/}).click();
 await page.waitForTimeout(700);const mix=await measure('mix');
 assert(mix.peak>0&&mix.clipped===0&&mix.truePeak<=0);assert(Math.abs(mix.lufs-reference.lufs)<2.5);
 for(let i=1;i<=before.snapshot.tracks.length;i++){
  await page.getByRole('button',{name:`独奏 ${i}`,exact:true}).click();
  const solo=await measure('solo-'+i);assert(solo.peak>0&&solo.clipped===0&&solo.truePeak<=0);assert(Math.abs(solo.lufs-reference.lufs)<3.5,JSON.stringify(solo));
  await page.getByRole('button',{name:`独奏 ${i}`,exact:true}).click();
 }
 await page.getByRole('button',{name:'独奏 1',exact:true}).click();
 await page.getByRole('spinbutton',{name:'音轨音量数值 1',exact:true}).fill('-18');
 const quieter=await measure('solo-1-minus6');assert(quieter.lufs<observed.find(x=>x.name==='solo-1').lufs-1);
 await page.getByRole('spinbutton',{name:'音轨音量数值 1',exact:true}).fill('-12');
 await page.getByRole('button',{name:'试听静音 1',exact:true}).click();
 const muted=await measure('muted');assert(muted.peak<1e-6);
 await page.getByRole('button',{name:'试听静音 1',exact:true}).click();await page.getByRole('button',{name:'独奏 1',exact:true}).click();
 await page.locator('.studio-song-title').click();await page.keyboard.press('Space');await page.waitForFunction(()=>document.querySelector('.studio')?.dataset.playing==='false');
 await page.screenshot({path:path.join(root,'workbench-4k.png')});
 const after=await call('revisions.get',{project_id:owner.project_id});assert.equal(after.snapshot_sha256,before.snapshot_sha256);
 assert.equal((await call('jobs.list',{project_id:owner.project_id,limit:1000})).length,jobsBefore);
 assert.equal(errors.length,0,errors.join('\n'));
 await writeFile(path.join(root,'workbench-browser.json'),JSON.stringify({reference,mix,observed,errors,headUnchanged:true,jobsUnchanged:true,removedChordAndStructure:true},null,2));
}finally{measurement?.child.kill();await browser.close();}
