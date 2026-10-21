import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import type {CSSProperties} from 'react';
import type {AuditionState, Citation, Job, PreprocessingResult, PreprocessingView, Revision, MixingState} from '../../../packages/contracts/types';
import {Discussion} from './Discussion';
import {hasTrackContent,switchPreset} from './manualPreset';
import type {PresetChoice,SoundChange,SoundPhase} from './manualPreset';
import {SoundPanel} from './SoundPanel';
import {useInstrumentLabels} from './useInstrumentLabels';
import {SourceArrangement} from './SourceArrangement';
import {DawEditor} from './DawEditor';
import type {EditRequest} from './DawEditor';
import {trackName as instrumentName,orderedTracks} from './dawEditing';
import {call} from './api';
import {writeAudition} from './auditionSync';
import {boundAudition,sourceSample} from './auditionState';
import {NativeTransport} from './engine/NativeTransport';
import type {MixValues} from './liveMix';
import {audibility} from './liveMix';
import {WindowControls,dragWindow} from './WindowChrome';
import {ContextMenu} from './ContextMenu';
import {Icon} from './Icon';
import {trackColors as colors,canvasTheme} from './visual';
import {useDisplayScale} from './useDisplayScale';
import {PanelResize,usePanelSize} from './PanelResize';
import {subscribeDomain} from './events';
import './workbench.css';
import './daw.css';
import './discussion.css';

const time=(s:number)=>`${Math.floor(s/60).toString().padStart(2,'0')}:${Math.floor(s%60).toString().padStart(2,'0')}`;
type Render=PreprocessingResult&{start_sample?:number;end_sample?:number};
type Cue={sequence:string|null;desired:AuditionState|null;observed:unknown};
type Version={id:string;revision_id:string;title:string;created_at:number};
type Difference=Record<string,{added:string[];removed:string[];changed:string[]}>;
type PluginInfo={name:string;manufacturer:string;is_instrument?:boolean;metadata_asset_id?:string;parameters_readback:Array<{id:string;name:string;label:string;text:string}>};

export function Workbench({title,revision:head,publication:initial,jobs=[],onBack,onCommitted,onReimport}:{
  title:string;revision:Revision;publication:PreprocessingResult|null;jobs?:Job[];onBack:()=>void;onCommitted?:()=>Promise<void>;onReimport?:()=>Promise<void>;
}){
  const [savedTitle,setSavedTitle]=useState(''),[savingVersion,setSavingVersion]=useState(false);
  const [liveEpoch,setLiveEpoch]=useState(0);
  const [trackRequest,setTrackRequest]=useState<{sequence:number;action:string;trackId?:string}>({sequence:0,action:''});
  const [trackMenu,setTrackMenu]=useState<{x:number;y:number;id:string}|null>(null);
  const requestTrack=(action:string,trackId?:string)=>setTrackRequest(value=>({sequence:value.sequence+1,action,trackId}));
  const [liveLoading,setLiveLoading]=useState<string[]>([]);
  const [trackWidth,setTrackWidth]=usePanelSize('studio.track-width',190,140,480);
  const [aiWidth,setAiWidth]=usePanelSize('studio.ai-width',340,260,900);
  const body=useRef<HTMLDivElement>(null),[bodyWidth,setBodyWidth]=useState(window.innerWidth);
  useEffect(()=>{const node=body.current;if(!node)return;const observer=new ResizeObserver(entries=>setBodyWidth(entries[0].contentRect.width));observer.observe(node);return()=>observer.disconnect();},[]);
  const rightWidth=Math.min(aiWidth,Math.max(260,bodyWidth-140-332));
  const leftWidth=Math.min(trackWidth,Math.max(140,bodyWidth-rightWidth-332));
  const [versionId,setVersionId]=useState('head');
  const [revision,setRevision]=useState(head);
  const instrumentLabels=useInstrumentLabels((revision.snapshot.plugins??[]).flatMap(p=>p.preset_id?[p.preset_id]:[]));
  const revisionRef=useRef(revision);revisionRef.current=revision;
  const [publication,setPublication]=useState<Render|null>(initial);
  const [mixDraft,setMixDraft]=useState<Record<string,MixValues>>({});
  const [originalMix,setOriginalMix]=useState<Record<string,MixValues>>({});
  const originalMixRef=useRef(originalMix);originalMixRef.current=originalMix;
  const [mixing,setMixing]=useState(false),[mixProfile,setMixProfile]=useState<MixingState|null>(null),[mixDirty,setMixDirty]=useState(false),[mixSaving,setMixSaving]=useState(false);
  const mixRef=useRef<Record<string,MixValues>>({}),preserveMix=useRef<string|null>(null);
  const [versions,setVersions]=useState<Version[]>([]);
  const [selected,setSelected]=useState('all');
  const [pianoRequest,setPianoRequest]=useState(0);
  const [inspector,setInspector]=useState('discussion');
  const [chatFocused,setChatFocused]=useState(false);
  const [soundChange,setSoundChange]=useState<SoundChange|null>(null);
  const soundBusy=useRef(false),soundMounted=useRef(true);
  const soundEpoch=useRef(0);
  const soundAttempt=useRef<{choice:PresetChoice;trackId:string;targetId:string|null;applied?:Revision}|null>(null);
  useEffect(()=>{soundMounted.current=true;return()=>{soundMounted.current=false;soundEpoch.current++;};},[head.project_id]);
  useEffect(()=>{if(soundChange?.phase!=='done')return;const timer=setTimeout(()=>setSoundChange(null),1800);return()=>clearTimeout(timer);},[soundChange?.phase]);
  const pendingCitation=useRef<Citation|null>(null);
  const [mode,setMode]=useState(initial?'mix':'original');
  const [referenceId,setReferenceId]=useState<string|null>(null);
  const [trackIds,setTrackIds]=useState<string[]>([]);
  const [position,setPosition]=useState(0);
  const [playing,setPlaying]=useState(false);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  const [loop,setLoop]=useState(false);
  const [loopStart,setLoopStart]=useState(0);
  const [loopEnd,setLoopEnd]=useState(0);
  const [zoom,setZoom]=useState(1),[focusEditor,setFocusEditor]=useState(false);
  const [viewStart,setViewStart]=useState(0);
  const [livePeaks,setLivePeaks]=useState<Record<string,number>>({});
  const cueWrites=useRef<Promise<unknown>>(Promise.resolve());
  const currentHead=useRef(head.id);
  useEffect(()=>{if(!localCommit.current||head.id===localCommit.current||head.created_at>localCommitTime.current)currentHead.current=head.id;},[head.id]);
  const transportWrite=useCallback(<T,>(operation:'audition.set'|'audition.report',args:Record<string,unknown>,followHead=false)=>writeAudition<T>(call,head.project_id,operation,args,()=>currentHead.current,id=>{currentHead.current=id;},followHead),[head.project_id]);
  const viewport=(nextZoom:number,anchor=.5)=>{const z=Math.max(1,Math.min(256,nextZoom)),at=viewStart+duration/zoom*anchor;setZoom(z);setViewStart(Math.max(0,Math.min(duration-duration/z,at-duration/z*anchor)));};
  const [width,setWidth]=useState(900);
  const displayScale=useDisplayScale();
  const [wave,setWave]=useState<Array<Array<[number,number]>>>([]);
  const [difference,setDifference]=useState<Difference|null>(null);
  const [pluginInfo,setPluginInfo]=useState<Record<string,PluginInfo>>({});
  const pluginInfoCache=useRef(new Map<string,PluginInfo>());
  const [pluginInfoError,setPluginInfoError]=useState(''),[pluginInfoEpoch,setPluginInfoEpoch]=useState(0);
  const [sourceIssues,setSourceIssues]=useState<string[]>([]);
  const [editingBusy,setEditingBusy]=useState(false),[undoStack,setUndoStack]=useState<string[]>([]),[redoStack,setRedoStack]=useState<string[]>([]);
  const localCommitTime=useRef(0);
  const editingLock=useRef(false),localCommit=useRef<string|null>(null),commitBefore=useRef<string|null>(null),historyHead=useRef<string|null>(null);
  const [canvas,setCanvas]=useState<HTMLCanvasElement|null>(null);
  const player=useRef(new NativeTransport());
  const lastCue=useRef<string|null>(null);
  const lastCueRevision=useRef<string|null>(null);
  const requestGeneration=useRef(0),applyGeneration=useRef(0);
  const localCuePending=useRef(false);
  const applyingCue=useRef(false);
  const wantsPlaying=useRef(false);
  const playbackFinished=useRef(false),onPlaybackEnded=useRef(()=>{});
  const lastReportTime=useRef(0);
  const snapshot=revision.snapshot;
  const sourceReference=snapshot.source_reference;
  const originalView=mode==='original'&&!!sourceReference;
  const [copyingSource,setCopyingSource]=useState(false);
  const displayedTracks=mode==='original'?(sourceReference?.tracks??[]):orderedTracks(snapshot);
  const rate=snapshot.sample_rate??44100;
  const duration=(snapshot.duration_samples??0)/rate;
  const tracks=useMemo(()=>orderedTracks(snapshot),[snapshot]);
  const atHead=versionId==='head'&&(revision.id===head.id||revision.id===localCommit.current);
  const renderJob=jobs.filter(job=>job.kind==='project.render'&&job.revision_id===revision.id).at(-1);
  const renderBusy=!!renderJob&&['queued','running','cancel_requested'].includes(renderJob.status);
  const exportBusy=jobs.some(job=>job.kind==='project.export'&&job.revision_id===revision.id&&job.payload.format==='wav'&&['queued','running','cancel_requested'].includes(job.status));
  const notes=useMemo(()=>(snapshot.notes??[]).filter(n=>selected==='all'||n.track_id===selected),[snapshot,selected]);
  const issues=[...(publication?.issues??[]),...sourceIssues];
  useEffect(()=>{
    player.current=new NativeTransport();const transport=player.current;let frame=0;
    transport.onEnded=()=>{playbackFinished.current=true;setPlaying(false);setPosition(transport.offset);onPlaybackEnded.current();};
    transport.onState=state=>{
      setPlaying(!!state.playing);setLivePeaks(state.meters??{});
      setLiveLoading(Object.entries(state.plugins??{}).filter(([,value])=>value.status==='loading').map(([id])=>id));
    };
    transport.onError=error=>{if(error.name!=='AbortError')setError(error.message);};
    const draw=()=>{setPosition(transport.position);frame=requestAnimationFrame(draw);};
    frame=requestAnimationFrame(draw);transport.startPolling();
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
        call<Version[]>('versions.list',{project_id:head.project_id,limit:1000})]);
      if(!active)return;
      if(versionId==='head'&&localCommit.current&&read.id!==localCommit.current){if(read.created_at<=localCommitTime.current)return;localCommit.current=null;currentHead.current=read.id;}
      const full=renders.filter(r=>(r.start_sample??0)===0&&(r.end_sample??read.snapshot.duration_samples)===read.snapshot.duration_samples);
      if(versionId==='head'&&read.id!==revisionRef.current.id&&player.current.state.project_id===head.project_id)preserveMix.current=read.id;
      setRevision(read);setPublication(full.at(-1)??preprocessed.publication);setVersions(list.slice().reverse());
    };
    void update().catch(e=>{if(active)setError(e.message);});
    let pending:ReturnType<typeof setTimeout>|undefined;
    const unsubscribe=subscribeDomain(event=>{
      if(event.type==='connection.ready')setLiveEpoch(value=>value+1);
      if(event.type==='connection.ready'||event.project_id===head.project_id&&['revision.created','preprocessing.completed','render.published','version.saved'].includes(event.type)){
        clearTimeout(pending);pending=setTimeout(()=>void update().catch(()=>{}),120);
      }
    });
    const timer=setInterval(()=>void update().catch(()=>{}),15000);
    return()=>{active=false;clearInterval(timer);clearTimeout(pending);unsubscribe();};
  },[versionId,head]);

  useEffect(()=>{if(localCommit.current&&(head.id===localCommit.current||head.created_at>localCommitTime.current))localCommit.current=null;
    if(editingLock.current||localCommit.current)return;
    if(historyHead.current&&head.id!==historyHead.current){setUndoStack([]);setRedoStack([]);historyHead.current=null;}
  },[head.id,editingBusy]);
  async function acceptManualRevision(next:Revision,before=revision.id){
    revisionRef.current=next;
    commitBefore.current=before;localCommit.current=next.id;localCommitTime.current=next.created_at;historyHead.current=next.id;preserveMix.current=next.id;currentHead.current=next.id;
    setRevision(next);setPublication(null);setVersionId('head');setError('');
    // The committed edit is usable now. Derived playback and library refreshes
    // must not extend the editor's transaction lock.

    void onCommitted?.().catch(e=>{if(currentHead.current===next.id)setError('工程列表刷新失败：'+(e as Error).message);});
  }
  async function loadManualSound(applied:Revision,trackId:string,targetId:string,force=false){
    const expected=applied.snapshot.plugins?.find(p=>p.id===targetId);
    if(!expected)throw new Error('音轨乐器不存在');
    if(!hasTrackContent(applied,trackId))return;
    const current=revisionRef.current;
    if(current.snapshot.plugins?.find(p=>p.id===targetId)?.state_asset_id!==expected.state_asset_id)throw new Error('音轨的音色已发生变化');
    await player.current.waitTrack({project:head.project_id,revision:current.id,view:'arrangement'},trackId,force);
  }

  async function chooseManualPreset(choice:PresetChoice,targetId:string|null,trackId=selected,retry=false){
    if(soundBusy.current||!atHead)return;
    const base=revisionRef.current,track=base.snapshot.tracks?.find(t=>t.id===trackId);if(!track)return;
    soundBusy.current=true;
    const epoch=soundEpoch.current,active=()=>soundMounted.current&&soundEpoch.current===epoch;
    const previous=soundAttempt.current;
    const attempt=retry&&previous?previous:{choice,trackId,targetId};soundAttempt.current=attempt;
    setSoundChange({trackId,trackName:instrumentName(track),presetId:choice.presetId,presetName:choice.presetName,phase:attempt.applied?'load':'capture',completed:attempt.applied?2:0,startedAt:Date.now(),applied:!!attempt.applied});
    const progress=(phase:Exclude<SoundPhase,'error'>)=>{if(active())setSoundChange(state=>state?{...state,phase,completed:{capture:0,apply:1,load:2,done:3}[phase],applied:!!attempt.applied}:state);};
    const load=async(next:Revision)=>{
      const plugin=next.snapshot.plugins?.find(p=>p.id===targetId)??next.snapshot.plugins?.find(p=>p.track_id===trackId&&p.preset_id===choice.presetId);
      if(!plugin)throw new Error('未找到更换后的乐器');
      await loadManualSound(next,trackId,plugin.id,retry);
    };
    try{
      if(attempt.applied){await load(attempt.applied);progress('done');}
      else await switchPreset({api:call,base,trackId,targetId,choice,progress,active,
        commit:async(before,operations,description)=>{
          while(editingLock.current){await new Promise(resolve=>setTimeout(resolve,30));if(!active())throw new DOMException('窗口已关闭','AbortError');}
          if(!active())throw new DOMException('窗口已关闭','AbortError');
          editingLock.current=true;setEditingBusy(true);
          try{const next=await call<Revision>('edits.apply',{project_id:head.project_id,scope:{track_ids:[trackId],start_sample:0,end_sample:before.snapshot.duration_samples,structural:true},operations,author:'user',description},before.id,true);
            attempt.applied=next;if(active()){setUndoStack(stack=>[...stack,before.id]);setRedoStack([]);await acceptManualRevision(next,before.id);}return next;
          }finally{editingLock.current=false;setEditingBusy(false);}
        },load});
    }catch(e){if(active())setSoundChange(state=>state?{...state,phase:'error',message:(e as Error).message,applied:!!attempt.applied}:state);}
    finally{soundBusy.current=false;}
  }
  async function manualEdit(request:EditRequest){
    if(editingLock.current)throw new Error('前一次编辑仍在保存。');
    if(!atHead)throw new Error('历史版本只读，请切回当前版本。');
    editingLock.current=true;setEditingBusy(true);
    try{const next=await call<Revision>('edits.apply',{project_id:head.project_id,
      scope:{track_ids:request.trackIds,start_sample:0,end_sample:snapshot.duration_samples,structural:request.structural??false},
      operations:request.operations,author:'user',description:request.description},revision.id,true);
      setUndoStack(stack=>[...stack,revision.id]);setRedoStack([]);await acceptManualRevision(next);
    }finally{editingLock.current=false;setEditingBusy(false);}
  }
  async function manualRestore(redo=false){
    const stack=redo?redoStack:undoStack,target=stack.at(-1);if(!target)return;
    if(editingLock.current||!atHead)throw new Error('请等待保存完成并切回当前版本。');
    editingLock.current=true;setEditingBusy(true);
    try{const next=await call<Revision>('revisions.restore',{project_id:head.project_id,revision_id:target,description:redo?'手动编辑重做':'手动编辑撤销'},revision.id,true);
      if(redo){setRedoStack(stack=>stack.slice(0,-1));setUndoStack(stack=>[...stack,revision.id]);}
      else{setUndoStack(stack=>stack.slice(0,-1));setRedoStack(stack=>[...stack,revision.id]);}
      await acceptManualRevision(next);
    }finally{editingLock.current=false;setEditingBusy(false);}
  }

  useEffect(()=>{
    if(preserveMix.current===revision.id){return;}
    mixRef.current={};setMixDraft({});
    playbackFinished.current=false;
    player.current.pause();player.current.offset=0;setPosition(0);
    setLoopStart(previous=>Math.min(previous,Math.max(0,duration-1/rate)));
    setLoopEnd(previous=>previous>0?Math.min(previous,duration):duration);
    setMode((snapshot.notes?.length??0)>0||!snapshot.source_asset_id?'mix':'original');setViewStart(0);setZoom(1);
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
    if(inspector!=='sounds')return;
    let active=true;setPluginInfoError('');
    void Promise.all((snapshot.plugins??[]).filter(p=>p.track_id===selected).map(async plugin=>{
      if(!plugin.descriptor_asset_id)return null;
      let info=pluginInfoCache.current.get(plugin.descriptor_asset_id);
      if(!info){const response=await fetch('/v1/assets/'+plugin.descriptor_asset_id);if(!response.ok)throw new Error('当前乐器读取失败');const raw=await response.json();info={...(raw.plugin??raw),metadata_asset_id:plugin.descriptor_asset_id} as PluginInfo;pluginInfoCache.current.set(plugin.descriptor_asset_id,info);}
      return [plugin.id,info] as const;
    })).then(items=>{if(active)setPluginInfo(previous=>({...previous,...Object.fromEntries(items.filter((item):item is readonly [string,PluginInfo]=>item!==null))}));}).catch(e=>{if(active)setPluginInfoError(e.message);});
    return()=>{active=false;};
  },[revision.id,inspector,selected,pluginInfoEpoch]);

  useEffect(()=>{
    let active=true;setSourceIssues([]);
    const included=new Set(snapshot.analysis_run_ids??[]);
    void call<Array<{id:string;payload:{issues?:string[]}}>>('analysis.list',{project_id:head.project_id,limit:1000}).then(runs=>{
      if(active)setSourceIssues([...new Set(runs.filter(run=>included.has(run.id)).flatMap(run=>(run.payload.issues??[]).map(issue=>'原始分析 · '+issue)))]);
    }).catch(()=>{});
    return()=>{active=false;};
  },[revision.id,head.project_id]);


  const currentMonitor=useCallback(()=>Object.fromEntries((mode==='original'?(sourceReference?.tracks??[]):tracks)
    .filter(track=>track.kind!=='midi').map(track=>[track.id,{gain_db:track.gain_db??0,pan:0,mute:false,solo:false,
      ...(mode==='original'?originalMixRef.current[track.id]:mixing?mixProfile?.tracks[track.id]:mixRef.current[track.id])}])),[mode,sourceReference,tracks,mixing,mixProfile]);
  useEffect(()=>{
    const transport=player.current;let active=true;
    const target={project:head.project_id,revision:mode==='reference'?(referenceId??revision.id):revision.id,view:mode==='original'?'original' as const:'arrangement' as const};
    void transport.open(target).then(()=>{if(active&&mode!=='reference')return transport.monitor(currentMonitor());}).catch(e=>{if(active&&e.name!=='AbortError')setError(e.message);});
    return()=>{active=false;};
  },[revision.id,mode,referenceId,liveEpoch]);
  useEffect(()=>{void player.current.monitor(currentMonitor()).catch(e=>{if(e.name!=='AbortError')setError(e.message);});},[mixDraft,originalMix,mixing,mixProfile]);

  async function updateSource(patch:Parameters<typeof SourceArrangement>[0]['onUpdate'] extends (p:infer P)=>unknown?P:never){
    setEditingBusy(true);try{const next=await call<Revision>('reference.update',{project_id:head.project_id,revision_id:revision.id,...patch},revision.id,true);await acceptManualRevision(next);}finally{setEditingBusy(false);}
  }
  async function saveVersion(){setSavingVersion(true);try{
    await cueWrites.current.catch(()=>{});
    const saved=await call<Version>('versions.save',{project_id:head.project_id,revision_id:currentHead.current,title:savedTitle.trim()||'版本 '+(versions.length+1),track_levels:Object.fromEntries(tracks.map(t=>[t.id,{gain_db:mixRef.current[t.id]?.gain_db??t.gain_db??0}]))},currentHead.current,true);
    setVersions(items=>[saved,...items.filter(item=>item.id!==saved.id)]);setSavedTitle('');await onCommitted?.();
  }catch(e){setError((e as Error).message);}finally{setSavingVersion(false);}}
  useEffect(()=>{if(!mixing||mixProfile)return;let active=true;void call<MixingState>('mixing.get',{project_id:head.project_id}).then(profile=>{if(active){setMixProfile(profile);setMixDirty(false);}}).catch(e=>setError(e.message));return()=>{active=false;};},[mixing,head.project_id]);
  async function saveMixProfile(){
    if(!mixProfile)throw new Error('混音设置尚未读取。');
    setMixSaving(true);try{
      const saved=await call<MixingState>('mixing.save',{project_id:head.project_id,expected_version:mixProfile.version,tracks:mixProfile.tracks},undefined,true);
      setMixProfile(saved);setMixDirty(false);return saved;
    }finally{setMixSaving(false);}
  }
  function changeMix(id:string,patch:MixValues){
    if(originalView&&sourceReference?.tracks.some(t=>t.id===id)){
      const draft={...originalMixRef.current,[id]:{...originalMixRef.current[id],...patch}};
      originalMixRef.current=draft;setOriginalMix(draft);
    }else if(mixing){setMixProfile(profile=>profile?{...profile,tracks:{...profile.tracks,[id]:{...profile.tracks[id],...patch}}}:profile);setMixDirty(true);}
    else{const draft={...mixRef.current,[id]:{...mixRef.current[id],...patch}};mixRef.current=draft;setMixDraft(draft);}
    void player.current.monitor({[id]:patch}).catch(e=>setError(e.message));
    if(mode!=='mix'&&!originalView){setMode('mix');setTrackIds([]);}
  }

  const applyCue=useCallback(async(state:AuditionState,sequence:string|null,preserve=false)=>{
    const applying=++applyGeneration.current;
    wantsPlaying.current=state.playing??false;
    if(state.revision_id!==revision.id){const project=await call<{current_revision:string}>('projects.get',{project_id:head.project_id});if(project.current_revision===state.revision_id){setVersionId('head');await onCommitted?.();}else setVersionId(state.revision_id);return;}
    state=boundAudition(state,snapshot.duration_samples??0);lastCueRevision.current=state.revision_id;
    if(sequence){if(sequence!==lastCue.current&&state.playing)playbackFinished.current=false;lastCue.current=sequence;}
    const nextMode=state.mode??'original';
    setMode(nextMode);setTrackIds(state.track_ids??[]);setLoop(state.loop??false);setReferenceId(state.reference_revision_id??null);
    setLoopStart((state.loop_start_sample??0)/rate);setLoopEnd((state.loop_end_sample??snapshot.duration_samples??0)/rate);
    const target={project:head.project_id,revision:nextMode==='reference'?(state.reference_revision_id??revision.id):revision.id,view:nextMode==='original'?'original' as const:'arrangement' as const};
    const monitor=nextMode==='reference'?undefined:Object.fromEntries((nextMode==='original'?(sourceReference?.tracks??[]):tracks)
      .filter(track=>track.kind!=='midi').map(track=>[track.id,{gain_db:track.gain_db??0,pan:0,mute:false,solo:false,
        ...(nextMode==='original'?originalMixRef.current[track.id]:mixing?mixProfile?.tracks[track.id]:mixRef.current[track.id]),
        ...(nextMode==='tracks'?{mute:!state.track_ids?.includes(track.id)}:{})}]));
    applyingCue.current=true;setLoading(true);
    try{
      await player.current.apply(target,{playing:state.playing??false,
        ...(!preserve?{sample:state.position_sample??0}:{}),loop:state.loop??false,
        start:state.loop_start_sample??0,end:state.loop_end_sample??snapshot.duration_samples},monitor);
      if(applying===applyGeneration.current){setPlaying(player.current.playing);setError('');}
    }catch(e){if(applying===applyGeneration.current)setError((e as Error).message);}
    finally{if(applying===applyGeneration.current){applyingCue.current=false;setLoading(false);}}
  },[revision.id,rate,snapshot.duration_samples,sourceReference,tracks,mixing,mixProfile]);

  useEffect(()=>{
    let active=true;
    const poll=async()=>{const cue=await call<Cue>('audition.get',{project_id:head.project_id});
      const changed=cue.sequence!==lastCue.current;
      const readyNow=!playbackFinished.current&&cue.desired?.revision_id===revision.id&&cue.desired.playing&&!player.current.playing&&player.current.state.status==='ready';
      if(active&&!localCuePending.current&&cue.sequence&&(changed||readyNow)&&cue.desired)await applyCue(cue.desired,cue.sequence,lastCueRevision.current!==cue.desired.revision_id&&preserveMix.current===revision.id&&player.current.playing);
      if(active&&!localCuePending.current&&cue.sequence===lastCue.current&&cue.sequence&&cue.desired?.revision_id===revision.id&&(changed||Date.now()-lastReportTime.current>3000)){
        lastReportTime.current=Date.now();
        const status=applyingCue.current?'loading':player.current.playing?'playing':cue.desired.playing?'error':'paused';
        await transportWrite('audition.report',{sequence:cue.sequence,position_sample:sourceSample(player.current.position*rate,snapshot.duration_samples??0),status});
      }};
    let pending:ReturnType<typeof setTimeout>|undefined;
    const unsubscribe=subscribeDomain(event=>{
      if(event.type==='connection.ready'||event.project_id===head.project_id&&event.type==='audition.set'){
        clearTimeout(pending);pending=setTimeout(()=>void poll().catch(()=>{}),50);
      }
    });
    void poll().catch(()=>{});const timer=setInterval(()=>void poll().catch(()=>{}),5000);
    return()=>{active=false;clearInterval(timer);clearTimeout(pending);unsubscribe();};
  },[head.project_id,head.id,revision.id,rate,applyCue,transportWrite]);

  function desired(patch:Partial<AuditionState>={}):AuditionState{
    return boundAudition({revision_id:revision.id,position_sample:Math.round(Math.min(duration,patch.playing===false?player.current.renderPosition:player.current.position)*rate),mode:mode as AuditionState['mode'],
      track_ids:trackIds,level_match:false,reference_revision_id:referenceId,loop_start_sample:Math.round(loopStart*rate),loop_end_sample:Math.round((loopEnd||duration)*rate),loop,playing:player.current.playing||wantsPlaying.current,...patch},snapshot.duration_samples??0);
  }
  async function cue(patch:Partial<AuditionState>){
    const generation=++requestGeneration.current;let state=desired(patch);
    localCuePending.current=true;
    if(patch.playing===false){player.current.pause();state.position_sample=Math.round(player.current.offset*rate);setPlaying(false);}
    state=boundAudition(state,snapshot.duration_samples??0);
    if(patch.position_sample!==undefined){playbackFinished.current=false;player.current.pause();player.current.offset=(state.position_sample??0)/rate;setPosition((state.position_sample??0)/rate);}
    // Local audio and saving are independent. A persistence failure must not
    // tear down working voices or undo a solo/fader gesture.
    const local=applyCue(state,null,patch.position_sample===undefined&&state.playing===true);
    const followHead=versionId==='head';
    const write=cueWrites.current.catch(()=>{}).then(async()=>{await local;const engine=player.current.state;
      return transportWrite<Cue>('audition.set',{state,...(engine.engine_session?{engine_ack:{engine_session:engine.engine_session,generation:engine.generation}}:{})},followHead);});
    cueWrites.current=write;
    try{const result=await write;await local;
      if(!result)return;
      if(generation!==requestGeneration.current)return;
      lastCue.current=result.sequence;
      if(result.desired?.revision_id!==state.revision_id)await onCommitted?.();
      if(result.sequence)await transportWrite('audition.report',{sequence:result.sequence,
        position_sample:sourceSample(player.current.position*rate,snapshot.duration_samples??0),status:player.current.playing?'playing':state.playing?'needs_activation':'paused'});
    }catch(e){if(generation===requestGeneration.current)setError('播放位置未保存：'+(e as Error).message);}
    finally{if(generation===requestGeneration.current)localCuePending.current=false;}
  }
  onPlaybackEnded.current=()=>{void cue({playing:false});};

  async function playToggle(){
    if(!duration)return;
    if(wantsPlaying.current&&!player.current.playing&&(loading||liveLoading.length)){await cue({playing:false});return;}
    const restart=!player.current.playing&&(playbackFinished.current||player.current.position>=duration);
    if(restart)playbackFinished.current=false;
    await cue({playing:!player.current.playing,...(restart?{position_sample:0}:{})});
  }
  const playShortcut=useRef(()=>{});
  playShortcut.current=()=>{void playToggle();};
  useEffect(()=>{
    const handle=(event:KeyboardEvent)=>{
      const target=event.target as HTMLElement|null;
      if(event.code!=='Space'||event.repeat||event.ctrlKey||event.metaKey||event.altKey||target?.closest('input,textarea,select,[contenteditable="true"],.discussion button,.discussion summary,.toolbar-pin,[aria-label="展开或收起工具栏"]'))return;
      event.preventDefault();event.stopPropagation();playShortcut.current();
    };
    window.addEventListener('keydown',handle,true);
    return()=>window.removeEventListener('keydown',handle,true);
  },[]);
  async function render(){setLoading(true);
    try{const profile=mixDirty?await saveMixProfile():mixProfile;await call<Job>('jobs.submit',{project_id:head.project_id,kind:'project.export',payload:{revision_id:revision.id,format:'wav',mixing_version:profile?.version??0}},currentHead.current,true);setError('');await onCommitted?.();}
    catch(e){setError((e as Error).message);}finally{setLoading(false);}}
  async function compare(id:string){const result=await call<{diff:Difference}>('revisions.compare',{project_id:head.project_id,before_revision:id,after_revision:revision.id});setDifference(result.diff);}

  function locate(citation:Citation){
    player.current.pause();setPlaying(false);
    if(citation.revision_id!==revision.id){pendingCitation.current=citation;setVersionId(citation.revision_id);return;}
    setSelected(citation.track_id??'all');
    const nextZoom=Math.min(24,Math.max(1,duration/Math.max(1,(citation.end_sample-citation.start_sample)/rate)));
    setZoom(nextZoom);setViewStart(Math.min(citation.start_sample/rate,Math.max(0,duration-duration/nextZoom)));
    void cue({position_sample:citation.start_sample,playing:false,...(citation.view==='original'?{mode:'original'}:{})});
  }
  useEffect(()=>{const citation=pendingCitation.current;if(citation?.revision_id===revision.id){pendingCitation.current=null;locate(citation);}},[revision.id]);

  useEffect(()=>{if(!canvas)return;
    const observer=new ResizeObserver(entries=>setWidth(entries[0].contentRect.width));observer.observe(canvas);return()=>observer.disconnect();},[canvas]);
  useEffect(()=>{
    const node=canvas;if(!node||!duration)return;
    const height=444,dpr=displayScale;node.width=Math.round(width*dpr);node.height=Math.round(height*dpr);
    const ctx=node.getContext('2d')!;ctx.scale(dpr,dpr);ctx.fillStyle=canvasTheme.background;ctx.fillRect(0,0,width,height);
    const left=46,available=width-left-12,visible=duration/zoom;
    const x=(sample:number)=>left+(sample/rate-viewStart)/visible*available;
    const pitches=notes.map(n=>n.pitch),low=Math.max(0,(pitches.length?Math.min(...pitches):48)-2),high=Math.min(127,(pitches.length?Math.max(...pitches):84)+2);
    const row=310/Math.max(1,high-low+1);ctx.font='10px "Segoe UI", sans-serif';
    for(let pitch=low;pitch<=high;pitch++){
      const y=125+(high-pitch)*row,black=[1,3,6,8,10].includes(pitch%12);
      ctx.fillStyle=black?canvasTheme.rowDark:canvasTheme.row;ctx.fillRect(left,y,available,Math.max(1,row-.5));
      ctx.fillStyle=black?canvasTheme.keyDark:canvasTheme.key;ctx.fillRect(3,y,36,Math.max(1,row-.6));
      if(pitch%12===0){ctx.fillStyle=canvasTheme.text;ctx.fillText(`C${pitch/12-1}`,8,y+row-1);}}
    const step=visible>180?30:visible>60?10:visible>20?5:1;
    for(let seconds=Math.ceil(viewStart/step)*step;seconds<=viewStart+visible;seconds+=step){
      const px=x(seconds*rate);ctx.strokeStyle=canvasTheme.grid;ctx.beginPath();ctx.moveTo(px,25);ctx.lineTo(px,height);ctx.stroke();ctx.fillStyle=canvasTheme.text;ctx.fillText(time(seconds),px+4,16);}
    wave.forEach((channels,i)=>{const px=x(i/wave.length*duration*rate);if(px<left||px>width)return;
      const[min,max]=channels[0];ctx.strokeStyle='#8e8479';ctx.beginPath();ctx.moveTo(px,72+min*29);ctx.lineTo(px,72+max*29);ctx.stroke();});
    ctx.save();ctx.beginPath();ctx.rect(left,118,available,height-118);ctx.clip();
    for(const note of notes){const px=x(note.start_sample),w=(note.end_sample-note.start_sample)/rate/visible*available;if(px+w<left||px>width)continue;
      ctx.fillStyle=colors[Math.max(0,tracks.findIndex(t=>t.id===note.track_id))%colors.length];ctx.globalAlpha=selected==='all'?.76:.94;
      ctx.fillRect(px,125+(high-note.pitch)*row,Math.max(1.6,w),Math.max(1.5,row-1));}ctx.restore();ctx.globalAlpha=1;
    if(loop){ctx.fillStyle=canvasTheme.selection;ctx.fillRect(x(loopStart*rate),25,(loopEnd-loopStart)/visible*available,height);}
    const cursor=x(position*rate);if(cursor>=left&&cursor<=width){ctx.strokeStyle=canvasTheme.playhead;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(cursor,24);ctx.lineTo(cursor,height);ctx.stroke();ctx.fillStyle=canvasTheme.playhead;ctx.fillRect(cursor-3,22,6,5);}
  },[canvas,width,displayScale,duration,rate,notes,tracks,position,zoom,viewStart,wave,loop,loopStart,loopEnd,selected]);

  useEffect(()=>{if(selected!=='all'&&!tracks.some(t=>t.id===selected))setSelected('all');},[tracks,selected]);
  const engineProblem=Object.values(player.current.state.plugins??{}).find(p=>p.status==='failed')?.error??player.current.state.control_error;
  return <section className={'studio '+(mixing?'is-mixing ':'')+(focusEditor?'is-focused':'')+(chatFocused?' is-chat-focused':'')} aria-label="音乐工作台" data-revision={revision.id} data-position={position.toFixed(5)} data-playing={playing} data-audio-clock="native" data-engine-status={player.current.state.status} data-engine-revision={player.current.state.applied_revision??''} data-live-ready={Object.entries(player.current.state.plugins??{}).filter(([,p])=>p.status==='ready').map(([id])=>id).join(',')} data-live-errors={Object.keys(player.current.liveErrors).length} data-live-levels={JSON.stringify(livePeaks)}>
    <div className="studio-toolbar" onPointerDown={dragWindow}><button className="studio-back" aria-label="返回音乐库" onClick={onBack}><Icon name="back" size={15}/></button><span className="studio-song-title">{title}</span><div className="transport-buttons"><button className="icon-button" aria-label="回到开头" onClick={()=>void cue({position_sample:0})}><Icon name="start"/></button><button className="play-button" aria-label={playing?'暂停':'播放'} disabled={loading} onClick={()=>void playToggle()}>{loading?'···':<Icon name={playing?'pause':'play'} size={16}/>}</button><button className={'icon-button '+(loop?'is-active':'')} aria-label="循环" aria-pressed={loop} onClick={()=>void cue({loop:!loop})}><Icon name="loop"/></button></div><div className="clock"><strong>{time(position)}</strong><span>/ {time(mode==='original'?duration:Math.max(duration,player.current.duration))}</span></div><div className="studio-stages" role="navigation" aria-label="制作阶段"><button aria-pressed={!mixing} onClick={()=>setMixing(false)}>编曲</button><button aria-pressed={mixing} onClick={()=>{setVersionId('head');setFocusEditor(false);setChatFocused(false);setMixing(true);if(mode!=='mix')void cue({mode:'mix',track_ids:[]});}}>混音</button><span>{mixing?'独立混音 · 手动保存':(snapshot.listening_calibration?'原曲参考响度 · 即时调音量':'音量即时调节 · 保存版本时记录')}</span>{mixing&&<><button disabled={mixSaving||!mixDirty} onClick={()=>void saveMixProfile().catch(e=>setError(e.message))}>{mixSaving?'保存中…':'保存混音'}</button><button disabled={loading||exportBusy||editingBusy||!mixProfile} onClick={()=>void render()}>{exportBusy?'导出中…':'导出最终混音'}</button></>}</div><span className="toolbar-divider"/><div className="mode-switch" aria-label="试听方式"><button aria-pressed={mode==='original'} disabled={!snapshot.source_asset_id} onClick={()=>{setSelected('all');setMixing(false);if(inspector==='sounds')setInspector('discussion');void cue({mode:'original'});}}>原曲</button>{referenceId&&<button aria-pressed={mode==='reference'} onClick={()=>void cue({mode:'reference'})}>参考 <span>A</span></button>}<button aria-pressed={mode==='mix'}  onClick={()=>{setSelected('all');void cue({mode:'mix'});}}>重建 <span>B</span></button></div><button className="soft-button" aria-pressed={focusEditor} onClick={()=>{setChatFocused(false);setFocusEditor(!focusEditor);}}>{focusEditor?'恢复侧栏':'专注编辑'}</button><details className="transport-popover"><summary>循环范围</summary><div className="loop-editor"><label>循环起点 <input aria-label="循环起点秒" type="number" min={0} max={duration} step={.1} value={+loopStart.toFixed(2)} onChange={e=>setLoopStart(+e.target.value)}/></label><span>—</span><label>终点 <input aria-label="循环终点秒" type="number" min={0} max={duration} step={.1} value={+loopEnd.toFixed(2)} onChange={e=>setLoopEnd(+e.target.value)}/></label><button disabled={!Number.isFinite(loopStart)||!Number.isFinite(loopEnd)||loopStart<0||loopEnd>duration||loopStart>=loopEnd} title="起点须早于终点，终点不超过歌曲结尾" onClick={()=>void cue({loop_start_sample:Math.round(loopStart*rate),loop_end_sample:Math.round(loopEnd*rate),loop:true})}>应用循环</button></div></details><div className="zoom-control"><button aria-label="缩小时间轴" onClick={()=>viewport(zoom/1.5)}>−</button><input aria-label="时间轴缩放" type="range" min={0} max={8} step={.05} value={Math.log2(zoom)} onChange={e=>viewport(2**+e.target.value)}/><button aria-label="放大时间轴" onClick={()=>viewport(zoom*1.5)}>+</button><button onClick={()=>{setZoom(1);setViewStart(0);}}>整曲</button><button onClick={()=>{const begin=loopStart,finish=loopEnd||duration;if(finish>begin){const z=Math.min(256,duration/(finish-begin));setZoom(z);setViewStart(Math.min(begin,duration-duration/z));}}}>选区</button></div><WindowControls/></div>
    {(error||engineProblem)&&<div className="studio-message" role="status">{error||engineProblem}<button aria-label="关闭工作台提示" onClick={()=>setError('')}>×</button></div>}
    {mode==='tracks'&&<div className="audition-filter" role="status"><span>仅试听 {trackIds.length}/{tracks.length} 条声部{tracks.some(t=>!trackIds.includes(t.id))?' · 未播放：'+tracks.filter(t=>!trackIds.includes(t.id)).map(instrumentName).join('、'):''}</span><button onClick={()=>void cue({mode:'mix',track_ids:[]})}>返回完整混音</button></div>}
    {trackMenu&&<ContextMenu x={trackMenu.x} y={trackMenu.y} onClose={()=>setTrackMenu(null)} items={[
      {label:'打开钢琴卷帘',action:()=>{setSelected(trackMenu.id);setPianoRequest(n=>n+1);}},
      {label:'新增乐器轨道',disabled:!atHead||editingBusy,action:()=>requestTrack('add')},
      {label:'上移轨道',disabled:!atHead||editingBusy||tracks.findIndex(t=>t.id===trackMenu.id)<=0,action:()=>requestTrack('up',trackMenu.id)},
      {label:'下移轨道',disabled:!atHead||editingBusy||tracks.findIndex(t=>t.id===trackMenu.id)===tracks.length-1,action:()=>requestTrack('down',trackMenu.id)},
      {label:'删除轨道',danger:true,disabled:!atHead||editingBusy,action:()=>requestTrack('remove',trackMenu.id)},
    ]}/>}
    <div className="studio-body resizable-studio" ref={body} style={{'--track-width':leftWidth+'px','--ai-width':rightWidth+'px'} as CSSProperties}><aside className="track-panel"><div className="panel-heading"><span>音轨</span><button aria-label="添加乐器轨道" hidden={mode==='original'} disabled={mode==='original'||!atHead||editingBusy} onClick={()=>requestTrack('add')}>＋</button></div><button className={'all-tracks '+(selected==='all'?'is-selected':'')} onClick={()=>setSelected('all')}>全部声部 <small>{mode==='original'?`${sourceReference?.tracks.filter(t=>t.kind==='audio').length??1} 音频`:(snapshot.notes??[]).length.toLocaleString()}</small></button>
      <div className="track-cards">{displayedTracks.map((track,i)=>{const values=originalView?originalMix:mixing?(mixProfile?.tracks??{}):mixDraft,state=audibility(displayedTracks.filter(t=>t.kind!=='midi'),values,track.id),gain=values[track.id]?.gain_db??track.gain_db??0;return <div className={'track-card '+(selected===track.id?'is-selected ':'')+(state.muted?'is-muted':'')+(originalView&&track.kind==='midi'?' is-reference-midi':'')} key={track.id} style={{'--track-color':colors[i%colors.length]} as CSSProperties} data-track={track.id} onContextMenu={e=>{if(originalView){e.preventDefault();return;}if((e.target as Element).closest('input,textarea'))return;e.preventDefault();setTrackMenu({x:e.clientX,y:e.clientY,id:track.id});}} data-effective-muted={state.muted}>
        <button className="track-select" title={instrumentName(track)+(originalView?'':' · 双击打开卷帘')} onClick={()=>setSelected(track.id)} onDoubleClick={()=>{setSelected(track.id);if(!originalView)setPianoRequest(n=>n+1);}}><span className="track-index">{(i+1).toString().padStart(2,'0')}</span><div><strong>{instrumentName(track)}</strong><small>{instrumentLabels.presets[(snapshot.plugins??[]).find(p=>p.track_id===track.id&&p.order===0)?.preset_id??'']??(snapshot.plugins??[]).find(p=>p.track_id===track.id&&p.order===0)?.preset_name??(track.kind==='audio'?'音频波形':originalView?'旋律参考 · 不发声':'音色')}</small></div></button>
        <button hidden={originalView} className="track-more" aria-label={'轨道菜单 '+(i+1)} onClick={e=>{const r=e.currentTarget.getBoundingClientRect();setTrackMenu({x:r.left,y:r.bottom,id:track.id});}}>⋯</button><div className="track-actions">{player.current.state.plugins?.[track.id]?.status==='loading'&&<small className="track-loading" role="status">{['启动音源…','读取音源…','恢复音色…','准备播放…'][Math.min(3,player.current.state.plugins[track.id].load_stage??0)]}</small>}<small className="track-note-count">{state.soloMuted?'独奏排除':state.manualMute?'已静音':track.kind==='audio'?'音频':(originalView?(sourceReference.notes??[]):(snapshot.notes??[])).filter(n=>n.track_id===track.id).length.toLocaleString()+' 音符'}</small><span className="track-color-line"/><button aria-label={`独奏 ${i+1}`} aria-pressed={state.solo} disabled={versionId!=='head'||originalView&&track.kind==='midi'} onClick={()=>changeMix(track.id,{solo:!state.solo})}>独奏</button><button aria-label={`试听静音 ${i+1}`} aria-pressed={state.muted} data-muted-by-solo={state.soloMuted} title={state.soloMuted?'其他轨道独奏时暂时静音；取消独奏恢复':'静音此轨'} disabled={versionId!=='head'||originalView&&track.kind==='midi'} onClick={()=>changeMix(track.id,{mute:!state.manualMute})}>静音</button></div>
        <div className="track-volume"><label htmlFor={'volume-'+track.id}>音量</label><input id={'volume-'+track.id} aria-label={`音轨音量 ${i+1}`} type="range" min={-60} max={12} step={.1} value={Math.max(-60,Math.min(12,gain))} disabled={versionId!=='head'||originalView&&track.kind==='midi'} onChange={e=>changeMix(track.id,{gain_db:+e.target.value})}/><input aria-label={`音轨音量数值 ${i+1}`} type="number" min={-120} max={24} step={.1} value={+gain.toFixed(1)} disabled={versionId!=='head'||originalView&&track.kind==='midi'} onChange={e=>{if(Number.isFinite(e.target.valueAsNumber))changeMix(track.id,{gain_db:Math.max(-120,Math.min(24,e.target.valueAsNumber))});}}/><span>dB</span></div>
      </div>;})}</div>
      </aside><PanelResize label="调整声部栏宽度" value={leftWidth} min={140} max={Math.min(480,Math.max(140,bodyWidth-rightWidth-332))} onChange={setTrackWidth} reset={190}/>
      <div className="arrangement">
        {mode==='original'&&!sourceReference?<div className="source-unprepared"><strong>原曲编排尚未生成</strong><p>按新流程分离人声、识别旋律与和弦，并建立新的重建工程。</p><button disabled={!onReimport||copyingSource} onClick={()=>{setCopyingSource(true);void onReimport?.().catch(e=>setError(e.message)).finally(()=>setCopyingSource(false));}}>{copyingSource?'正在建立…':'按新流程建立副本'}</button><small>当前工程保留。</small></div>:originalView&&sourceReference?<SourceArrangement revision={revision} reference={sourceReference} selected={selected} onSelect={setSelected} start={Math.round(viewStart*rate)} end={Math.round(Math.min(duration,viewStart+duration/zoom)*rate)} position={Math.round(position*rate)} onSeek={sample=>void cue({position_sample:sample})} onViewport={(factor,anchor,pan)=>{if(pan!==undefined)setViewStart(Math.max(0,Math.min(duration-duration/zoom,viewStart+pan*duration/zoom)));else viewport(zoom*factor,anchor);}} onUpdate={updateSource} busy={editingBusy} editable={atHead}/>:<DawEditor trackRequest={trackRequest} mixingOnly={mixing} livePeaks={livePeaks} liveMix={mixing?mixProfile?.tracks:mixDraft} onMixChange={changeMix} mixSaving={mixSaving} onViewport={(factor,anchor,pan)=>{if(pan!==undefined)setViewStart(Math.max(0,Math.min(duration-duration/zoom,viewStart+pan*duration/zoom)));else viewport(zoom*factor,anchor);}} pianoRequest={pianoRequest} revision={revision} selected={selected} onSelect={setSelected} editable={atHead} busy={editingBusy||mixSaving} onEdit={manualEdit} onUndo={()=>manualRestore()} onRedo={()=>manualRestore(true)} canUndo={!!undoStack.length} canRedo={!!redoStack.length} start={Math.round(viewStart*rate)} end={Math.round(Math.min(duration,viewStart+duration/zoom)*rate)} position={Math.round(position*rate)} selectionStart={Math.round(loopStart*rate)} selectionEnd={Math.round((loopEnd||duration)*rate)} onSeek={sample=>void cue({position_sample:sample})} overview={<div className="roll-frame"><div className="roll-caption"><span>WAVEFORM</span><span>源样本时间 · 演奏时刻保留</span></div><canvas ref={setCanvas} data-waveform-buckets={wave.length} className="piano-roll" role="img" aria-label="整曲波形与钢琴卷帘" onClick={e=>{const b=e.currentTarget.getBoundingClientRect(),seconds=viewStart+Math.max(0,Math.min(1,(e.clientX-b.left-46)/(b.width-58)))*duration/zoom;void cue({position_sample:Math.round(Math.min(duration,seconds)*rate)});}}/><div className="roll-footer"><span>PIANO ROLL</span><span>{notes.length.toLocaleString()} NOTES <i/> {rate/1000} kHz</span></div></div>}/>}

        <input className="timeline-pan" aria-label="时间轴平移" type="range" min={0} max={Math.max(0,duration-duration/zoom)} step={.05} value={viewStart} onChange={e=>setViewStart(+e.target.value)}/></div>
      <PanelResize label="调整 AI 侧栏宽度" value={rightWidth} min={260} max={Math.min(900,Math.max(260,bodyWidth-leftWidth-332))} onChange={setAiWidth} reverse reset={340}/><aside className="inspector"><div className="inspector-tabs" role="tablist" aria-label="侧栏内容" onKeyDown={e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;const tabs=Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('[role=tab]')),index=tabs.indexOf(e.target as HTMLButtonElement);if(index<0)return;e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(index+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].focus();tabs[next].click();}}>{[['discussion','讨论'],['sounds','音色与效果'],['versions','版本'],['analysis','分析']].filter(([id])=>mode!=='original'||id!=='sounds').map(([id,label])=><button role="tab" key={id} tabIndex={inspector===id?0:-1} aria-selected={inspector===id} aria-pressed={inspector===id} onClick={()=>{setInspector(id);if(id!=='discussion')setChatFocused(false);}}><Icon name={id==='discussion'?'chat':id==='sounds'?'mixer':id==='versions'?'history':'analysis'}/>{label}</button>)}</div>
        <div className={'inspector-content '+(inspector==='discussion'?'has-discussion':inspector==='sounds'?'has-sounds':'')}>{inspector==='discussion'?<Discussion workspace={mode==='original'?'original':'arrangement'} referenceTrackId={originalView&&selected!=='all'?selected:null} expanded={chatFocused} onToggleExpand={()=>{setFocusEditor(false);setChatFocused(value=>!value);}} revision={revision} headId={head.id} instrumentDraft={null} jobs={jobs} start={loopStart} end={loopEnd||duration} trackId={originalView||selected==='all'?null:selected} onLocate={locate} onCompare={id=>{void compare(id);setChatFocused(false);setInspector('versions');}}/>:inspector==='analysis'?<><div className="inspector-section"><h3>分析概览</h3><div className="stat-grid"><div><strong>{tracks.length}</strong><span>声部</span></div><div><strong>{snapshot.timing_map?.anchors.length??0}</strong><span>拍点</span></div><div><strong>{versions.length}</strong><span>版本</span></div></div></div><details className="inspector-section issues"><summary>待核对与来源 <span>{issues.length}</span></summary><ul>{issues.map((issue,i)=><li key={i}>{issue}</li>)}</ul></details><div className="inspector-section"><span className="mini-label">重建音频</span><p className="render-ready" data-render-status={renderJob?.status??'none'}>{liveLoading.length?'◌ 正在载入 '+liveLoading.map(id=>instrumentName(tracks.find(t=>t.id===id)!)).join('、'):player.current.state.status==='ready'?'● 本地引擎就绪':renderBusy?'◌ 正在生成此版本的音频':renderJob?.status==='failed'?'× 渲染失败 · '+String(renderJob.error?.code):publication?'● 此版本的音频已生成':'○ 此版本等待渲染'}</p><small>编辑只更新相关音轨；导出时才生成整曲混音。</small></div></>:
        inspector==='sounds'?<SoundPanel revision={revision} selected={selected} onSelect={setSelected} pluginInfo={pluginInfo} metadataError={pluginInfoError} onReloadMetadata={()=>setPluginInfoEpoch(value=>value+1)} enabled={!originalView&&selected!=='all'&&atHead&&!editingBusy} change={soundChange} onChoose={(choice,targetId)=>void chooseManualPreset(choice,targetId)} onRetry={()=>{const attempt=soundAttempt.current;if(attempt)void chooseManualPreset(attempt.choice,attempt.targetId,attempt.trackId,true);}}/>:
        <div className="inspector-section"><h3>已保存版本</h3><div className="save-version-row"><input aria-label="版本名称" placeholder="版本名称（可选）" value={savedTitle} onChange={e=>setSavedTitle(e.target.value)}/><button disabled={savingVersion||mixSaving||editingBusy} onClick={()=>void saveVersion()}>保存版本</button></div>{!versions.length&&<p className="muted">点击保存版本，记录此时的工程。平时的修改会自动保存。</p>}<label className="version-select">查看版本<select aria-label="工程版本" value={versionId} onChange={e=>setVersionId(e.target.value)}><option value="head">当前工程</option>{versions.map(v=><option key={v.id} value={v.revision_id}>{v.title} · {new Date(v.created_at*1000).toLocaleTimeString()}</option>)}</select></label>{versions.map(v=><div className={'version-card '+(v.revision_id===revision.id?'is-current':'')} key={v.id}><div><i/><span>{v.title}</span><small>{new Date(v.created_at*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}</small></div><button onClick={()=>setVersionId(v.revision_id)}>查看</button><button onClick={()=>void compare(v.revision_id)}>比较差异</button><button onClick={()=>void cue({mode:'reference',reference_revision_id:v.revision_id})}>设为 A</button></div>)}{difference&&<div className="version-diff">{Object.entries(difference).filter(([name])=>name!=='settings').map(([name,v])=><p key={name}>{name} <span>+{v.added.length} −{v.removed.length} Δ{v.changed.length}</span></p>)}</div>}</div>}
      </div></aside></div><div className="studio-bottom"><span>空格：播放 / 暂停</span><span>{mode==='original'?'点击波形定位 · 点击旋律音符修正':mixing?'混音设置单独保存':'双击声部进入钢琴卷帘'}</span></div>
  </section>;
}
