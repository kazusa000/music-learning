import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const source=await readFile(new URL('../../src/engine/projectLoading.ts',import.meta.url),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;
const {projectLoadProgress:progress,expectedPluginTracks,pluginLoadLabel}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
const target={project:'p',revision:'new',view:'arrangement'},names={a:'钢琴',b:'贝斯'};
const state={project_id:'p',revision_id:'new',view:'arrangement',status:'loading',applied_revision:'old',plugins:{a:{status:'ready'},b:{status:'loading',load_stage:2}}};
const read=(s=state,expected=['a','b'],err='')=>progress(s,target,names,expected,err);
assert.equal(read().percent,50);assert.match(read().detail,/贝斯 · 恢复音色/);
for(const stale of [{project_id:'other'},{revision_id:'old'},{view:'original'}]){
 const result=read({...state,...stale,status:'ready',applied_revision:'new'});
 assert.equal(result.phase,'preparing');assert.equal(result.percent,null);assert.equal(result.ready,0);
}
const nodesReady={...state,plugins:{a:{status:'ready'},b:{status:'ready'}}};
assert.equal(read(nodesReady).percent,99);assert.equal(read({...nodesReady,status:'ready'}).phase,'finishing');
assert.equal(read({...nodesReady,status:'ready',applied_revision:'new'}).percent,100);
const failure=read({...state,plugins:{...state.plugins,b:{status:'failed',error:'采样文件不可用'}}});
assert.equal(failure.phase,'error');assert.equal(failure.percent,50);assert.equal(failure.detail,'采样文件不可用');
assert.equal(read({status:'stopped'},[],'本机连接中断').phase,'error');
// Runtime graph owns the count: empty/frozen/bypassed nodes are not permanently pending.
const audio={...nodesReady,plugins:{},status:'ready',applied_revision:'new'};
assert.equal(read(audio).total,0);assert.equal(read(audio).percent,100);
assert.equal(read({...audio,status:'loading',applied_revision:null},[]).percent,null);
assert.equal(read({...state,plugins:{...state.plugins,bus:{status:'loading'}}}).total,3);
assert.deepEqual(expectedPluginTracks({snapshot:{notes:[{track_id:'a'},{track_id:'b'},{track_id:'f'}],clips:[{track_id:'c'}],
 plugins:[{track_id:'a'},{track_id:'a'},{track_id:'b',bypass:true},{track_id:'c'},{track_id:'f'},{track_id:'empty'}],frozen_tracks:{f:{}}}}),['a','c']);
assert.equal(pluginLoadLabel(0),'启动音源');assert.equal(pluginLoadLabel(2),'恢复音色');assert.equal(pluginLoadLabel(99),'准备播放');
console.log('Target identity, actual node progress, revision handoff, failure, empty/frozen and source audio passed');
