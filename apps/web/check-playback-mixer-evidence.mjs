// Check real-browser observations; this is not a browser automation driver.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const observations=JSON.parse(await readFile(process.argv[2],'utf8'));
const get=name=>{const row=observations.find(r=>r.name===name);assert.ok(row,'Missing observation: '+name);return row;};
const levels=row=>row.meters.map(m=>m.db);
const audible=(row,indices)=>indices.forEach(i=>assert.ok(levels(row)[i]>-59,`${row.name}: track ${i} silent`));
const silent=(row,indices)=>indices.forEach(i=>assert.equal(levels(row)[i],-60,`${row.name}: track ${i} not muted`));
for(const row of observations)assert.deepEqual(row.messages,[],row.name);
for(const name of ['31-cold-reference-level-fixed','32-replay-reference-level-fixed','33-final-live-volume-plus-six','34-mouse-and-keyboard-track-fader','39-final-clear-all','40-external-sdk-edit-playing','43-final-restore-all-after-pause','45-final-mixer-clears-filter']){
  const row=get(name);assert.equal(row.playing,'true',name);audible(row,[0,1,2]);
}
const cold=get('31-cold-reference-level-fixed'),replay=get('32-replay-reference-level-fixed');
assert.ok(Math.abs(levels(cold)[2]-levels(replay)[2])<.8,'Cold/replay level mismatch');
const delta=levels(get('33-final-live-volume-plus-six'))[2]-levels(cold)[2];
assert.ok(Math.abs(delta-6)<.8,'Fader failed to change signal by 6 dB');
for(const [name,on,off] of [
  ['35-final-live-mute-solo',[1],[0,2]],
  ['36-final-multi-solo',[0,1],[2]],
  ['37-final-mute-one-solo',[0],[1,2]],
  ['38-final-all-audible-solos-muted',[],[0,1,2]],
  ['42-final-paused-edits-resume',[2],[0,1]],
  ['44-final-explicit-tracks-filter',[1,2],[0]],
]){const row=get(name);assert.equal(row.playing,'true');audible(row,on);silent(row,off);}
for(const name of ['33-final-live-volume-plus-six','34-mouse-and-keyboard-track-fader','35-final-live-mute-solo','36-final-multi-solo','37-final-mute-one-solo','38-final-all-audible-solos-muted','39-final-clear-all','40-external-sdk-edit-playing'])assert.equal(get(name).starts,replay.starts,'Mixer restarted transport: '+name);
assert.equal(get('41-final-save-pending-pause').playing,'false');
assert.equal(get('44-final-explicit-tracks-filter').filtered,true);
assert.equal(get('45-final-mixer-clears-filter').filtered,false);
const end=get('47-final-ended'),quiet=get('48-final-remains-ended'),again=get('49-final-replay');
assert.equal(end.playing,'false');assert.equal(quiet.playing,'false');
assert.equal(end.starts,quiet.starts,'Polling restarted naturally ended playback');
assert.equal(end.voices,0);assert.equal(quiet.voices,0);
assert.equal(again.playing,'true');assert.ok(again.position<2,'Replay did not restart at beginning');
assert.equal(again.starts,quiet.starts+1);
const reopened=get('50-reopen-retains-pending-save');
assert.equal(reopened.playing,'true');audible(reopened,[0,1]);silent(reopened,[2]);
const original=get('51-final-original-mode'),reconstructed=get('52-final-reconstructed-mode');
assert.equal(original.playing,'true');assert.equal(reconstructed.playing,'true');
silent(original,[0,1,2]);audible(reconstructed,[0,1,2]);
assert.equal(original.starts,reconstructed.starts);
assert.equal(get('53-paused-before-service-restart').playing,'false');
const restarted=get('54-play-after-service-restart');assert.equal(restarted.playing,'true');audible(restarted,[0,1,2]);
assert.equal(get('55-final-paused').playing,'false');
console.log(JSON.stringify({status:'succeeded',observations:observations.length,signal_delta_db:delta,cold_replay_delta_db:levels(replay)[2]-levels(cold)[2],coverage:'connected-playback/mixing/mute/solo/pause/SDK/filter/end/replay'}));
