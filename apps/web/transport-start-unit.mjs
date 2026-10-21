import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';

let source=await readFile(new URL('./src/transport.ts',import.meta.url),'utf8');
for(const name of ['api','listeningCalibration','listeningLevel','liveMix'])source=source.replace(`'./${name}'`,JSON.stringify(new URL('./src/'+name+'.ts',import.meta.url).href));
const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;
const {Transport}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
const starts=[];
const param=()=>({cancelScheduledValues(){},setTargetAtTime(){}});
const node=()=>({connect(){},disconnect(){},gain:param()});
const compressor=()=>({...node(),threshold:{},knee:{},ratio:{},attack:{},release:{}});
const context={createDynamicsCompressor:compressor,createWaveShaper:node,state:'running',currentTime:0,destination:node(),createGain:node,createChannelSplitter:node,createChannelMerger:node,
  createAnalyser:()=>({...node(),fftSize:512,getFloatTimeDomainData(values){values.fill(.1);}}),
  createBufferSource:()=>({...node(),start(when,offset=0){starts.push({when,offset,now:context.currentTime});},stop(){}})};
const transport=new Transport();transport.context=context;transport.matched=false;
const live={session_id:'a',generation:1,sample_rate:44100,duration_samples:44100*305,status:'ready'};
transport.configureChannels([{id:'a',assetId:'live:a:1',bakedGain:0,bakedPan:0,values:{},live}]);
let reads=0,late=false;const readSamples=[];
transport.liveBuffer=async(_live,sample,frames)=>{reads++;readSamples.push(sample);if(late)context.currentTime+=.05;await Promise.resolve();return {duration:frames/44100,length:frames};};
const loop={enabled:false,start:0,end:305};
await transport.play(['track:a'],0,loop);await new Promise(setImmediate);
assert.equal(transport.playing,true);assert.ok(starts.length>0);
context.currentTime=.01;transport.pause();transport.pause();await new Promise(setImmediate);
const pausedReads=reads,offset=transport.offset;
const cached=transport.primes.size;assert.ok(cached>0,'a repeated pause must retain the ready first block');
await transport.play(['track:a'],offset,loop);await new Promise(setImmediate);
assert.ok(!readSamples.slice(pausedReads).includes(Math.round(offset*44100)),'resume consumes the cached first block; only future blocks are fetched');
transport.pause();await new Promise(setImmediate);
// >22ms backend replies used to be discarded forever. Keep the remaining PCM,
// advance through late blocks and preserve the shared clock.
late=true;context.currentTime+=1;
await transport.play(['track:a'],290,loop);await new Promise(setImmediate);
assert.ok(starts.slice(-2).every(s=>s.when>=s.now));
assert.equal(Object.keys(transport.liveErrors).length,0);transport.pause();
await new Promise(setImmediate);
const cancel=new Transport();cancel.context=context;cancel.matched=false;
cancel.configureChannels([{id:'a',assetId:'live:a:1',bakedGain:0,bakedPan:0,values:{},live}]);
let release;cancel.liveBuffer=()=>new Promise(resolve=>{release=resolve;});
const pending=cancel.play(['track:a'],0,loop);
await new Promise(setImmediate);cancel.pause();
release({duration:4096/44100,length:4096});await pending;
assert.equal(cancel.playing,false,'a late first block cannot restart a cancelled play');
// The audio-thread notification, rather than a window timer, refills an active
// stream. The browser test separately loads the real worklet module.
const clocked=new Transport();clocked.context=context;clocked.matched=false;clocked.clock={};
clocked.configureChannels([{id:'a',assetId:'live:a:1',bakedGain:0,bakedPan:0,values:{},live}]);
let clockReads=0;clocked.liveBuffer=async(_live,_sample,frames)=>{clockReads++;return {duration:frames/44100,length:frames};};
await clocked.play(['track:a'],1,loop);await new Promise(setImmediate);
const tickReads=clockReads;context.currentTime+=.15;
clocked.streams.get('track:a').tick();await new Promise(setImmediate);
assert.ok(clockReads>tickReads);assert.equal(clocked.streams.get('track:a').timer,undefined);
clocked.pause();await new Promise(setImmediate);
// A half-second UI stall must not exhaust the bounded supply of future PCM.
late=false;
for(const ahead of [.24,1]){
  const stalled=new Transport();stalled.context=context;stalled.matched=false;stalled.clock={};stalled.lookaheadSeconds=ahead;
  stalled.configureChannels([{id:'a',assetId:'live:a:1',bakedGain:0,bakedPan:0,values:{},live}]);
  stalled.liveBuffer=async(_live,_sample,frames)=>({duration:frames/44100,length:frames});
  await stalled.play(['track:a'],5,loop);await new Promise(setImmediate);
  context.currentTime+=.5;stalled.streams.get('track:a').tick();await new Promise(setImmediate);
  const stream=stalled.streams.get('track:a');
  if(ahead===1)assert.equal(stream.underruns,0,'background supply absorbs a 500 ms UI stall');
  else assert.ok(stream.underruns>0,'the old buffer reproduces the failure');
  stalled.pause();await new Promise(setImmediate);
}
console.log(JSON.stringify({status:'succeeded',coverage:'cached-resume/double-pause/slow-PCM-start/shared-clock/no-silent-retry-loop',reads,starts:starts.length}));
