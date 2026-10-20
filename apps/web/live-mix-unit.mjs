import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import ts from 'typescript';
import {channelGains} from './src/liveMix.ts';

const channel={id:'a',assetId:'shared',bakedGain:-12,bakedPan:0,values:{gain_db:-6,pan:0}};
assert.ok(Math.abs(channelGains(channel,false)[0]-10**(.3))<1e-10);
assert.deepEqual(channelGains({...channel,values:{mute:true}},false),[0,0]);
assert.deepEqual(channelGains(channel,true),[0,0]);
assert.deepEqual(channelGains({...channel,bakedPan:.5,values:{gain_db:-12,pan:0}},false),[2,1]);

// Exercise actual transport routing, independent voices for identical PCM, and
// source replacement with a deterministic Web Audio contract double.
let sourceCode=await readFile(new URL('./src/transport.ts',import.meta.url),'utf8');
for(const module of ['listeningLevel','liveMix'])sourceCode=sourceCode.replace(`'./${module}'`,JSON.stringify(pathToFileURL(path.resolve('apps/web/src/'+module+'.ts')).href));
const javascript=ts.transpileModule(sourceCode,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;
const {Transport}=await import('data:text/javascript;base64,'+Buffer.from(javascript).toString('base64'));
const starts=[];
const param=()=>({value:0,cancelScheduledValues(){},setTargetAtTime(value){this.value=value;}});
const node=()=>({connect(){},disconnect(){},gain:param()});
const context={state:'running',currentTime:0,destination:node(),resume:async()=>{},createGain:node,createChannelSplitter:node,createChannelMerger:node,
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
transport.pause();
console.log(JSON.stringify({status:'succeeded',coverage:'gain/balance/mute/solo/shared-PCM-independent-voices/no-restart/timbre-source-replacement'}));
