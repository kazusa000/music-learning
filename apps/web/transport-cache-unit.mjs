import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import ts from 'typescript';

const baseline=process.argv.find(arg=>arg.startsWith('--baseline='))?.slice(11);
let source=baseline?execFileSync('git',['show',baseline+':apps/web/src/transport.ts'],{encoding:'utf8'}):await readFile(new URL('./src/transport.ts',import.meta.url),'utf8');
for(const name of ['api','listeningCalibration','listeningLevel','liveMix'])source=source.replace(`'./${name}'`,JSON.stringify(new URL('./src/'+name+'.ts',import.meta.url).href));
const {Transport}=await import('data:text/javascript;base64,'+Buffer.from(ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText).toString('base64'));
const starts=[];
const node=()=>({connect(){},disconnect(){},gain:{cancelScheduledValues(){},setTargetAtTime(){}}});
const context={state:'running',currentTime:0,baseLatency:0,getOutputTimestamp:()=>({}),destination:node(),
 createGain:node,createChannelSplitter:node,createChannelMerger:node,createWaveShaper:node,
 createDynamicsCompressor:()=>({...node(),threshold:{},knee:{},ratio:{},attack:{},release:{}}),
 createAnalyser:()=>({...node(),fftSize:512}),
 createBufferSource:()=>({...node(),start(){assert.ok(this.buffer);starts.push(this.buffer);},stop(){}}),
 // Metadata-only stand-ins exercise the real cache byte accounting without
 // allocating a gigabyte in the regression suite.
 decodeAudioData:async id=>({id,duration:397,length:44100*397,numberOfChannels:2})};
globalThis.fetch=async url=>({ok:true,arrayBuffer:async()=>url});
const t=new Transport();t.context=context;t.matched=false;
const channels=Array.from({length:8},(_,i)=>({id:String(i),assetId:'asset'+i,bakedGain:0,bakedPan:0,values:{}}));
const ids=channels.map(c=>'track:'+c.id),loop={enabled:false,start:0,end:397};
t.configureChannels(channels);
await t.play(ids,2,loop);
assert.equal(t.voices.size,8);assert.equal(t.duration,397);
assert.ok(t.buffers.size<16,'real 1 GiB eviction must have occurred');
assert.equal(new Set(starts).size,8,'all evicted and retained stems start with their decoded audio');
const before=starts.length;t.configureChannels(channels.map((c,i)=>({...c,values:{gain_db:-6,mute:i===1,solo:i===0}})));
await t.play(ids,t.position,loop,true);assert.equal(starts.length,before,'monitor controls reuse voices');
t.pause();await t.play(ids,20,loop);assert.equal(t.voices.size,8);
// Preload/cancellation can evict every cached entry between decode and start.
t.pause();t.primeLive=async()=>{await t.warm(['background-source']);return [];};
try{await t.play(ids,30,loop);}catch(error){
 if(!baseline)throw error;
 assert.match(error.message,/Cannot read properties of undefined \(reading 'duration'\)/);
 console.log(JSON.stringify({status:'reproduced',baseline,error:error.message}));process.exit(0);
}
assert.ok(!baseline,'the selected baseline no longer reproduces the defect');
assert.equal(t.voices.size,8);
// Preserved playback takes the same ownership path when replacing an asset.
t.configureChannels(channels.map((c,i)=>({...c,assetId:i===0?'replacement':c.assetId})));
const originalBuffer=t.buffer.bind(t);t.buffer=async id=>{const buffer=await originalBuffer(id);t.buffers.clear();return buffer;};
await t.play(ids,t.position,loop,true);assert.equal(t.voices.get(ids[0]).source.buffer.id,'/v1/assets/replacement');
t.pause();let release;t.buffer=()=>new Promise(resolve=>{release=resolve;});
const pending=t.play([ids[0]],0,loop);await Promise.resolve();t.pause();release({duration:397});await pending;
assert.equal(t.playing,false,'late decode cannot restart a paused transaction');
console.log(JSON.stringify({status:'succeeded',coverage:'397s/8-stems/1GiB-eviction/preload-eviction/monitor-reuse/resume/preserved-asset-switch/cancellation'}));
