import type {Revision} from '../../../../packages/contracts/types';
import type {EngineState} from './NativeTransport';

export type LoadTarget={project:string;revision:string;view:'original'|'arrangement'};
export type LoadProgress={phase:'preparing'|'loading'|'finishing'|'ready'|'error';title:string;detail:string;
  retryable?:boolean;
  percent:number|null;ready:number;total:number;tracks:Array<{id:string;name:string;status:string;stage:string;error?:string}>};

export function pluginLoadLabel(stage=0){
  return ['启动音源','读取插件','恢复音色','准备播放','准备播放'][Math.max(0,Math.min(4,Math.floor(stage)||0))];
}

export function expectedPluginTracks(revision:Revision){
  const snapshot=revision.snapshot;
  const content=new Set([...(snapshot.notes??[]),...(snapshot.clips??[])].map(event=>event.track_id));
  return [...new Set((snapshot.plugins??[]).filter(plugin=>!plugin.bypass&&content.has(plugin.track_id)&&
    !snapshot.frozen_tracks?.[plugin.track_id]).map(plugin=>plugin.track_id))];
}

/** Percentages count ready native nodes, never elapsed time or estimated samples. */
export function projectLoadProgress(state:EngineState,target:LoadTarget,names:Record<string,string>,expected:string[],error=''):LoadProgress{
  const matching=state.project_id===target.project&&state.revision_id===target.revision&&state.view===target.view;
  const plugins=matching?state.plugins??{}:{};
  const observed=Object.keys(plugins),failed=observed.filter(id=>plugins[id].status==='failed'||plugins[id].status==='unassigned');
  const readyGraph=matching&&state.status==='ready'&&state.applied_revision===target.revision&&
    !failed.length&&observed.every(id=>plugins[id].status==='ready');
  const ids=readyGraph?observed:[...new Set([...expected,...observed])];
  const tracks=ids.map((id,index)=>({id,name:names[id]??`音源 ${index+1}`,status:plugins[id]?.status??'pending',
    stage:plugins[id]?.status==='ready'?'已就绪':plugins[id]?.status==='unassigned'?'未选择乐器':plugins[id]?.status==='failed'?'加载失败':
      plugins[id]?pluginLoadLabel(plugins[id].load_stage):'等待加载',error:plugins[id]?.error}));
  const ready=tracks.filter(track=>track.status==='ready').length,total=tracks.length;
  if(readyGraph)return {phase:'ready',title:'工程已就绪',detail:total?`${total} 个音源已就绪 · 可以播放`:'可以播放与编辑',percent:100,ready,total,tracks};
  const problem=error||(matching?state.control_error:'')||failed.map(id=>plugins[id].error).find(Boolean);
  if(!error&&!state.control_error&&matching&&state.playable_revision===target.revision&&failed.length&&
    failed.every(id=>plugins[id].status==='unassigned'))return {phase:'error',title:'部分 MIDI 音轨未选择乐器',
      detail:`${failed.map(id=>names[id]??'MIDI 音轨').join('、')}：请在音色栏选择乐器，其他已就绪音轨可以播放`,
      retryable:false,percent:total?Math.floor(ready/total*100):null,ready,total,tracks};
  if(error||failed.length||problem)return {phase:'error',title:failed.length?'部分音源加载失败':'工程加载未完成',
    detail:problem||'请展开查看音轨状态',percent:total?Math.min(99,Math.floor(ready/total*100)):null,ready,total,tracks};
  if(!matching)return {phase:'preparing',title:'正在准备工程',detail:'读取音轨与音源配置',percent:null,ready:0,total:expected.length,tracks};
  const loading=tracks.filter(track=>track.status!=='ready');
  if(loading.length)return {phase:'loading',title:'正在加载音源',
    detail:loading.slice(0,2).map(track=>`${track.name} · ${track.stage}`).join('；')+(loading.length>2?` 等 ${loading.length} 个`:''),
    percent:total?Math.min(99,Math.floor(ready/total*100)):null,ready,total,tracks};
  return {phase:'finishing',title:'正在准备播放',detail:target.view==='original'?'准备原曲音频与播放位置':'同步音轨与播放位置',
    percent:total?99:null,ready,total,tracks};
}
