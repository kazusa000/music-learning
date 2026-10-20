import type {Job, Operation, PluginInstance, Revision} from '../../../packages/contracts/types';
import type {EditOperation} from './dawEditing';

export type PresetChoice={id:string;name:string;presetId:string;presetName:string};
export type SoundPhase='capture'|'apply'|'load'|'done'|'error';
export type SoundChange={trackId:string;trackName:string;presetId:string;presetName:string;phase:SoundPhase;completed:number;startedAt:number;message?:string;applied?:boolean};
type Call=<T>(operation:Operation,args?:object,revision?:string,write?:boolean)=>Promise<T>;
export function hasTrackContent(revision:Revision,trackId:string){
  return !!revision.snapshot.frozen_tracks?.[trackId]||
    (revision.snapshot.notes??[]).some(n=>n.track_id===trackId)||
    (revision.snapshot.clips??[]).some(c=>c.track_id===trackId);
}
const canonical=(v:unknown):unknown=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b)).map(([k,x])=>[k,canonical(x)])):v;
const same=(a:unknown,b:unknown)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));

export function presetOperations(revision:Revision,trackId:string,targetId:string|null,source:PluginInstance,jobId:string):EditOperation[]{
  const chain=(revision.snapshot.plugins??[]).filter(p=>p.track_id===trackId);
  const target=chain.find(p=>p.id===targetId);
  const operations:EditOperation[]=[];
  if(target){
    for(const lane of revision.snapshot.automation??[])if(lane.plugin_id===target.id&&
      (source.class_id!==target.class_id||!(lane.parameter_id in (source.parameters??{}))))operations.push({op:'delete',collection:'automation',id:lane.id});
    operations.push({op:'patch_plugin',id:target.id,preset_capture_job_id:jobId,bypass:false});
  }else{
    for(const plugin of chain)operations.push({op:'patch_plugin',id:plugin.id,order:plugin.order+1});
    operations.push({op:'put',collection:'plugins',entity:{...source,id:crypto.randomUUID().replaceAll('-',''),track_id:trackId,order:0,bypass:false}});
  }
  return operations;
}

export async function switchPreset(options:{
  api:Call;base:Revision;trackId:string;targetId:string|null;choice:PresetChoice;
  progress:(phase:Exclude<SoundPhase,'error'>)=>void;
  commit:(base:Revision,operations:EditOperation[],description:string)=>Promise<Revision>;
  load:(revision:Revision)=>Promise<void>;active?:()=>boolean;sleep?:(ms:number)=>Promise<void>;
}){
  const {api,base,trackId,targetId,choice,progress,commit,load}=options;
  if(targetId&&!base.snapshot.plugins?.some(p=>p.id===targetId&&p.track_id===trackId))throw new Error('原乐器已移除，请重新选择音色');
  const active=()=>{if(options.active&&!options.active())throw new DOMException('操作已离开当前工程','AbortError');};
  const sleep=options.sleep??(ms=>new Promise(resolve=>setTimeout(resolve,ms)));
  active();progress('capture');
  let job=await api<Job>('jobs.submit',{project_id:base.project_id,kind:'instrument.capture',payload:{preset_id:choice.presetId},max_attempts:1},base.id,true);
  while(job.status==='queued'||job.status==='running'||job.status==='cancel_requested'){
    active();await sleep(400);active();job=await api<Job>('jobs.get',{job_id:job.id});
  }
  active();
  if(job.status!=='succeeded')throw new Error(String(job.error?.message??job.error?.code??'音色读取失败'));
  const source=job.result?.plugin as PluginInstance|undefined;
  if(!source?.class_id||!source.state_asset_id||!source.descriptor_asset_id)throw new Error('音色未返回完整乐器状态');
  progress('apply');
  const project=await api<{current_revision:string}>('projects.get',{project_id:base.project_id});
  const latest=await api<Revision>('revisions.get',{project_id:base.project_id,revision_id:project.current_revision});
  active();
  if(!latest.snapshot.tracks?.some(t=>t.id===trackId))throw new Error('这条音轨已被删除');
  const oldChain=(base.snapshot.plugins??[]).filter(p=>p.track_id===trackId);
  const newChain=(latest.snapshot.plugins??[]).filter(p=>p.track_id===trackId);
  if(targetId?!same(oldChain.find(p=>p.id===targetId),newChain.find(p=>p.id===targetId)):!same(oldChain,newChain))
    throw new Error('这条音轨的乐器已发生变化，请重新选择音色');
  const next=await commit(latest,presetOperations(latest,trackId,targetId,source,job.id),'更换音色：'+choice.presetName);
  active();progress('load');await load(next);active();progress('done');return next;
}
