import assert from 'node:assert/strict';
import {arrangeClipEdits,snapSample,sampleBeat,beatSample,gridLines,trackName,userNote,removeTrack,orderedTracks} from './src/dawEditing.ts';
import {result} from './src/api.ts';
const provenance={origin:'model',source_id:'fixture'};
assert.equal(trackName({name:'midi-1-9-0',role:'drums',instrument_candidates:['GM:0']}),'鼓组');
assert.equal(trackName({name:'midi-6-4-0',instrument_candidates:['GM:0']}),'钢琴');
assert.equal(trackName({name:'我的旋律',instrument_candidates:['GM:29']}),'我的旋律');
const timing={id:'a'.repeat(32),sample_rate:10000,pickup:{n:1,d:1},provenance,
  anchors:[{sample:0,beat:{n:-1,d:1}},{sample:10000,beat:{n:1,d:1}},{sample:30000,beat:{n:3,d:1}}],
  meters:[{beat:{n:0,d:1},bar:1,numerator:3,denominator:4}]};
const snapshot={sample_rate:10000,duration_samples:30000,timing_map:timing};
assert.equal(sampleBeat(5000,timing),0);assert.equal(beatSample(2,timing),20000);
assert.equal(snapSample(16400,snapshot,.5),15000);
assert.equal(snapSample(2800,snapshot,.25),2500);
assert.equal(snapSample(-12,snapshot,.25),0);
assert.equal(snapSample(42000,snapshot,.25),30000);
assert.equal(snapSample(16400,{...snapshot,timing_map:null},.25),17500);
const bars=gridLines(snapshot,0,30000,.25).filter(l=>l.major);
assert.deepEqual(bars.map(b=>[b.sample,b.label]),[[5000,'1'],[30000,'2']]);
assert.ok(gridLines(snapshot,1,29999,.001).length<355);
const note={id:'n',track_id:'t',pitch:60,spelling:'C4',start_sample:5000,end_sample:10000,
  timing_mode:'beat',timing_map_id:timing.id,notation_beat:{n:0,d:1},notation_duration:{n:1,d:1},provenance,velocity:.5};
const pitched=userNote(note,'revision',{pitch:62});assert.equal(pitched.timing_mode,'beat');assert.equal(pitched.spelling,null);
const shifted=userNote(note,'revision',{start_sample:6000,end_sample:11000});
assert.equal(shifted.timing_mode,'sample');assert.equal(shifted.timing_map_id,null);assert.equal(shifted.notation_beat,null);
assert.equal(note.start_sample,5000);assert.equal(shifted.provenance.origin,'user');
const s={tracks:[{id:'t'},{id:'other'}],track_order:['other','t'],notes:[note],clips:[{id:'clip',track_id:'t'}],
  controllers:[{id:'cc',track_id:'t'}],plugins:[{id:'p',track_id:'t'}],automation:[{id:'a',plugin_id:'p'},{id:'b',track_id:'other'}],
  routing:{id:'r',nodes:['t','p','other'],edges:[{source:'t',target:'other'},{source:'p',target:'other'}]}};
assert.deepEqual(orderedTracks(s).map(t=>t.id),['other','t']);
const deleted=removeTrack(s,'t');assert.ok(deleted.some(op=>op.collection==='automation'&&op.id==='a'));
assert.ok(!deleted.some(op=>op.id==='b'));assert.deepEqual(deleted.at(-1).routing.nodes,['other']);
assert.deepEqual(deleted.at(-1).routing.edges,[]);
const clip={id:'clip',track_id:'t',start_sample:5000,duration_samples:10000,provenance};
const clipNotes={...snapshot,notes:[{...note,clip_id:'clip'}]};
const moved=await arrangeClipEdits(clipNotes,clip,10000,'revision');
assert.equal(moved[0].op,'arrange_clip');assert.equal(moved[1].entity.timing_mode,'sample');
assert.equal(moved[1].entity.start_sample,10000);assert.equal(moved[1].entity.notation_beat,null);
const cloned=await arrangeClipEdits(clipNotes,clip,15000,'revision',true);
assert.notEqual(cloned[1].entity.id,note.id);assert.equal(cloned[1].entity.clip_id,cloned[0].new_id);
assert.equal(note.timing_mode,'beat');assert.equal(clip.start_sample,5000);
await assert.rejects(result(new Response(JSON.stringify({ok:false,error:{message:'Contract validation failed',details:[{msg:'owned note lies outside its clip'}]}}),{status:422})),/owned note lies outside its clip/);
console.log(JSON.stringify({status:'succeeded',coverage:'timing/snap/notation/dependent-entity-removal'}));
