import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
let source=await readFile(new URL('./src/engine/NativeTransport.ts',import.meta.url),'utf8');
source=source.replace("import {call} from '../api';","const call=()=>{throw new Error('Inject a test boundary');};");
const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;
const {NativeTransport}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
const operations=[];
let state={status:'stopped'},inflight=0;
const api=async(operation,args={})=>{
  assert.equal(inflight++,0,'Runtime writes must serialize');
  operations.push({operation,args});await new Promise(setImmediate);
  if(operation==='engine.open')state={...args,status:'ready',engine_session:'a'.repeat(48),generation:(state.generation??0)+1,
    duration_samples:44100*305,source_sample_rate:44100,monitor:{a:{gain_db:-12}},plugins:{a:{status:'ready'}},loop:false,loop_start_sample:0,loop_end_sample:44100*305};
  if(operation==='engine.update')state={...state,revision_id:args.revision_id,generation:state.generation+1};
  if(operation==='engine.claim')state={...state,owner_id:args.owner_id};
  if(operation==='engine.monitor'){assert.equal(args.generation,state.generation);state.monitor={...state.monitor,...args.tracks};}
  if(operation==='engine.transport'){assert.equal(args.generation,state.generation);state={...state,...args};}
  if(operation==='engine.close'){assert.equal(args.owner_id,state.owner_id);state={status:'stopped'};}
  inflight--;return structuredClone(state);
};
const transport=new NativeTransport(api),target={project:'p',revision:'r1',view:'arrangement'};
await transport.open(target);
await transport.apply(target,{playing:true,loop:false,start:0,end:44100*305});
assert.equal(operations.filter(x=>x.operation==='engine.open').length,1);
assert.equal(operations.findLast(x=>x.operation==='engine.transport').args.loop,undefined,'Unchanged loops must not rebuild the graph');
await transport.open({...target,revision:'r2'});
assert.equal(operations.filter(x=>x.operation==='engine.update').length,1);
await Promise.all([transport.monitor({a:{gain_db:-9,name:'not a runtime field'}}),transport.monitor({a:{mute:true}})]);
assert(!('name' in operations.find(x=>x.operation==='engine.monitor').args.tracks.a));
assert.equal(transport.playing,true);
// A late unmount must not close a different controller's output.
state.owner_id='another';
await transport.close();
assert(!operations.some(x=>x.operation==='engine.close'));
assert(!operations.some(x=>x.operation.includes('chunk')||x.operation==='playback.get'));
console.log('Native control serialization, revision update, loop preservation, monitor fields and stale-owner close passed');
