import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';

let source=await readFile(new URL('./src/transport.ts',import.meta.url),'utf8');
for(const name of ['api','listeningLevel','liveMix'])source=source.replace(`'./${name}'`,JSON.stringify(new URL('./src/'+name+'.ts',import.meta.url).href));
const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;
const {Transport}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
const starts=[];
const param=()=>({cancelScheduledValues(){},setTargetAtTime(){}});
const node=()=>({connect(){},disconnect(){},gain:param()});
const context={state:'running',currentTime:0,destination:node(),createGain:node,createChannelSplitter:node,createChannelMerger:node,
  createAnalyser:()=>({...node(),fftSize:512,getFloatTimeDomainData(values){values.fill(.1);}}),
  createBufferSource:()=>({...node(),start(when,offset=0){starts.push({when,offset,now:context.currentTime});},stop(){}})};
const transport=new Transport();transport.context=context;transport.matched=false;
const live={session_id:'a',generation:1,sample_rate:44100,duration_samples:44100*305,status:'ready'};
transport.configureChannels([{id:'a',assetId:'live:a:1',bakedGain:0,bakedPan:0,values:{},live}]);
let reads=0,late=false;
transport.liveBuffer=async(_live,_sample,frames)=>{reads++;if(late)context.currentTime+=.05;await Promise.resolve();return {duration:frames/44100,length:frames};};
const loop={enabled:false,start:0,end:305};
await transport.play(['track:a'],0,loop);await new Promise(setImmediate);
assert.equal(transport.playing,true);assert.ok(starts.length>0);
context.currentTime=.01;transport.pause();transport.pause();await new Promise(setImmediate);
const pausedReads=reads,offset=transport.offset;
const cached=transport.primes.size;assert.ok(cached>0,'a repeated pause must retain the ready first block');
await transport.play(['track:a'],offset,loop);await new Promise(setImmediate);
assert.equal(reads,pausedReads+1,'resume consumes the cached first block; only the next block is fetched');
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
console.log(JSON.stringify({status:'succeeded',coverage:'cached-resume/double-pause/slow-PCM-start/shared-clock/no-silent-retry-loop',reads,starts:starts.length}));
