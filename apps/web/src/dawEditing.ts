import type {ApplyEdit, Beat, Clip, NoteEvent, Snapshot, TimingMap, Track} from '../../../packages/contracts/types';
export type EditOperation=ApplyEdit['operations'][number];

export const uuid=()=>crypto.randomUUID().replaceAll('-','');
export function trackName(track:Pick<Track,'name'> & Partial<Track>){
  if(!/^midi-\d+-\d+-\d+$/.test(track.name))return track.name;
  if(track.role==='drums')return '鼓组';
  const gm=track.instrument_candidates?.find(c=>/^GM:\d+$/.test(c));
  return ({'GM:0':'钢琴','GM:4':'电钢琴','GM:24':'原声吉他','GM:25':'原声吉他','GM:29':'电吉他','GM:33':'指奏贝斯','GM:40':'小提琴','GM:42':'大提琴','GM:48':'弦乐合奏','GM:52':'合唱声部'} as Record<string,string>)[gm??'']??'乐器声部';
}
const value=(b:Beat)=>b.n/(b.d??1);
function interpolate(n:number,x:number[],y:number[]){
  if(n<x[0]||n>x.at(-1)!)throw new Error('位置超出已测定的节拍映射。');
  let i=0;while(i<x.length-2&&x[i+1]<=n)i++;
  return y[i]+(n-x[i])*(y[i+1]-y[i])/(x[i+1]-x[i]);
}
export const sampleBeat=(n:number,map:TimingMap)=>interpolate(n,map.anchors.map(a=>a.sample),map.anchors.map(a=>value(a.beat)));
export const beatSample=(n:number,map:TimingMap)=>Math.round(interpolate(n,map.anchors.map(a=>value(a.beat)),map.anchors.map(a=>a.sample)));
export function snapSample(n:number,s:Snapshot,division:number){
  const end=s.duration_samples??0,clamped=Math.min(end,Math.max(0,n));
  if(!division)return Math.round(clamped);
  const map=s.timing_map;
  if(!map)return Math.min(end,Math.max(0,Math.round(Math.round(clamped/(s.sample_rate??44100)/division)*division*(s.sample_rate??44100))));
  const beat=Math.round(sampleBeat(clamped,map)/division)*division;
  return Math.min(end,Math.max(0,beatSample(Math.min(value(map.anchors.at(-1)!.beat),Math.max(value(map.anchors[0].beat),beat)),map)));
}
export function gridLines(s:Snapshot,start:number,end:number,division:number){
  const map=s.timing_map,rate=s.sample_rate??44100;
  if(!map){const step=Math.max(.25,division||.25,2**Math.ceil(Math.log2((end-start)/rate/150)));const lines=[];
    for(let second=Math.ceil(start/rate/step)*step;second<=end/rate;second+=step)lines.push({sample:Math.round(second*rate),major:Number.isInteger(second),label:second.toFixed(1)+'s'});return lines;}
  const a=sampleBeat(start,map),b=sampleBeat(end,map);let step=division||.25;
  while((b-a)/step>350)step*=2;
  const lines=[];
  for(let beat=Math.ceil(a/step)*step;beat<=b;beat+=step){
    const meter=map.meters.filter(m=>value(m.beat)<=beat).at(-1)??map.meters[0];
    const bars=(beat-value(meter.beat))/(meter.numerator*4/meter.denominator);
    const major=beat>=0&&Math.abs(bars-Math.round(bars))<1e-7;
    lines.push({sample:beatSample(beat,map),major,label:major?String(meter.bar+Math.round(bars)):beat<0?'弱起':''});
  }return lines;
}
export function userNote(note:NoteEvent,revisionId:string,patch:Partial<NoteEvent>):NoteEvent{
  const changed={...note,...patch,provenance:{origin:'user' as const,source_id:'ui:'+revisionId,review:'unreviewed' as const}};
  if(changed.pitch!==note.pitch)changed.spelling=null;
  if(changed.start_sample!==note.start_sample||changed.end_sample!==note.end_sample){
    // Direct editing explicitly anchors to performed source time. Old notation stays in history.
    changed.timing_mode='sample';changed.notation_beat=null;changed.notation_duration=null;changed.timing_map_id=null;
  }return changed;
}
export const putNote=(note:NoteEvent):EditOperation=>({op:'put',collection:'notes',entity:note});
export async function arrangeClipEdits(s:Snapshot,clip:Clip,at:number,revision:string,duplicate=false):Promise<EditOperation[]>{
  const id=duplicate?uuid():clip.id,delta=at-clip.start_sample;
  const ops:EditOperation[]=[{op:'arrange_clip',id:clip.id,action:duplicate?'duplicate':'move',sample:at,...(duplicate?{new_id:id}:{})}];
  // Keep the service's clip validation and exact duplicate ids. Explicit time edits
  // anchor the changed notes to samples, including previously beat-bound notes.
  for(const note of s.notes??[])if(note.clip_id===clip.id){
    let noteId=note.id;
    if(duplicate){const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(id+note.id));noteId=Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('').slice(0,32);}
    ops.push(putNote({...userNote(note,revision,{start_sample:note.start_sample+delta,end_sample:note.end_sample+delta}),id:noteId,clip_id:id}));
  }
  ops.push({op:'put',collection:'clips',entity:{...clip,id,start_sample:at,provenance:{origin:'user',source_id:'ui:'+revision,review:'unreviewed'}}});
  return ops;
}
export function removeTrack(s:Snapshot,id:string):EditOperation[]{
  const plugins=new Set((s.plugins??[]).filter(p=>p.track_id===id).map(p=>p.id));
  const removedNodes=new Set([id,...plugins]);
  const ops:EditOperation[]=[];
  for(const collection of ['notes','clips','controllers','plugins','automation'] as const)
    for(const item of s[collection]??[])if(item.track_id===id||('plugin_id' in item&&plugins.has(item.plugin_id??'')))ops.push({op:'delete',collection,id:item.id});
  ops.push({op:'delete',collection:'tracks',id});
  ops.push({op:'track_order',track_ids:orderedTracks(s).filter(t=>t.id!==id).map(t=>t.id)});
  if(s.routing)ops.push({op:'routing',routing:{...s.routing,nodes:(s.routing.nodes??[]).filter(n=>!removedNodes.has(n)),edges:(s.routing.edges??[]).filter(e=>!removedNodes.has(e.source)&&!removedNodes.has(e.target))}});
  return ops;
}
export function orderedTracks(s:Snapshot):Track[]{
  const order=s.track_order??[];return [...(s.tracks??[])].sort((a,b)=>{
    const ai=order.indexOf(a.id),bi=order.indexOf(b.id);return(ai<0?9999:ai)-(bi<0?9999:bi);
  });
}
