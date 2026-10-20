import assert from 'node:assert/strict';
import {boundAudition} from './src/auditionState.ts';
import {writeAudition} from './src/auditionSync.ts';

const failure=code=>Object.assign(new Error(code),{code});
let head='old',calls=[],attempts=0;
const invoke=async(op,args,expected,write)=>{
  calls.push({op,args,expected,write});
  if(op==='projects.get')return {current_revision:'new'};
  if(op==='revisions.get')return {snapshot:{duration_samples:1000}};
  if(attempts++===0)throw failure('revision_conflict');
  return {sequence:'s',desired:args.state};
};
const state={revision_id:'old',position_sample:500,mode:'mix',track_ids:[],playing:true,loop:true,loop_start_sample:100,loop_end_sample:700};
const cue=await writeAudition(invoke,'p','audition.set',{state},()=>head,id=>head=id,true);
assert.equal(head,'new');assert.equal(cue.desired.revision_id,'new');
assert.deepEqual(cue.desired,{...state,revision_id:'new'});assert.equal(state.revision_id,'old');
assert.equal(calls.at(-1).expected,'new');assert.ok(calls.at(-1).write);

// A historical target is intentional and must not be rebound to the head.
head='old';attempts=0;calls=[];
const historical=await writeAudition(invoke,'p','audition.set',{state:{...state,revision_id:'historical'}},()=>head,id=>head=id);
assert.equal(historical.desired.revision_id,'historical');assert.equal(calls.at(-1).expected,'new');

// A mixer save may supersede an observation after the cue itself succeeded.
assert.equal(await writeAudition(async()=>{throw failure('transport_superseded');},'p','audition.report',{sequence:'stale'},()=>head,()=>{}),null);
await assert.rejects(()=>writeAudition(async()=>{throw failure('transport_superseded');},'p','audition.set',{state},()=>head,()=>{}),{code:'transport_superseded'});
for(const code of ['network_error','invalid_arguments']){
  let count=0;
  await assert.rejects(()=>writeAudition(async()=>{count++;throw failure(code);},'p','audition.set',{state},()=>head,()=>{}),{code});
  assert.equal(count,1);
}
let count=0;
await assert.rejects(()=>writeAudition(async op=>{if(op==='projects.get')return {current_revision:'r'+count};if(op==='revisions.get')return {snapshot:{duration_samples:1000}};count++;throw failure('revision_conflict');},'p','audition.set',{state},()=>head,()=>{},true),{code:'revision_conflict'});
assert.equal(count,3);
console.log(JSON.stringify({status:'succeeded',coverage:'head-conflict-recovery/historical-target/sequence-fencing/no-network-replay/bounded-retries'}));

const tail=boundAudition({...state,position_sample:307*44100,loop_end_sample:305*44100},305*44100);
assert.equal(tail.position_sample,305*44100);
const collapsed=boundAudition({...state,loop_start_sample:1000,loop_end_sample:1100},300);
assert.equal(collapsed.loop,false);assert.equal(collapsed.loop_start_sample,0);assert.equal(collapsed.loop_end_sample,300);
let latest='old';let tries=0;
const rebased=await writeAudition(async(op,args)=>{
 if(op==='projects.get')return {current_revision:'shorter'};
 if(op==='revisions.get')return {snapshot:{duration_samples:300}};
 if(tries++===0)throw failure('revision_conflict');
 return args.state;
},'p','audition.set',{state},()=>latest,id=>latest=id,true);
assert.equal(rebased.position_sample,300);assert.equal(rebased.loop_end_sample,300);
assert.equal(state.position_sample,500); // caller state is immutable
