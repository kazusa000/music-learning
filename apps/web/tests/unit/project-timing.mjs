import assert from 'node:assert/strict';
import {projectTiming} from '../../src/projectTiming.ts';
import {gridLines} from '../../src/dawEditing.ts';
const snapshot={sample_rate:44100,duration_samples:264600,timing_map:{
 anchors:[{sample:0,beat:{n:0,d:1}},{sample:264600,beat:{n:12,d:1}}],
 meters:[{beat:{n:0,d:1},bar:1,numerator:6,denominator:8}]}};
assert.equal(projectTiming(snapshot).bpm,120);
assert.equal(projectTiming(snapshot).denominator,8);
assert.deepEqual(gridLines(snapshot,0,264600,.25).filter(l=>l.major).map(l=>l.sample),[0,66150,132300,198450,264600]);
const variable={...snapshot,timing_map:{...snapshot.timing_map,anchors:[{sample:0,beat:{n:0}},{sample:44100,beat:{n:2}},{sample:220500,beat:{n:6}}]}};
assert.equal(projectTiming(variable,0).bpm,120);assert.equal(projectTiming(variable,44100).bpm,60);assert.equal(projectTiming(variable,220500).bpm,60);
assert.equal(projectTiming({...snapshot,timing_map:null}),null);
console.log('Tempo display, variable segments and 6/8 bar grid passed');
