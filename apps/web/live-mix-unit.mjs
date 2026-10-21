import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
import {channelGains} from './src/liveMix.ts';
import {listeningGain} from './src/listeningLevel.ts';

const channel={id:'a',assetId:'shared',bakedGain:-12,bakedPan:0,values:{gain_db:-6,pan:0}};
assert.ok(Math.abs(channelGains(channel,false)[0]-10**(.3))<1e-10);
assert.deepEqual(channelGains({...channel,values:{mute:true}},false),[0,0]);
assert.deepEqual(channelGains(channel,true),[0,0]);
assert.deepEqual(channelGains({...channel,bakedPan:.5,values:{gain_db:-12,pan:0}},false),[2,1]);

// Exercise actual transport routing, independent voices for identical PCM, and
// source replacement with a deterministic Web Audio contract double.
let sourceCode=await readFile(new URL('./src/transport.ts',import.meta.url),'utf8');
for(const module of ['listeningCalibration','listeningLevel','liveMix','api'])sourceCode=sourceCode.replace(`'./${module}'`,JSON.stringify(new URL('./src/'+module+'.ts',import.meta.url).href));
const javascript=ts.transpileModule(sourceCode,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;
const {Transport}=await import('data:text/javascript;base64,'+Buffer.from(javascript).toString('base64'));
const starts=[];
const param=()=>({value:0,cancelScheduledValues(){},setTargetAtTime(value){this.value=value;}});
const node=()=>({connect(){},disconnect(){},gain:param()});
let resumes=0;
const compressor=()=>({...node(),threshold:{},knee:{},ratio:{},attack:{},release:{}});
const context={createDynamicsCompressor:compressor,createWaveShaper:node,state:'running',currentTime:0,destination:node(),resume:async()=>{resumes++;},createGain:node,createChannelSplitter:node,createChannelMerger:node,
  createAnalyser:()=>({...node(),fftSize:512,getFloatTimeDomainData(values){values.fill(0);}}),
  createBufferSource:()=>({...node(),start(when,offset){starts.push({when,offset});},stop(){}})};
const transport=new Transport();transport.context=context;transport.matched=false;
const buffer={numberOfChannels:2,length:48000,duration:2};transport.buffers.set('shared',buffer);transport.levels.set('shared',{rms:.02,peak:.04});
transport.configureChannels([channel,{...channel,id:'b',values:{gain_db:-12}}]);
await transport.play(['track:a','track:b'],.5,{enabled:true,start:0,end:2});
assert.equal(starts.length,2);assert.equal(transport.voices.size,2);
assert.equal(transport.voices.get('track:a').gain.gain.value,1);
assert.ok(transport.voices.get('track:a').left.gain.value>1.99);
assert.equal(transport.voices.get('track:b').left.gain.value,1);
transport.configureChannels([{...channel,values:{gain_db:0}},{...channel,id:'b',values:{gain_db:-12,mute:true}}]);
assert.equal(starts.length,2);assert.equal(transport.voices.get('track:b').left.gain.value,0);
assert.ok(transport.voices.get('track:a').left.gain.value>3.98);
await transport.play(['track:a','track:b'],0,{enabled:true,start:0,end:2},true);
assert.equal(starts.length,2);assert.equal(transport.schedule.length,1);
transport.buffers.set('changed',buffer);transport.levels.set('changed',{rms:.02,peak:.04});
transport.configureChannels([{...channel,assetId:'changed'},{...channel,id:'b'}]);
await transport.play(['track:a','track:b'],0,{enabled:true,start:0,end:2},true);
assert.equal(starts.length,3);assert.equal(transport.voices.get('track:a').assetId,'changed');
assert.equal(transport.schedule.length,1);
const beforeMixerChanges=starts.length;
transport.configureChannels([{...channel,values:{gain_db:-12,solo:true}},{...channel,id:'b',values:{gain_db:-12}}]);
assert.equal(transport.voices.get('track:b').left.gain.value,0);
transport.configureChannels([{...channel,values:{gain_db:-12,solo:true}},{...channel,id:'b',values:{gain_db:-12,solo:true}}]);
assert.equal(transport.voices.get('track:a').left.gain.value,1);
assert.equal(transport.voices.get('track:b').left.gain.value,1);
transport.configureChannels([{...channel,values:{gain_db:-12,solo:true,mute:true}},{...channel,id:'b',values:{gain_db:-12}}]);
assert.equal(transport.voices.get('track:a').left.gain.value,0);
assert.equal(transport.voices.get('track:b').left.gain.value,0);
transport.configureChannels([{...channel,values:{gain_db:-12}},{...channel,id:'b',values:{gain_db:-12,pan:1}}]);
assert.equal(transport.voices.get('track:b').left.gain.value,0);
assert.equal(transport.voices.get('track:b').right.gain.value,1);
assert.equal(transport.voices.get('track:a').left.gain.value,1);
assert.equal(starts.length,beforeMixerChanges);
transport.pause();
assert.equal(resumes,0,'already-running contexts must not resume again');
// Background refresh retains the actual decoded asset as well as its alias.
await transport.warm(['track:a','track:b']);
assert.ok(transport.buffers.has('shared'));
const beforeFast=starts.length;
await transport.play(['track:a','track:b'],0,{enabled:true,start:0,end:2});
const decode=transport.buffer.bind(transport);
transport.buffer=async()=>{throw new Error('mixer route unexpectedly waited for decode');};
await transport.play(['track:a','track:b'],0,{enabled:true,start:0,end:2},true);
assert.equal(starts.length,beforeFast+2);transport.pause();
transport.buffer=decode;
const pausedStarts=starts.length;
transport.configureChannels([{...channel,values:{gain_db:-6,mute:true}},{...channel,id:'b',values:{gain_db:-12,solo:true}}]);
assert.equal(starts.length,pausedStarts);
await transport.play(['track:a','track:b'],.5,{enabled:false,start:0,end:2});
assert.equal(starts.length,pausedStarts+2);
assert.equal(transport.voices.get('track:a').left.gain.value,0);
assert.equal(transport.voices.get('track:b').left.gain.value,1);
transport.pause();
// Cold first play and replay must use the same full-mix reference. Independent
// per-stem loudness matching would destroy the balance before preload finishes.
const cold=new Transport();cold.context=context;
cold.configureChannels([{...channel,values:{gain_db:-12}},{...channel,id:'b',values:{gain_db:-12}}]);
cold.family('mix',['track:a','track:b']);
const loaded=[];const mixLevel={rms:.08,peak:.2};
cold.buffer=async id=>{loaded.push(id);cold.buffers.set(id,buffer);cold.levels.set(id,id==='mix'?mixLevel:{rms:id==='track:a'?.01:.001,peak:.04});return buffer;};
await cold.play(['track:a','track:b'],0,{enabled:false,start:0,end:2});
assert.ok(loaded.includes('mix'));
assert.equal(cold.voices.get('track:a').gain.gain.value,listeningGain(mixLevel));
assert.equal(cold.voices.get('track:b').gain.gain.value,listeningGain(mixLevel));
const firstGain=cold.voices.get('track:b').gain.gain.value;cold.pause();
await cold.play(['track:a','track:b'],0,{enabled:false,start:0,end:2});
assert.equal(cold.voices.get('track:b').gain.gain.value,firstGain);cold.pause();
console.log(JSON.stringify({status:'succeeded',coverage:'gain/balance/mute/solo/shared-PCM-independent-voices/no-restart/timbre-source-replacement'}));
