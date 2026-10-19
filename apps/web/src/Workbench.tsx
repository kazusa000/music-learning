import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import type {CSSProperties} from 'react';
import type {AuditionState, Citation, Job, PreprocessingResult, PreprocessingView, Revision} from '../../../packages/contracts/types';
import {Discussion} from './Discussion';
import type {InstrumentDraft} from './Discussion';
import {InstrumentBrowser} from './InstrumentBrowser';
import {DawEditor} from './DawEditor';
import type {EditRequest} from './DawEditor';
import {trackName as instrumentName} from './dawEditing';
import {call} from './api';
import {Transport} from './transport';
import './workbench.css';
import './daw.css';

const time=(s:number)=>`${Math.floor(s/60).toString().padStart(2,'0')}:${Math.floor(s%60).toString().padStart(2,'0')}`;
const colors=['#8ca0ff','#58cfbc','#e7ad6b','#d48bdd','#73bdeb','#d8ce7c','#ec8d9b'];
type Render=PreprocessingResult&{start_sample?:number;end_sample?:number};
type Cue={sequence:string|null;desired:AuditionState|null;observed:unknown};
type Version={id:string;description:string;created_at:number;parent_id:string|null};
type Difference=Record<string,{added:string[];removed:string[];changed:string[]}>;
type PluginInfo={name:string;manufacturer:string;parameters_readback:Array<{id:string;name:string;label:string;text:string}>};

export function Workbench({title,revision:head,publication:initial,jobs=[],onBack,onCommitted}:{
  title:string;revision:Revision;publication:PreprocessingResult|null;jobs?:Job[];onBack:()=>void;onCommitted?:()=>Promise<void>;
}){
  const [versionId,setVersionId]=useState('head');
  const [revision,setRevision]=useState(head);
  const [publication,setPublication]=useState<Render|null>(initial);
  const [versions,setVersions]=useState<Version[]>([]);
  const [selected,setSelected]=useState('all');
  const [inspector,setInspector]=useState('discussion');
  const [instrumentDraft,setInstrumentDraft]=useState<InstrumentDraft|null>(null);
  const consumeInstrumentDraft=useCallback(()=>setInstrumentDraft(null),[]);
  const pendingCitation=useRef<Citation|null>(null);
  const [mode,setMode]=useState('original');
  const [referenceId,setReferenceId]=useState<string|null>(null);
  const [hybridJobId,setHybridJobId]=useState<string|null>(null);
  const [trackIds,setTrackIds]=useState<string[]>([]);
  const [position,setPosition]=useState(0);
  const [playing,setPlaying]=useState(false);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  const [loop,setLoop]=useState(false);
  const [loopStart,setLoopStart]=useState(0);
  const [loopEnd,setLoopEnd]=useState(0);
  const [zoom,setZoom]=useState(1);
  const [viewStart,setViewStart]=useState(0);
  const [width,setWidth]=useState(900);
  const [wave,setWave]=useState<Array<Array<[number,number]>>>([]);
  const [difference,setDifference]=useState<Difference|null>(null);
  const [pluginInfo,setPluginInfo]=useState<Record<string,PluginInfo>>({});
  const [sourceIssues,setSourceIssues]=useState<string[]>([]);
  const [editingBusy,setEditingBusy]=useState(false),[undoStack,setUndoStack]=useState<string[]>([]),[redoStack,setRedoStack]=useState<string[]>([]);
  const editingLock=useRef(false),localCommit=useRef<string|null>(null),commitBefore=useRef<string|null>(null),historyHead=useRef<string|null>(null);
  const canvas=useRef<HTMLCanvasElement>(null);
  const player=useRef(new Transport());
  const lastCue=useRef<string|null>(null);
  const requestGeneration=useRef(0);
  const localCuePending=useRef(false);
  const applyingCue=useRef(false);
  const lastReportTime=useRef(0);
  const snapshot=revision.snapshot;
  const rate=snapshot.sample_rate??44100;
  const duration=(snapshot.duration_samples??0)/rate;
  const tracks=snapshot.tracks??[];
  const renderJob=jobs.filter(job=>job.kind==='project.render'&&job.revision_id===revision.id).at(-1);
  const renderBusy=!!renderJob&&['queued','running','cancel_requested'].includes(renderJob.status);
  const hybridJob=jobs.filter(job=>job.kind==='project.export'&&job.payload.format==='hybrid'&&job.status==='succeeded'&&job.revision_id===revision.id).at(-1);
  const notes=useMemo(()=>(snapshot.notes??[]).filter(n=>selected==='all'||n.track_id===selected),[snapshot,selected]);
  const sections=(snapshot.annotations??[]).filter(a=>a.kind==='section');
  const issues=[...(publication?.issues??[]),...sourceIssues];
  const sourceIds=useCallback(async(state:AuditionState,render:Render|null)=>{
    if(state.mode==='hybrid'){
      if(!state.hybrid_job_id)throw new Error('请选择已完成的贝斯混合试听。');
      const job=await call<Job>('jobs.get',{job_id:state.hybrid_job_id});
      if(job.project_id!==head.project_id||job.revision_id!==state.revision_id||job.status!=='succeeded'||job.payload.format!=='hybrid'||typeof job.result?.asset_id!=='string')throw new Error('此版本的贝斯混合尚未完成。');
      return[job.result.asset_id];
    }
    if(state.mode==='reference'){
      if(!state.reference_revision_id)throw new Error('请选择参考版本。');
      const args={project_id:head.project_id,revision_id:state.reference_revision_id};
      const [view,renders,reference]=await Promise.all([call<PreprocessingView>('preprocessing.get',args),call<Render[]>('renders.list',args),call<Revision>('revisions.get',args)]);
      const full=renders.filter(r=>(r.start_sample??0)===0&&(r.end_sample??reference.snapshot.duration_samples)===reference.snapshot.duration_samples).at(-1)??view.publication;
      if(!full)throw new Error('参考版本尚未完成渲染。');
      return [full.mix_asset_id];
    }
    if(state.mode==='original')return snapshot.source_asset_id?[snapshot.source_asset_id]:[];
    if(!render)throw new Error('此版本尚未完成渲染，请先生成试听。');
    if(state.mode==='mix')return[render.mix_asset_id];
    const ids=(state.track_ids??[]).map(id=>render.track_renders[id]).filter((id):id is string=>typeof id==='string');
    if(ids.length!==state.track_ids?.length)throw new Error('该声部暂无此版本的渲染。');
    return ids;
  },[snapshot,head.project_id]);

  useEffect(()=>{
    player.current=new Transport();const transport=player.current;let frame=0;
    const draw=()=>{setPosition(transport.position);setPlaying(transport.playing);
      if(transport.playing&&!transport.loop&&transport.position>=transport.duration)transport.pause();
      frame=requestAnimationFrame(draw);};
    frame=requestAnimationFrame(draw);
    return()=>{cancelAnimationFrame(frame);void transport.close();};
  },[head.project_id]);

  useEffect(()=>{
    let active=true;
    const update=async()=>{
      const id=versionId==='head'?head.id:versionId;
      const [read,preprocessed,renders,list]=await Promise.all([
        id===head.id?Promise.resolve(head):call<Revision>('revisions.get',{project_id:head.project_id,revision_id:id}),
        call<PreprocessingView>('preprocessing.get',{project_id:head.project_id,revision_id:id}),
        call<Render[]>('renders.list',{project_id:head.project_id,revision_id:id}),
        call<Version[]>('revisions.list',{project_id:head.project_id,limit:1000})]);
      if(!active)return;
      if(localCommit.current&&read.id!==localCommit.current&&head.id===commitBefore.current)return;
      const full=renders.filter(r=>(r.start_sample??0)===0&&(r.end_sample??read.snapshot.duration_samples)===read.snapshot.duration_samples);
      setRevision(read);setPublication(full.at(-1)??preprocessed.publication);setVersions(list.slice().reverse());
    };
    void update().catch(e=>{if(active)setError(e.message);});
    const timer=setInterval(()=>void update().catch(()=>{}),2500);
    return()=>{active=false;clearInterval(timer);};
  },[versionId,head]);

  useEffect(()=>{if(localCommit.current&&head.id!==commitBefore.current)localCommit.current=null;
    if(editingLock.current||localCommit.current)return;
    if(historyHead.current&&head.id!==historyHead.current){setUndoStack([]);setRedoStack([]);historyHead.current=null;}
  },[head.id,editingBusy]);
  async function acceptManualRevision(next:Revision){
    commitBefore.current=revision.id;localCommit.current=next.id;historyHead.current=next.id;player.current.pause();setPlaying(false);
    setRevision(next);setPublication(null);setVersionId('head');setError('已保存新版本；生成试听后可听到修改。');
    try{await onCommitted?.();}catch(e){setError('版本已保存，但刷新失败：'+(e as Error).message);}
  }
  async function manualEdit(request:EditRequest){
    if(editingLock.current)throw new Error('前一次编辑仍在保存。');
    if(revision.id!==head.id||versionId!=='head')throw new Error('历史版本只读，请切回当前版本。');
    editingLock.current=true;setEditingBusy(true);
    try{const next=await call<Revision>('edits.apply',{project_id:head.project_id,
      scope:{track_ids:request.trackIds,start_sample:0,end_sample:snapshot.duration_samples,structural:request.structural??false},
      operations:request.operations,author:'user',description:request.description},revision.id,true);
      setUndoStack(stack=>[...stack,revision.id]);setRedoStack([]);await acceptManualRevision(next);
    }finally{editingLock.current=false;setEditingBusy(false);}
  }
  async function manualRestore(redo=false){
    const stack=redo?redoStack:undoStack,target=stack.at(-1);if(!target)return;
    if(editingLock.current||revision.id!==head.id||versionId!=='head')throw new Error('请等待保存完成并切回当前版本。');
    editingLock.current=true;setEditingBusy(true);
    try{const next=await call<Revision>('revisions.restore',{project_id:head.project_id,revision_id:target,description:redo?'手动编辑重做':'手动编辑撤销'},revision.id,true);
      if(redo){setRedoStack(stack=>stack.slice(0,-1));setUndoStack(stack=>[...stack,revision.id]);}
      else{setUndoStack(stack=>stack.slice(0,-1));setRedoStack(stack=>[...stack,revision.id]);}
      await acceptManualRevision(next);
    }finally{editingLock.current=false;setEditingBusy(false);}
  }

  useEffect(()=>{
    player.current.pause();player.current.offset=0;setPosition(0);
    setLoopStart(previous=>Math.min(previous,Math.max(0,duration-1/rate)));
    setLoopEnd(previous=>previous>0?Math.min(previous,duration):duration);
    setMode(snapshot.source_asset_id?'original':'mix');setViewStart(0);setZoom(1);
  },[revision.id,duration,snapshot.source_asset_id]);

  useEffect(()=>{
    let active=true;setWave([]);
    const id=publication?.waveforms['waveforms/source.json']??publication?.waveforms['waveforms/mix.json'];
    if(id)void fetch('/v1/assets/'+id).then(r=>r.json()).then(data=>{
      if(active)setWave(data.levels.find((l:{buckets:unknown[]})=>l.buckets.length<3000)?.buckets??data.levels.at(-1).buckets);
    }).catch(()=>{});
    return()=>{active=false;};
  },[publication?.manifest_asset_id]);

  useEffect(()=>{
    let active=true;
    void Promise.all((snapshot.plugins??[]).map(async plugin=>{
      if(!plugin.descriptor_asset_id)return null;
      const raw=await (await fetch('/v1/assets/'+plugin.descriptor_asset_id)).json();
      return [plugin.id,raw.plugin??raw] as const;
    })).then(items=>{if(active)setPluginInfo(Object.fromEntries(items.filter((item):item is readonly [string,PluginInfo]=>item!==null)));}).catch(()=>{});
    return()=>{active=false;};
  },[revision.id]);

  useEffect(()=>{
    let active=true;setSourceIssues([]);
    const included=new Set(snapshot.analysis_run_ids??[]);
    void call<Array<{id:string;payload:{issues?:string[]}}>>('analysis.list',{project_id:head.project_id,limit:1000}).then(runs=>{
      if(active)setSourceIssues([...new Set(runs.filter(run=>included.has(run.id)).flatMap(run=>(run.payload.issues??[]).map(issue=>'原始分析 · '+issue)))]);
    }).catch(()=>{});
    return()=>{active=false;};
  },[revision.id,head.project_id]);

  const applyCue=useCallback(async(state:AuditionState,sequence:string|null,preserve=false)=>{
    if(state.revision_id!==revision.id){setVersionId(state.revision_id);return;}
    if(sequence)lastCue.current=sequence;
    setMode(state.mode??'original');setTrackIds(state.track_ids??[]);setLoop(state.loop??false);
    setReferenceId(state.reference_revision_id??null);
    setHybridJobId(state.hybrid_job_id??null);
    setLoopStart((state.loop_start_sample??0)/rate);setLoopEnd((state.loop_end_sample??snapshot.duration_samples??0)/rate);
    const offset=(state.position_sample??0)/rate;
    if(!preserve){player.current.pause();player.current.offset=offset;setPosition(offset);}
    if(state.playing){
      applyingCue.current=true;
      setLoading(true);
      try{await player.current.play(await sourceIds(state,publication),offset,{enabled:state.loop??false,start:(state.loop_start_sample??0)/rate,end:(state.loop_end_sample??snapshot.duration_samples??0)/rate},preserve);setError('');}
      catch(e){player.current.pause();setError((e as Error).message);}
      finally{applyingCue.current=false;setLoading(false);}
    }else{player.current.pause();player.current.offset=offset;setPosition(offset);}
  },[revision.id,rate,snapshot.duration_samples,publication,sourceIds]);

  useEffect(()=>{
    let active=true;
    const poll=async()=>{const cue=await call<Cue>('audition.get',{project_id:head.project_id});
      const changed=cue.sequence!==lastCue.current;
      if(active&&!localCuePending.current&&cue.sequence&&changed&&cue.desired)await applyCue(cue.desired,cue.sequence);
      if(active&&!localCuePending.current&&cue.sequence===lastCue.current&&cue.sequence&&cue.desired?.revision_id===revision.id&&(changed||Date.now()-lastReportTime.current>3000)){
        lastReportTime.current=Date.now();
        const status=applyingCue.current?'loading':player.current.playing?'playing':cue.desired.playing?(player.current.context?.state==='running'?'error':'needs_activation'):'paused';
        await call('audition.report',{project_id:head.project_id,sequence:cue.sequence,position_sample:Math.round(player.current.position*rate),status},head.id,true);
      }};
    void poll().catch(()=>{});const timer=setInterval(()=>void poll().catch(()=>{}),1500);
    return()=>{active=false;clearInterval(timer);};
  },[head.project_id,head.id,revision.id,rate,applyCue]);

  function desired(patch:Partial<AuditionState>={}):AuditionState{
    return{revision_id:revision.id,position_sample:Math.round(Math.min(duration,patch.playing===false?player.current.renderPosition:player.current.position)*rate),mode:mode as AuditionState['mode'],
      track_ids:trackIds,reference_revision_id:referenceId,hybrid_job_id:hybridJobId,loop_start_sample:Math.round(loopStart*rate),loop_end_sample:Math.round((loopEnd||duration)*rate),loop,playing:player.current.playing,...patch};
  }
  async function cue(patch:Partial<AuditionState>){
    const generation=++requestGeneration.current,state=desired(patch);
    localCuePending.current=true;
    if(patch.playing===false){player.current.pause();setPlaying(false);}
    if(patch.position_sample!==undefined){player.current.pause();player.current.offset=patch.position_sample/rate;setPosition(patch.position_sample/rate);}
    try{const result=await call<Cue>('audition.set',{project_id:head.project_id,state},head.id,true);
      if(generation!==requestGeneration.current)return;
      await applyCue(state,result.sequence,patch.position_sample===undefined&&state.playing===true);
      if(result.sequence)await call('audition.report',{project_id:head.project_id,sequence:result.sequence,
        position_sample:Math.round(player.current.position*rate),status:player.current.playing?'playing':state.playing?'needs_activation':'paused'},head.id,true);
    }catch(e){setError((e as Error).message);}
    finally{if(generation===requestGeneration.current)localCuePending.current=false;}
  }
  async function playToggle(){
    if(!player.current.playing)try{await player.current.activate();}catch(e){setError((e as Error).message);return;}
    await cue({playing:!player.current.playing});
  }
  async function render(){setLoading(true);
    try{await call<Job>('jobs.submit',{project_id:head.project_id,kind:'project.render',payload:{revision_id:revision.id}},head.id,true);setError('渲染任务已保存，完成后自动显示。');}
    catch(e){setError((e as Error).message);}finally{setLoading(false);}}
  async function compare(id:string){const result=await call<{diff:Difference}>('revisions.compare',{project_id:head.project_id,before_revision:id,after_revision:revision.id});setDifference(result.diff);}

  function locate(citation:Citation){
    player.current.pause();setPlaying(false);
    if(citation.revision_id!==revision.id){pendingCitation.current=citation;setVersionId(citation.revision_id);return;}
    setSelected(citation.track_id??'all');setLoopStart(citation.start_sample/rate);setLoopEnd(citation.end_sample/rate);
    const nextZoom=Math.min(24,Math.max(1,duration/Math.max(1,(citation.end_sample-citation.start_sample)/rate)));
    setZoom(nextZoom);setViewStart(Math.min(citation.start_sample/rate,Math.max(0,duration-duration/nextZoom)));
    void cue({position_sample:citation.start_sample,loop_start_sample:citation.start_sample,loop_end_sample:citation.end_sample,playing:false});
  }
  useEffect(()=>{const citation=pendingCitation.current;if(citation?.revision_id===revision.id){pendingCitation.current=null;locate(citation);}},[revision.id]);

  useEffect(()=>{const node=canvas.current;if(!node)return;
    const observer=new ResizeObserver(entries=>setWidth(entries[0].contentRect.width));observer.observe(node);return()=>observer.disconnect();},[]);
  useEffect(()=>{
    const node=canvas.current;if(!node||!duration)return;
    const height=444,dpr=devicePixelRatio;node.width=Math.round(width*dpr);node.height=height*dpr;
    const ctx=node.getContext('2d')!;ctx.scale(dpr,dpr);ctx.fillStyle='#10121d';ctx.fillRect(0,0,width,height);
    const left=46,available=width-left-12,visible=duration/zoom;
    const x=(sample:number)=>left+(sample/rate-viewStart)/visible*available;
    const pitches=notes.map(n=>n.pitch),low=Math.max(0,(pitches.length?Math.min(...pitches):48)-2),high=Math.min(127,(pitches.length?Math.max(...pitches):84)+2);
    const row=310/Math.max(1,high-low+1);ctx.font='10px "Segoe UI", sans-serif';
    for(let pitch=low;pitch<=high;pitch++){
      const y=125+(high-pitch)*row,black=[1,3,6,8,10].includes(pitch%12);
      ctx.fillStyle=black?'#10121d':'#171a28';ctx.fillRect(left,y,available,Math.max(1,row-.5));
      ctx.fillStyle=black?'#242838':'#c4c9da';ctx.fillRect(3,y,36,Math.max(1,row-.6));
      if(pitch%12===0){ctx.fillStyle='#727d98';ctx.fillText(`C${pitch/12-1}`,8,y+row-1);}}
    const step=visible>180?30:visible>60?10:visible>20?5:1;
    for(let seconds=Math.ceil(viewStart/step)*step;seconds<=viewStart+visible;seconds+=step){
      const px=x(seconds*rate);ctx.strokeStyle='#272b40';ctx.beginPath();ctx.moveTo(px,25);ctx.lineTo(px,height);ctx.stroke();ctx.fillStyle='#727d98';ctx.fillText(time(seconds),px+4,16);}
    wave.forEach((channels,i)=>{const px=x(i/wave.length*duration*rate);if(px<left||px>width)return;
      const[min,max]=channels[0];ctx.strokeStyle='#5f77c2';ctx.beginPath();ctx.moveTo(px,72+min*29);ctx.lineTo(px,72+max*29);ctx.stroke();});
    ctx.save();ctx.beginPath();ctx.rect(left,118,available,height-118);ctx.clip();
    for(const note of notes){const px=x(note.start_sample),w=(note.end_sample-note.start_sample)/rate/visible*available;if(px+w<left||px>width)continue;
      ctx.fillStyle=colors[Math.max(0,tracks.findIndex(t=>t.id===note.track_id))%colors.length];ctx.globalAlpha=selected==='all'?.76:.94;
      ctx.fillRect(px,125+(high-note.pitch)*row,Math.max(1.6,w),Math.max(1.5,row-1));}ctx.restore();ctx.globalAlpha=1;
    if(loop){ctx.fillStyle='#8b9bff14';ctx.fillRect(x(loopStart*rate),25,(loopEnd-loopStart)/visible*available,height);}
    const cursor=x(position*rate);if(cursor>=left&&cursor<=width){ctx.strokeStyle='#e6eaff';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(cursor,24);ctx.lineTo(cursor,height);ctx.stroke();ctx.fillStyle='#dce3ff';ctx.fillRect(cursor-3,22,6,5);}
  },[width,duration,rate,notes,tracks,position,zoom,viewStart,wave,loop,loopStart,loopEnd,selected]);

  const chord=(snapshot.annotations??[]).filter(a=>a.kind==='chord'&&a.start_sample/rate<=position&&a.end_sample/rate>position).map(a=>a.text).join(' / ');
  const focused=selected==='all'?tracks:tracks.filter(t=>t.id===selected);
  useEffect(()=>{if(selected!=='all'&&!tracks.some(t=>t.id===selected))setSelected('all');},[tracks,selected]);
  return <section className="studio" aria-label="音乐工作台" data-revision={revision.id} data-position={position.toFixed(5)} data-playing={playing}>
    <div className="studio-top"><div className="studio-title"><button className="back-icon" aria-label="返回音乐库" onClick={onBack}>‹</button><div><div className="eyebrow">MUSIC LEARNING <span>/</span> SESSION</div><h2>{title}</h2></div></div><div className="studio-meta"><span className="revision-dot"/>{versionId==='head'?'当前版本':'历史版本'} <code>{revision.id.slice(0,8)}</code><button className="soft-button" onClick={()=>void render()} disabled={loading||renderBusy||editingBusy}>↗ {renderBusy?'渲染中':'生成试听'}</button></div></div>
    <div className="studio-toolbar"><div className="transport-buttons"><button className="icon-button" aria-label="回到开头" onClick={()=>void cue({position_sample:0})}>↤</button><button className="play-button" aria-label={playing?'暂停':'播放'} disabled={loading} onClick={()=>void playToggle()}>{loading?'···':playing?'Ⅱ':'▶'}</button><button className={'icon-button '+(loop?'is-active':'')} aria-label="循环" aria-pressed={loop} onClick={()=>void cue({loop:!loop})}>↻</button></div><div className="clock"><strong>{time(position)}</strong><span>/ {time(mode==='original'?duration:Math.max(duration,player.current.duration))}</span></div><span className="toolbar-divider"/><div className="mode-switch" aria-label="试听方式"><button aria-pressed={mode==='original'} disabled={!snapshot.source_asset_id} onClick={()=>void cue({mode:'original'})}>原曲</button>{referenceId&&<button aria-pressed={mode==='reference'} onClick={()=>void cue({mode:'reference'})}>参考 <span>A</span></button>}<button aria-pressed={mode==='mix'} disabled={!publication} onClick={()=>void cue({mode:'mix'})}>重建 <span>B</span></button>{hybridJob&&<button aria-pressed={mode==='hybrid'} onClick={()=>void cue({mode:'hybrid',hybrid_job_id:hybridJob.id})}>贝斯混合</button>}</div><span className="listening-label">{mode==='hybrid'?'BASS REPLACEMENT':mode==='tracks'?`${trackIds.length} 条声部 · 同步试听`:mode==='mix'?'VST3 RECONSTRUCTION':mode==='reference'?'REFERENCE · '+referenceId?.slice(0,8):'ORIGINAL RECORDING'}</span><div className="zoom-control"><span>−</span><input aria-label="时间轴缩放" type="range" min={1} max={256} step={1} value={zoom} onChange={e=>{setZoom(+e.target.value);setViewStart(0);}}/><span>+</span></div></div>
    {error&&<div className="studio-message" role="status">{error}<button aria-label="关闭工作台提示" onClick={()=>setError('')}>×</button></div>}
    <div className="studio-body"><aside className="track-panel"><div className="panel-heading"><span>声部</span><span>{tracks.length.toString().padStart(2,'0')}</span></div><button className={'all-tracks '+(selected==='all'?'is-selected':'')} onClick={()=>setSelected('all')}><span>▤</span> 全部声部 <small>{(snapshot.notes??[]).length.toLocaleString()}</small></button>
      <div className="track-cards">{tracks.map((track,i)=><div className={'track-card '+(selected===track.id?'is-selected':'')} key={track.id} style={{'--track-color':colors[i%colors.length]} as CSSProperties}><button className="track-select" onClick={()=>setSelected(track.id)}><span className="track-index">{(i+1).toString().padStart(2,'0')}</span><div><strong>{instrumentName(track)}</strong><small title="实际渲染预设">{(snapshot.plugins??[]).find(p=>p.track_id===track.id&&p.order===0)?.preset_name??(track.kind==='audio'?'音频素材':(snapshot.plugins??[]).some(p=>p.track_id===track.id)?'未记录明确预设':'尚未选择音色')}</small><small>{track.kind==='bus'?'BUS':track.kind==='audio'?'AUDIO':track.role??'MIDI'} · {(snapshot.notes??[]).filter(n=>n.track_id===track.id).length} notes</small></div></button><div className="track-actions"><small className="track-preview-label">试听</small><span className="track-color-line"/><button aria-label={`独奏 ${i+1}`} aria-pressed={mode==='tracks'&&trackIds.length===1&&trackIds[0]===track.id} disabled={!publication?.track_renders[track.id]} onClick={()=>void cue({mode:'tracks',track_ids:[track.id]})}>S</button><button aria-label={`试听静音 ${i+1}`} aria-pressed={mode==='tracks'&&!trackIds.includes(track.id)} disabled={!publication} onClick={()=>{const ids=mode==='tracks'?trackIds:tracks.map(t=>t.id),next=ids.includes(track.id)?ids.filter(id=>id!==track.id):[...ids,track.id];if(next.length)void cue({mode:'tracks',track_ids:next});else void cue({playing:false});}}>M</button><span>{track.gain_db??0} dB</span></div></div>)}</div>
      <div className="stem-heading">原曲分轨 <span>AUDIO STEMS</span></div><div className="stem-chips">{(snapshot.stems??[]).map(stem=><a key={stem.id} download href={'/v1/assets/'+stem.asset_id}><i/> {stem.name} <span>↓</span></a>)}</div><div className="local-note"><span>◈</span> 工程保存在本机</div></aside>
      <div className="arrangement"><div className="arrangement-heading"><div><span className="eyebrow">ARRANGEMENT</span><h3>编排与制作</h3></div><span className="view-pill">{selected==='all'?'整曲总览':'声部聚焦'} · {zoom===1?'FIT':zoom+'×'}</span></div><div className="section-lane" aria-label="段落候选">{sections.map((section,i)=><button key={section.id} style={{flex:Math.max(1,section.end_sample-section.start_sample)}} onClick={()=>{setLoopStart(section.start_sample/rate);setLoopEnd(section.end_sample/rate);void cue({position_sample:section.start_sample,loop_start_sample:section.start_sample,loop_end_sample:section.end_sample});}}><i style={{background:colors[i%colors.length]}}/><strong>{section.text}</strong><span>{time(section.start_sample/rate)}</span></button>)}</div>
        <DawEditor revision={revision} selected={selected} onSelect={setSelected} editable={versionId==='head'&&revision.id===head.id} busy={editingBusy} onEdit={manualEdit} onUndo={()=>manualRestore()} onRedo={()=>manualRestore(true)} canUndo={!!undoStack.length} canRedo={!!redoStack.length} start={Math.round(viewStart*rate)} end={Math.round(Math.min(duration,viewStart+duration/zoom)*rate)} position={Math.round(position*rate)} selectionStart={Math.round(loopStart*rate)} selectionEnd={Math.round((loopEnd||duration)*rate)} onSeek={sample=>void cue({position_sample:sample})} overview={<div className="roll-frame"><div className="roll-caption"><span>WAVEFORM</span><span>源样本时间 · 演奏时刻保留</span></div><canvas ref={canvas} data-waveform-buckets={wave.length} className="piano-roll" role="img" aria-label="整曲波形与钢琴卷帘" onClick={e=>{const b=e.currentTarget.getBoundingClientRect(),seconds=viewStart+Math.max(0,Math.min(1,(e.clientX-b.left-46)/(b.width-58)))*duration/zoom;void cue({position_sample:Math.round(Math.min(duration,seconds)*rate)});}}/><div className="roll-footer"><span>PIANO ROLL</span><span>{notes.length.toLocaleString()} NOTES <i/> {rate/1000} kHz</span></div></div>}/>

        <input className="timeline-pan" aria-label="时间轴平移" type="range" min={0} max={Math.max(0,duration-duration/zoom)} step={.05} value={viewStart} onChange={e=>setViewStart(+e.target.value)}/><div className="loop-editor"><label>循环起点 <input aria-label="循环起点秒" type="number" min={0} max={duration} step={.1} value={+loopStart.toFixed(2)} onChange={e=>setLoopStart(+e.target.value)}/></label><span>—</span><label>终点 <input aria-label="循环终点秒" type="number" min={0} max={duration} step={.1} value={+loopEnd.toFixed(2)} onChange={e=>setLoopEnd(+e.target.value)}/></label><button onClick={()=>void cue({loop_start_sample:Math.round(loopStart*rate),loop_end_sample:Math.round(loopEnd*rate),loop:true})}>应用循环</button></div><div className="context-strip"><div><span>当前和弦候选</span><strong>{chord||'—'}</strong></div><div><span>当前试听</span><strong className="context-mode">{mode==='original'?'原始录音':mode==='mix'?'整曲重建':mode==='reference'?'参考版本 A':mode==='hybrid'?'原曲 + 新贝斯':'选定声部'}</strong></div><p>模型分析待核对<br/>重建使用替代音色</p></div></div>
      <aside className="inspector"><div className="inspector-tabs">{[['discussion','讨论'],['analysis','分析'],['plugins','音源'],['instruments','乐器'],['versions','版本']].map(([id,label])=><button key={id} aria-pressed={inspector===id} onClick={()=>setInspector(id)}>{label}</button>)}</div>
        {inspector==='discussion'?<Discussion revision={revision} headId={head.id} instrumentDraft={instrumentDraft} onConsumeDraft={consumeInstrumentDraft} jobs={jobs} start={loopStart} end={loopEnd||duration} trackId={selected==='all'?null:selected} onLocate={locate} onCompare={id=>{void compare(id);setInspector('versions');}}/>:inspector==='analysis'?<><div className="inspector-section"><p className="eyebrow">SESSION OVERVIEW</p><h3>聆听、理解，再创造。</h3><p>以可定位的事件和声音为依据，逐步研究这首作品。</p><div className="stat-grid"><div><strong>{tracks.length}</strong><span>声部</span></div><div><strong>{(snapshot.annotations??[]).filter(a=>a.kind==='chord').length}</strong><span>和弦候选</span></div><div><strong>{sections.length}</strong><span>段落候选</span></div><div><strong>{versions.length}</strong><span>版本</span></div></div></div><details className="inspector-section issues"><summary>待核对与来源 <span>{issues.length}</span></summary><ul>{issues.map((issue,i)=><li key={i}>{issue}</li>)}</ul></details><div className="inspector-section"><span className="mini-label">RENDER STATUS</span><p className="render-ready" data-render-status={renderJob?.status??'none'}>{renderBusy?'◌ 正在生成此版本的音频':renderJob?.status==='failed'?'× 渲染失败 · '+String(renderJob.error?.code):publication?'● 此版本的音频已生成':'○ 此版本等待渲染'}</p><small>音频与版本严格绑定。新的修改需要重新生成试听。</small></div></>:
        inspector==='instruments'?<InstrumentBrowser enabled={selected!=='all'&&revision.id===head.id&&!editingBusy} onChoose={choice=>{setInstrumentDraft({id:crypto.randomUUID(),revisionId:revision.id,trackId:selected,text:'请为当前所选声部使用'+choice.name+'（instrument_id='+choice.id+'）。'+(choice.presetId?'指定音色 '+choice.presetName+'（preset_id='+choice.presetId+'）。':'请从该乐器下的跨库音色中，按编曲、音域与奏法选择合适音色。')+'保持音符、时间与其他声部不变；捕获验证通过后换音色并生成整曲试听。'});setInspector('discussion');}}/>:
        inspector==='plugins'?<div className="inspector-section"><p className="eyebrow">SIGNAL CHAIN</p>{focused.map(track=><div className="plugin-track" key={track.id}><h4>{instrumentName(track)}</h4>{(snapshot.plugins??[]).filter(p=>p.track_id===track.id).sort((a,b)=>a.order-b.order).map(plugin=><details className="plugin-card" key={plugin.id}><summary><span className={plugin.bypass?'plugin-off':'plugin-on'}/>{pluginInfo[plugin.id]?.name??plugin.class_id.slice(0,10)}<small>VST3 · {plugin.version}</small></summary><p>预设：{plugin.preset_name??'旧版通用音色 / 未记录'}<br/><small>{plugin.preset_id??'需要重新选择'}</small></p><p>{Object.keys(plugin.parameters??{}).length} 个公开参数 · 完整状态已保存</p><code>{plugin.state_asset_id.slice(0,16)}</code><div className="parameter-list">{Object.entries(plugin.parameters??{}).slice(0,24).map(([id,value])=><div key={id}><span>{pluginInfo[plugin.id]?.parameters_readback.find(p=>p.id===id)?.name??id}</span><b>{value.toFixed(3)}</b></div>)}</div></details>)}{!(snapshot.plugins??[]).some(p=>p.track_id===track.id)&&<p>无插入插件</p>}</div>)}</div>:
        <div className="inspector-section"><p className="eyebrow">VERSION HISTORY</p><label className="version-select">查看版本<select aria-label="工程版本" value={versionId} onChange={e=>setVersionId(e.target.value)}><option value="head">当前版本</option>{versions.map(v=><option key={v.id} value={v.id}>{v.id.slice(0,8)} · {v.description.slice(0,24)}</option>)}</select></label>{versions.map(v=><div className={'version-card '+(v.id===revision.id?'is-current':'')} key={v.id}><div><i/><code>{v.id.slice(0,8)}</code><small>{new Date(v.created_at*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}</small></div><p>{v.description}</p><button onClick={()=>setVersionId(v.id)}>查看</button><button onClick={()=>void compare(v.id)}>比较差异</button><button onClick={()=>void cue({mode:'reference',reference_revision_id:v.id})}>设为 A</button></div>)}{difference&&<div className="version-diff">{Object.entries(difference).filter(([name])=>name!=='settings').map(([name,v])=><p key={name}>{name} <span>+{v.added.length} −{v.removed.length} Δ{v.changed.length}</span></p>)}</div>}</div>}
      </aside></div><div className="studio-bottom"><span><i/> LOCAL SESSION</span><span>{snapshot.source_asset_id?'原始录音与分析输出独立保留':'独立创作工程'}</span><code>REV {revision.id.slice(0,8).toUpperCase()}</code></div>
  </section>;
}
