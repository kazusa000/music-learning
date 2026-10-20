import {useEffect,useLayoutEffect,useMemo,useRef,useState} from 'react';
import type {PointerEvent as ReactPointerEvent,KeyboardEvent,ReactNode} from 'react';
import type {Clip,NoteEvent,Revision,Track} from '../../../packages/contracts/types';
import type {EditOperation} from './dawEditing';
import {PanelResize,usePanelSize} from './PanelResize';
import {trackColors as palette,canvasTheme} from './visual';
import {Icon} from './Icon';
import type {MixValues} from './liveMix';
import {audibility} from './liveMix';
import {arrangeClipEdits,beatSample,gridLines,orderedTracks,putNote,removeTrack,sampleBeat,snapSample,trackName,userNote,uuid} from './dawEditing';
import {ContextMenu} from './ContextMenu';
import type {MenuItem} from './ContextMenu';
import {crossesRect} from './dawMouse';

export type EditRequest={operations:EditOperation[];trackIds:string[];structural?:boolean;description:string};
type Gesture={kind:'move'|'resize'|'draw'|'box'|'velocity';revision:string;x:number;y:number;notes:NoteEvent[];preview:NoteEvent[];moved:boolean;endX:number;endY:number};
const label=(pitch:number)=>['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'][pitch%12]+(Math.floor(pitch/12)-1);
const left=48,top=26;

export function DawEditor({revision,selected,onSelect,editable,busy,onEdit,onUndo,onRedo,canUndo,canRedo,start,end,position,selectionStart,selectionEnd,onSeek,overview,pianoRequest=0,onViewport,liveMix,onMixChange,mixSaving=false,livePeaks={},mixingOnly=false,trackRequest}:{
  trackRequest?:{sequence:number;action:string;trackId?:string};
  mixingOnly?:boolean;livePeaks?:Record<string,number>;liveMix?:Record<string,MixValues>;onMixChange?:(id:string,patch:MixValues)=>void;mixSaving?:boolean;
  revision:Revision;selected:string;onSelect:(id:string)=>void;editable:boolean;busy:boolean;
  onEdit:(request:EditRequest)=>Promise<void>;onUndo:()=>Promise<void>;onRedo:()=>Promise<void>;canUndo:boolean;canRedo:boolean;
  start:number;end:number;position:number;selectionStart:number;selectionEnd:number;onSeek:(sample:number)=>void;overview:ReactNode;pianoRequest?:number;onViewport:(factor:number,anchor:number,pan?:number)=>void;
}){
  const s=revision.snapshot,rate=s.sample_rate??44100,duration=s.duration_samples??0;
  const tracks=useMemo(()=>orderedTracks(s),[s]),track=tracks.find(t=>t.id===selected);
  const [row,setRow]=usePanelSize('piano.row-height',14,8,40);
  const [pianoHeight,setPianoHeight]=usePanelSize('piano.panel-height',386,180,1200);
  const [velocityHeight,setVelocityHeight]=usePanelSize('piano.velocity-height',126,60,400);
  const [pitchScroll,setPitchScroll]=useState(0),[pianoViewportHeight,setPianoViewportHeight]=useState(386);
  const [view,setView]=useState('playlist'),[tool,setTool]=useState('pencil'),[division,setDivision]=useState(.25);
  useEffect(()=>{if(pianoRequest&&!mixingOnly)setView('piano');},[pianoRequest]);
  useEffect(()=>{setView(mixingOnly?'mixer':'playlist');},[mixingOnly]);
  const [noteIds,setNoteIds]=useState<string[]>([]),[gesture,setGesture]=useState<Gesture|null>(null);
  const liveGesture=useRef<Gesture|null>(null),copyBuffer=useRef<NoteEvent[]>([]),frame=useRef<HTMLDivElement>(null),pianoScroll=useRef<HTMLDivElement>(null);
  const [width,setWidth]=useState(800),[error,setError]=useState(''),[localMix,setMix]=useState<Record<string,Partial<Track>>>({});
  const mix=liveMix??localMix;
  const [pendingTrack,setPendingTrack]=useState<{id:string;name:string}|null>(null);
  const [menu,setMenu]=useState<{x:number;y:number;items:MenuItem[]}|null>(null);
  const [erasing,setErasing]=useState<string[]>([]);
  const pointerMode=useRef<{kind:'pan'|'erase';revision:string;x:number;y:number;svg?:SVGSVGElement;scroll?:HTMLElement;ids:Set<string>;collection?:'notes'|'clips'}|null>(null);
  const playlistScroll=useRef<HTMLDivElement>(null),trackSequence=useRef(0);
  const [fields,setFields]=useState({pitch:60,start:0,end:1,velocity:80});
  const [clipId,setClipId]=useState<string|null>(null),[clipPreview,setClipPreview]=useState<Clip|null>(null);
  const clipDrag=useRef<{clip:Clip;x:number;resize:boolean;revision:string;preview:Clip}|null>(null),dragTrack=useRef<string|null>(null);
  const span=Math.max(1,end-start),gridLeft=view==='playlist'?0:left,laneWidth=Math.max(240,width-(view==='playlist'?126:0)),pixels=laneWidth-gridLeft-10;
  const x=(sample:number)=>gridLeft+(sample-start)/span*pixels;
  const sample=(pixel:number)=>Math.round(start+(pixel-gridLeft)/pixels*span);
  const y=(pitch:number)=>top+(127-pitch)*row;
  const localNotes=(s.notes??[]).filter(n=>n.track_id===selected);
  const selectedNotes=localNotes.filter(n=>noteIds.includes(n.id));
  const activeNote=selectedNotes.length===1?selectedNotes[0]:null;
  const visibleNotes=localNotes.filter(n=>n.end_sample>start&&n.start_sample<end);
  const lines=useMemo(()=>gridLines(s,start,end,division),[s,start,end,division]);
  const labelLines=useMemo(()=>{let previous=-Infinity;return lines.filter(l=>{if(!l.major||(l.sample-previous)/span*pixels<38)return false;previous=l.sample;return true;});},[lines,span,pixels]);
  const labelSamples=useMemo(()=>new Set(labelLines.map(l=>l.sample)),[labelLines]);
  // Decimate only the painted grid at song scale; snapping still uses every musical subdivision.
  const displayLines=useMemo(()=>{let previous=-Infinity;const minorPixels=lines.length>1?(lines[1].sample-lines[0].sample)/span*pixels:0;return lines.filter(l=>{const px=(l.sample-start)/span*pixels;if(labelSamples.has(l.sample)){previous=px;return true;}if((!l.major&&minorPixels<8)||px-previous<12)return false;previous=px;return true;});},[lines,labelSamples,start,span,pixels]);
  // Stable note paths keep the full-song preview cheap while the playhead animates.
  const playlistRows=useMemo(()=>new Map(tracks.map(t=>{
    const clips=(s.clips??[]).filter(c=>c.track_id===t.id),free=(s.notes??[]).filter(n=>n.track_id===t.id&&!n.clip_id&&n.end_sample>start&&n.start_sample<end);
    const low=free.reduce((v,n)=>Math.min(v,n.pitch),127),high=free.reduce((v,n)=>Math.max(v,n.pitch),0),pitchSpan=Math.max(12,high-low+1);
    const glyphs=new Map<string,{x:number;y:number;w:number}>();
    for(const n of free){const nx=Math.max(gridLeft,gridLeft+(n.start_sample-start)/span*pixels),ny=51-(n.pitch-low)/pitchSpan*27,nw=Math.max(1.5,gridLeft+(Math.min(end,n.end_sample)-start)/span*pixels-nx);const key=Math.round(nx)+':'+Math.round(ny),old=glyphs.get(key);if(!old||nw>old.w)glyphs.set(key,{x:nx,y:ny,w:nw});}
    const notesPath=Array.from(glyphs.values(),g=>`M${g.x.toFixed(1)},${g.y.toFixed(1)}h${g.w.toFixed(1)}v2h-${g.w.toFixed(1)}Z`).join('');
    return [t.id,{clips,count:free.length,notesPath}] as const;
  })),[s,tracks,start,end,span,pixels,gridLeft]);
  const chosenClip=(s.clips??[]).find(c=>c.id===clipId);
  const noteEditing=editable&&!busy&&!!track&&['midi','instrument'].includes(track.kind);
  const wheelAction=useRef(onViewport);wheelAction.current=onViewport;
  const pitchAction=useRef(pitchZoom);pitchAction.current=pitchZoom;
  useEffect(()=>{const node=frame.current;if(!node)return;
    const wheel=(event:WheelEvent)=>{
      if(liveGesture.current||clipDrag.current)return;
      if((event.target as HTMLElement).closest('input,select,textarea'))return;
      const bounds=node.getBoundingClientRect(),anchor=Math.max(0,Math.min(1,(event.clientX-bounds.left-left)/(bounds.width-left-10)));
      if(event.altKey&&(event.target as HTMLElement).closest('.daw-piano-area')){event.preventDefault();pitchAction.current(rowValue.current*Math.exp(-Math.max(-150,Math.min(150,event.deltaY))*.006),event.clientY);}
      else if(event.ctrlKey||event.metaKey){event.preventDefault();wheelAction.current(Math.exp(-Math.max(-150,Math.min(150,event.deltaY))*.008),anchor);}
      else if(event.shiftKey||Math.abs(event.deltaX)>Math.abs(event.deltaY)){event.preventDefault();wheelAction.current(1,anchor,(event.shiftKey?event.deltaY:event.deltaX)/Math.max(540,bounds.width));}
    };
    node.addEventListener('wheel',wheel,{passive:false});return()=>node.removeEventListener('wheel',wheel);
  },[]);
  useEffect(()=>{const node=pianoScroll.current;if(!node)return;const observer=new ResizeObserver(()=>setPianoViewportHeight(node.clientHeight));observer.observe(node);return()=>observer.disconnect();},[view,selected]);
  const rowValue=useRef(row);rowValue.current=row;
  const pendingPitchScroll=useRef<number|null>(null);
  useLayoutEffect(()=>{if(pendingPitchScroll.current!==null&&pianoScroll.current){pianoScroll.current.scrollTop=pendingPitchScroll.current;pendingPitchScroll.current=null;}},[row]);
  function pitchZoom(value:number,clientY?:number){const node=pianoScroll.current,anchor=node?(clientY===undefined?node.clientHeight/2:Math.max(0,Math.min(node.clientHeight,clientY-node.getBoundingClientRect().top))):0;
    const at=node?(node.scrollTop+anchor-top)/row:0,next=Math.round(Math.max(8,Math.min(40,value)));
    if(node){const target=top+at*next-anchor;if(next===row)node.scrollTop=target;else pendingPitchScroll.current=target;}setRow(next);}
  function scrollPitch(value:number){pendingPitchScroll.current=null;const node=pianoScroll.current;if(node){node.scrollTop=value;setPitchScroll(node.scrollTop);}}
  function fitPitch(){const node=pianoScroll.current,notes=visibleNotes.length?visibleNotes:localNotes;if(!node||!notes.length)return;
    const high=Math.max(...notes.map(n=>n.pitch)),low=Math.min(...notes.map(n=>n.pitch)),next=Math.round(Math.max(8,Math.min(40,(node.clientHeight-48)/(high-low+3))));
    const target=Math.max(0,top+(126-high)*next);if(next===row)node.scrollTop=target;else pendingPitchScroll.current=target;setRow(next);}
  useEffect(()=>{const node=frame.current;if(!node)return;const observer=new ResizeObserver(entries=>setWidth(entries[0].contentRect.width));observer.observe(node);return()=>observer.disconnect();},[]);
  useEffect(()=>{if(liveGesture.current||clipDrag.current)setError('工程版本已改变，未提交拖动，请重试。');liveGesture.current=null;clipDrag.current=null;setGesture(null);setClipPreview(null);setMix({});pointerMode.current=null;setErasing([]);setMenu(null);},[revision.id]);
  useEffect(()=>{setNoteIds([]);setClipId(current=>(s.clips??[]).find(c=>c.id===current)?.track_id===selected?current:null);liveGesture.current=null;setGesture(null);},[selected]);
  useEffect(()=>{if(activeNote)setFields({pitch:activeNote.pitch,start:activeNote.start_sample/rate,end:activeNote.end_sample/rate,velocity:Math.round(activeNote.velocity*100)});},[activeNote?.id,activeNote?.pitch,activeNote?.start_sample,activeNote?.end_sample,activeNote?.velocity,rate]);
  useEffect(()=>{if(view!=='piano'||!pianoScroll.current)return;const first=localNotes.find(n=>n.end_sample>start)??localNotes[0];pianoScroll.current.scrollTop=Math.max(0,y(first?.pitch??60)-140);},[selected,view]);
  async function submit(operations:EditOperation[],description:string,structural=false,ids=track?[track.id]:[]){
    if(!editable||busy)return false;setError('');try{await onEdit({operations,description,structural,trackIds:ids});return true;}catch(e){setError((e as Error).message);return false;}
  }
  const changeNotes=(notes:NoteEvent[],description:string)=>submit(notes.map(putNote),description);
  function point(e:ReactPointerEvent<SVGSVGElement>){const b=e.currentTarget.getBoundingClientRect();return{x:(e.clientX-b.left)*e.currentTarget.viewBox.baseVal.width/b.width,y:(e.clientY-b.top)*e.currentTarget.viewBox.baseVal.height/b.height};}
  function begin(e:ReactPointerEvent<SVGSVGElement>){
    if(e.button!==0)return;frame.current?.focus({preventScroll:true});
    const p=point(e),hit=(e.target as SVGElement).closest<SVGElement>('[data-note-id]');
    const hitNote=hit&&localNotes.find(n=>n.id===hit.dataset.noteId);
    if(p.y<top){onSeek(Math.max(0,Math.min(duration,sample(p.x))));return;}
    if(hitNote){
      if(tool==='erase'&&noteEditing){void submit([{op:'delete',collection:'notes',id:hitNote.id}],'手动删除音符');return;}
      const ids=e.shiftKey?(noteIds.includes(hitNote.id)?noteIds.filter(id=>id!==hitNote.id):[...noteIds,hitNote.id]):noteIds.includes(hitNote.id)?noteIds:[hitNote.id];
      setNoteIds(ids);if(!noteEditing||e.shiftKey)return;
      const notes=localNotes.filter(n=>ids.includes(n.id));
      const kind=hit.dataset.action==='resize'?'resize':hit.dataset.action==='velocity'?'velocity':'move';
      const g:Gesture={kind,revision:revision.id,x:p.x,y:p.y,endX:p.x,endY:p.y,notes:kind==='resize'?[hitNote]:notes,preview:[],moved:false};
      if(kind==='velocity'){g.preview=notes.map(n=>userNote(n,revision.id,{velocity:Math.min(1,Math.max(.01,1-(p.y-top)/100))}));g.moved=true;}
      liveGesture.current=g;setGesture(g);e.currentTarget.setPointerCapture(e.pointerId);return;
    }
    if(p.x<left)return;
    if(tool==='pencil'&&noteEditing&&!e.ctrlKey&&!e.metaKey){
      const at=snapSample(sample(p.x),s,division),pitch=Math.max(0,Math.min(127,127-Math.floor((p.y-top)/row)));
      const length=s.timing_map?Math.max(1,beatSample(Math.min(sampleBeat(duration,s.timing_map),sampleBeat(at,s.timing_map)+Math.max(division,.25)),s.timing_map)-at):Math.max(1,Math.round((division||.25)*rate));
      if(at>=duration)return;
      const note:NoteEvent={id:uuid(),track_id:selected,clip_id:null,pitch,start_sample:at,end_sample:Math.min(duration,at+length),velocity:.8,provenance:{origin:'user',source_id:'ui:'+revision.id,review:'unreviewed'}};
      const g:Gesture={kind:'draw',revision:revision.id,x:p.x,y:p.y,endX:p.x,endY:p.y,notes:[note],preview:[note],moved:false};
      liveGesture.current=g;setGesture(g);setNoteIds([note.id]);e.currentTarget.setPointerCapture(e.pointerId);
    }else{
      if(!e.shiftKey)setNoteIds([]);
      const g:Gesture={kind:'box',revision:revision.id,x:p.x,y:p.y,endX:p.x,endY:p.y,notes:[],preview:[],moved:false};
      liveGesture.current=g;setGesture(g);e.currentTarget.setPointerCapture(e.pointerId);
    }
  }
  function move(e:ReactPointerEvent<SVGSVGElement>){
    const g=liveGesture.current;if(!g)return;const p=point(e);let preview:NoteEvent[]=[];
    if(g.kind==='move'){
      const origin=g.notes[0],proposed=snapSample(origin.start_sample+(p.x-g.x)/pixels*span,s,division)-origin.start_sample;
      const delta=Math.min(duration-Math.max(...g.notes.map(n=>n.end_sample)),Math.max(-Math.min(...g.notes.map(n=>n.start_sample)),proposed));
      const pitchDelta=Math.min(127-Math.max(...g.notes.map(n=>n.pitch)),Math.max(-Math.min(...g.notes.map(n=>n.pitch)),Math.round((g.y-p.y)/row)));
      preview=g.notes.map(n=>userNote(n,revision.id,{start_sample:n.start_sample+delta,end_sample:n.end_sample+delta,pitch:n.pitch+pitchDelta}));
    }else if(g.kind==='resize'||g.kind==='draw'){
      const n=g.notes[0],finish=Math.min(duration,Math.max(n.start_sample+1,snapSample(sample(p.x),s,division)));
      preview=[userNote(n,revision.id,{end_sample:finish})];
    }else if(g.kind==='velocity')preview=g.notes.map(n=>userNote(n,revision.id,{velocity:Math.min(1,Math.max(.01,1-(p.y-top)/100))}));
    const next={...g,endX:p.x,endY:p.y,preview,moved:g.moved||Math.abs(p.x-g.x)+Math.abs(p.y-g.y)>2};liveGesture.current=next;setGesture(next);
  }
  function finish(e:ReactPointerEvent<SVGSVGElement>){
    const g=liveGesture.current;liveGesture.current=null;setGesture(null);if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);
    if(!g||g.revision!==revision.id)return;
    if(g.kind==='box'){const minX=Math.min(g.x,g.endX),maxX=Math.max(g.x,g.endX),minY=Math.min(g.y,g.endY),maxY=Math.max(g.y,g.endY);
      setNoteIds(localNotes.filter(n=>x(n.end_sample)>=minX&&x(n.start_sample)<=maxX&&y(n.pitch)+row>=minY&&y(n.pitch)<=maxY).map(n=>n.id));return;}
    if(!g.moved&&g.kind!=='draw')return;
    const notes=g.preview.length?g.preview:g.notes;
    if(g.kind==='draw'){
      const note=notes[0],clip=(s.clips??[]).find(c=>c.track_id===selected&&c.start_sample<=note.start_sample&&c.start_sample+c.duration_samples>=note.end_sample);
      if(clip?.loop){setError('循环片段内请先明确展开或修改原始内容，不能直接画入重复事件。');return;}
      if(clip)note.clip_id=clip.id;
    }
    void changeNotes(notes,g.kind==='draw'?'手动画入音符':g.kind==='resize'?'手动调整音符时值':g.kind==='velocity'?'手动调整力度':'手动移动音符');
  }
  function cancelGesture(){if(frame.current)delete frame.current.dataset.panning;pointerMode.current=null;setErasing([]);liveGesture.current=null;setGesture(null);clipDrag.current=null;setClipPreview(null);}
  function keyboard(e:KeyboardEvent){
    if((e.target as HTMLElement).closest('input,select,textarea,button'))return;
    const command=e.ctrlKey||e.metaKey;
    if(e.key==='Escape'){cancelGesture();setMenu(null);setNoteIds([]);return;}
    if(command&&e.key.toLowerCase()==='z'){e.preventDefault();if(editable&&!busy)void(e.shiftKey?onRedo():onUndo()).catch(err=>setError(err.message));return;}
    if(command&&e.key.toLowerCase()==='a'&&view==='piano'){e.preventDefault();setNoteIds(localNotes.map(n=>n.id));return;}
    if(command&&e.key.toLowerCase()==='c'&&view==='piano'){e.preventDefault();copyBuffer.current=selectedNotes;return;}
    if(command&&e.key.toLowerCase()==='v'&&view==='piano'&&noteEditing&&copyBuffer.current.length){e.preventDefault();const min=Math.min(...copyBuffer.current.map(n=>n.start_sample)),at=snapSample(position,s,division);
      const pasted=copyBuffer.current.map(n=>({...userNote(n,revision.id,{start_sample:n.start_sample-min+at,end_sample:n.end_sample-min+at,track_id:selected}),id:uuid(),clip_id:null}));
      void changeNotes(pasted,'粘贴音符').then(saved=>{if(saved)setNoteIds(pasted.map(n=>n.id));});return;}
    if((e.key==='Delete'||e.key==='Backspace')&&view==='piano'&&noteEditing&&selectedNotes.length){e.preventDefault();void submit(selectedNotes.map(n=>({op:'delete',collection:'notes',id:n.id})),'删除所选音符');}
  }
  async function group(){
    if(!track||!noteEditing)return;const notes=localNotes.filter(n=>n.start_sample<selectionEnd&&n.end_sample>selectionStart);
    if(!notes.length){setError('循环区间内没有可编组的音符。');return;}
    if(notes.some(n=>n.clip_id||n.start_sample<selectionStart||n.end_sample>selectionEnd)){setError('区间含已编组或跨越边界的音符。请调整区间；不会截断或重复分配。');return;}
    const id=uuid(),begin=Math.min(...notes.map(n=>n.start_sample)),finish=Math.max(...notes.map(n=>n.end_sample));
    if(await submit([{op:'put',collection:'clips',entity:{id,track_id:track.id,start_sample:begin,duration_samples:finish-begin,provenance:{origin:'user',source_id:'ui:'+revision.id,review:'unreviewed'}}},...notes.map(n=>putNote({...n,clip_id:id}))],'将区间音符编组为 MIDI 片段',true))setClipId(id);
  }
  function clipBegin(e:ReactPointerEvent<SVGSVGElement>,clip:Clip){
    setClipId(clip.id);onSelect(clip.track_id);frame.current?.focus({preventScroll:true});if(!editable||busy||e.button!==0)return;
    clipDrag.current={clip,x:e.clientX,resize:(e.target as SVGElement).dataset.action==='resize',revision:revision.id,preview:clip};e.currentTarget.setPointerCapture(e.pointerId);
  }
  function clipMove(e:ReactPointerEvent<SVGSVGElement>){const g=clipDrag.current;if(!g)return;
    const delta=(e.clientX-g.x)/pixels*span;
    const next=g.resize?{...g.clip,duration_samples:Math.max(1,snapSample(g.clip.start_sample+g.clip.duration_samples+delta,s,division)-g.clip.start_sample)}:{...g.clip,start_sample:Math.min(duration-g.clip.duration_samples,Math.max(0,snapSample(g.clip.start_sample+delta,s,division)))};
    g.preview=next;setClipPreview(next);
  }
  function clipFinish(e:ReactPointerEvent<SVGSVGElement>){const g=clipDrag.current;clipDrag.current=null;setClipPreview(null);if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);
    if(!g||g.revision!==revision.id)return;
    if(g.resize&&g.preview.duration_samples!==g.clip.duration_samples)void submit([{op:'arrange_clip',id:g.clip.id,action:'trim',sample:g.clip.start_sample,end_sample:g.clip.start_sample+g.preview.duration_samples}],'裁剪片段',true,[g.clip.track_id]);
    else if(g.preview.start_sample!==g.clip.start_sample)void arrangeClipEdits(s,g.clip,g.preview.start_sample,revision.id).then(ops=>submit(ops,'移动片段',true,[g.clip.track_id])).catch(e=>setError(e.message));
  }
  async function addTrack(){const id=uuid();const ops:EditOperation[]=[{op:'put',collection:'tracks',entity:{id,name:'新乐器 '+(tracks.length+1),kind:'instrument',provenance:{origin:'user',source_id:'ui:'+revision.id,review:'unreviewed'}}},{op:'track_order',track_ids:[...tracks.map(t=>t.id),id]}];
    if(s.routing){const known=new Set([...tracks.map(t=>t.id),...(s.plugins??[]).map(p=>p.id)]),masters=(s.routing.nodes??[]).filter(n=>!known.has(n));
      if(masters.length!==1){setError('无法确定输出主总线，请先核对路由。');return;}
      ops.push({op:'routing',routing:{...s.routing,nodes:[...(s.routing.nodes??[]),id],edges:[...(s.routing.edges??[]),{source:id,target:masters[0],kind:'main',gain_db:0}]}});
    }
    setPendingTrack({id,name:'新乐器 '+(tracks.length+1)});
    try{if(await submit(ops,'新增乐器轨道',true,[...tracks.map(t=>t.id),id]))onSelect(id);}
    finally{setPendingTrack(null);}
  }
  async function reorder(from:string,to:string){if(from===to)return;const order=tracks.map(t=>t.id);const a=order.indexOf(from),b=order.indexOf(to);if(a<0||b<0)return;order.splice(a,1);order.splice(b,0,from);await submit([{op:'track_order',track_ids:order}],'调整轨道顺序',true,order);}
  function trackAction(action:string,id?:string){
    if(!editable||busy)return;if(action==='add'){void addTrack();return;}
    const index=tracks.findIndex(t=>t.id===id);if(index<0)return;
    if(action==='remove')void submit(removeTrack(s,id!),'删除轨道与所属内容',true,tracks.map(t=>t.id));
    else if(action==='up'&&index>0)void reorder(id!,tracks[index-1].id);
    else if(action==='down'&&index<tracks.length-1)void reorder(id!,tracks[index+1].id);
  }
  useEffect(()=>{if(!trackRequest||trackSequence.current===trackRequest.sequence)return;trackSequence.current=trackRequest.sequence;trackAction(trackRequest.action,trackRequest.trackId);},[trackRequest]);
  function trackMenu(id:string,x:number,y:number){const index=tracks.findIndex(t=>t.id===id);setMenu({x,y,items:[
    {label:'打开钢琴卷帘',action:()=>{onSelect(id);setView('piano');}},
    {label:'新增乐器轨道',action:()=>trackAction('add'),disabled:!editable||busy},
    {label:'上移轨道',action:()=>trackAction('up',id),disabled:!editable||busy||index<=0},
    {label:'下移轨道',action:()=>trackAction('down',id),disabled:!editable||busy||index===tracks.length-1},
    {label:'删除轨道',action:()=>trackAction('remove',id),disabled:!editable||busy,danger:true},
  ]});}
  function toolsMenu(x:number,y:number){setMenu({x,y,items:[
    ...[['pencil','画笔'],['select','选择'],['erase','橡皮']].map(([id,label])=>({label,action:()=>setTool(id)})),
    {label:'将选区音符编组',action:()=>void group(),disabled:!noteEditing},
    {label:'撤销',shortcut:'Ctrl Z',action:()=>void onUndo(),disabled:!editable||busy||!canUndo},
    {label:'重做',shortcut:'Ctrl Shift Z',action:()=>void onRedo(),disabled:!editable||busy||!canRedo},
  ]});}
  function eraseAt(clientX:number,clientY:number){const mode=pointerMode.current;if(mode?.kind!=='erase'||!mode.svg)return;
    const box=mode.svg.getBoundingClientRect(),vb=mode.svg.viewBox.baseVal;
    const p={x:(clientX-box.left)*(vb.width||box.width)/box.width,y:(clientY-box.top)*(vb.height||box.height)/box.height};
    const previous={x:mode.x,y:mode.y};
    if(mode.collection==='notes')for(const n of localNotes){if(crossesRect(previous,p,{x:x(n.start_sample),y:y(n.pitch)+1,width:Math.max(3,(n.end_sample-n.start_sample)/span*pixels),height:row-1}))mode.ids.add(n.id);}
    else{const rowId=mode.svg.closest<HTMLElement>('[data-track-id]')?.dataset.trackId;for(const c of s.clips??[]){if(c.track_id===rowId&&crossesRect(previous,p,{x:x(c.start_sample),y:6,width:Math.max(5,c.duration_samples/span*pixels),height:54}))mode.ids.add(c.id);}}
    mode.x=p.x;mode.y=p.y;setErasing([...mode.ids]);
  }
  function pointerDown(e:ReactPointerEvent<HTMLDivElement>){
    if(e.button!==1&&e.button!==2)return;const target=e.target as Element;
    if(target.closest('input,textarea,select,[contenteditable=true]'))return;
    if(e.button!==1)e.preventDefault();e.stopPropagation();frame.current?.focus({preventScroll:true});
    if((e.buttons&4)&&(e.buttons&2)){cancelGesture();toolsMenu(e.clientX,e.clientY);return;}
    if(e.button===1){const scroll=target.closest<HTMLElement>('.daw-piano-scroll,.daw-playlist-scroll,.daw-velocity,.daw-view-content');
      if(!scroll)return;pointerMode.current={kind:'pan',revision:revision.id,x:e.clientX,y:e.clientY,scroll,ids:new Set()};e.currentTarget.setPointerCapture(e.pointerId);e.currentTarget.dataset.panning='true';return;}
    if(e.ctrlKey||e.metaKey){onViewport?.(1e-6,.5);if(view==='piano')fitPitch();return;}
    const lane=target.closest<HTMLElement>('.daw-lane-label');if(lane){const id=lane.closest<HTMLElement>('[data-track-id]')?.dataset.trackId;if(id)trackMenu(id,e.clientX,e.clientY);return;}
    const svg=target.closest<SVGSVGElement>('.daw-piano-grid,.daw-playlist-row svg');
    if(!svg||!editable||busy||(view==='piano'&&!noteEditing))return;
    const collection=view==='piano'?'notes':'clips';const b=svg.getBoundingClientRect(),vb=svg.viewBox.baseVal;
    pointerMode.current={kind:'erase',revision:revision.id,x:(e.clientX-b.left)*(vb.width||b.width)/b.width,y:(e.clientY-b.top)*(vb.height||b.height)/b.height,svg,ids:new Set(),collection};
    eraseAt(e.clientX,e.clientY);e.currentTarget.setPointerCapture(e.pointerId);
  }
  function pointerMove(e:ReactPointerEvent<HTMLDivElement>){const mode=pointerMode.current;if(!mode)return;e.preventDefault();e.stopPropagation();
    if((e.buttons&6)===6){cancelGesture();toolsMenu(e.clientX,e.clientY);return;}
    if(mode.kind==='erase')eraseAt(e.clientX,e.clientY);
    else{mode.scroll!.scrollTop-=e.clientY-mode.y;onViewport?.(1,.5,-(e.clientX-mode.x)/pixels);mode.x=e.clientX;mode.y=e.clientY;}
  }
  function pointerEnd(e:ReactPointerEvent<HTMLDivElement>,cancel=false){const mode=pointerMode.current;if(!mode)return;e.preventDefault();e.stopPropagation();
    pointerMode.current=null;delete e.currentTarget.dataset.panning;setErasing([]);if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);
    if(cancel||mode.revision!==revision.id||mode.kind!=='erase'||!mode.ids.size)return;
    if(mode.collection==='notes')void submit([...mode.ids].map(id=>({op:'delete',collection:'notes',id})),'右键擦除音符');
    else{const clips=(s.clips??[]).filter(c=>mode.ids.has(c.id)),owners=[...new Set(clips.map(c=>c.track_id))];
      void submit([...(s.notes??[]).filter(n=>n.clip_id&&mode.ids.has(n.clip_id)).map(n=>({op:'delete' as const,collection:'notes' as const,id:n.id})),...clips.map(c=>({op:'delete' as const,collection:'clips' as const,id:c.id}))],'右键擦除片段',true,owners);}
  }
  async function deleteClip(){if(!chosenClip)return;const ops:EditOperation[]=(s.notes??[]).filter(n=>n.clip_id===chosenClip.id).map(n=>({op:'delete',collection:'notes',id:n.id}));ops.push({op:'delete',collection:'clips',id:chosenClip.id});if(await submit(ops,'删除片段及所属音符',true,[chosenClip.track_id]))setClipId(null);}
  async function duplicateClip(){if(!chosenClip)return;try{const ops=await arrangeClipEdits(s,chosenClip,chosenClip.start_sample+chosenClip.duration_samples,revision.id,true);await submit(ops,'复制片段',true,[chosenClip.track_id]);}catch(e){setError((e as Error).message);}}
  function mixChange(id:string,patch:MixValues){if(onMixChange){onMixChange(id,patch);return;}setMix(current=>({...current,[id]:{...current[id],...patch}}));}
  const previews=new Map((gesture?.preview??[]).map(n=>[n.id,n]));
  const displayNotes=visibleNotes.map(n=>previews.get(n.id)??n).concat(gesture?.kind==='draw'?(gesture.preview.length?gesture.preview:gesture.notes):[]);
  return <div className="daw-editor" ref={frame} tabIndex={0} onMouseDownCapture={e=>{if(e.button===1)e.preventDefault();if((e.buttons&6)===6){e.preventDefault();e.stopPropagation();cancelGesture();toolsMenu(e.clientX,e.clientY);}}} onLostPointerCapture={()=>{if(pointerMode.current)cancelGesture();}} onKeyDown={keyboard} onContextMenu={e=>{if(!(e.target as Element).closest('input,textarea,[contenteditable=true]'))e.preventDefault();}} onAuxClick={e=>{if(e.button===1)e.preventDefault();}} onPointerDownCapture={pointerDown} onPointerMoveCapture={pointerMove} onPointerUpCapture={e=>pointerEnd(e)} onPointerCancelCapture={e=>pointerEnd(e,true)} data-editor-revision={revision.id} aria-label="制作编辑器">
    <div className="daw-view-tabs">{(mixingOnly?[['mixer','混音台']]:[['playlist','编排'],['piano','钢琴卷帘'],['overview','总览']]).map(([id,name])=><button key={id} aria-pressed={view===id} onClick={()=>{if(id==='piano'&&!track){const first=tracks.find(t=>t.kind==='instrument'&&t.role!=='drums'&&t.instrument_candidates?.includes('GM:0'))??tracks.find(t=>t.kind==='instrument'&&t.role!=='drums')??tracks.find(t=>t.kind==='instrument');if(first)onSelect(first.id);}setView(id);}}><Icon name={id==='playlist'?'timeline':id==='piano'?'piano':id==='mixer'?'mixer':'wave'}/>{name}</button>)}<span className="daw-edit-state">{!busy&&(editable?'可编辑':'历史版本 · 只读')}</span><div className="daw-edit-tools" hidden={mixingOnly}><select aria-label="编辑声部" value={selected} onChange={e=>onSelect(e.target.value)}><option value="all">选择声部</option>{tracks.map(t=><option key={t.id} value={t.id}>{trackName(t)}</option>)}</select>
      <label>吸附<select aria-label="网格吸附" value={division} onChange={e=>setDivision(+e.target.value)}><option value={0}>关闭</option>{s.timing_map?<><option value={1}>1/4 音符</option><option value={.5}>1/8 音符</option><option value={.25}>1/16 音符</option><option value={.125}>1/32 音符</option></>:<><option value={.25}>0.25 秒</option><option value={.1}>0.1 秒</option><option value={.01}>0.01 秒</option></>}</select></label>
      <button disabled={!editable||busy||!canUndo} onClick={()=>void onUndo().catch(e=>setError(e.message))}>撤销</button><button disabled={!editable||busy||!canRedo} onClick={()=>void onRedo().catch(e=>setError(e.message))}>重做</button>
    </div><button className="editor-menu-button" aria-label="编辑工具菜单" onClick={e=>{const r=e.currentTarget.getBoundingClientRect();toolsMenu(r.left,r.bottom);}}>⋯</button></div>
    {error&&<p className="daw-editor-error" role="alert">{error}<button aria-label="关闭编辑提示" onClick={()=>setError('')}>×</button></p>}
    <div className="daw-view-content" data-view={view}>
    {view==='piano'&&<><div className="daw-note-tools">{[['select','选择 / 拖动'],['pencil','画笔'],['erase','橡皮']].map(([id,name])=><button key={id} aria-pressed={tool===id} onClick={()=>setTool(id)}>{name}</button>)}<button disabled={!noteEditing||!selectedNotes.length} onClick={()=>void submit(selectedNotes.map(n=>({op:'delete',collection:'notes',id:n.id})),'删除所选音符')}>删除音符</button><label>音高缩放 <button aria-label="缩小音高" onClick={()=>pitchZoom(row/1.2)}>−</button><input aria-label="音高缩放" type="range" min={8} max={40} step={1} value={row} onChange={e=>pitchZoom(+e.target.value)}/><button aria-label="放大音高" onClick={()=>pitchZoom(row*1.2)}>+</button></label><button onClick={fitPitch}>适配音域</button><small>{noteIds.length} 个已选 · Shift 多选 / 框选</small></div>
      {!track||!['instrument','midi'].includes(track.kind)?<div className="daw-empty">选择一个乐器或 MIDI 声部以编辑音符。</div>:<><div className="daw-piano-area" style={{flexBasis:pianoHeight}}><div className="daw-piano-scroll" ref={pianoScroll} style={{height:pianoHeight}} onScroll={e=>setPitchScroll(e.currentTarget.scrollTop)}><div className="daw-piano-ruler"><svg width={laneWidth} height={24} onPointerDown={e=>{if(e.button===0)onSeek(Math.max(0,Math.min(duration,sample(e.clientX-e.currentTarget.getBoundingClientRect().left))));}}>{labelLines.map(l=><g key={l.sample}><line x1={x(l.sample)} x2={x(l.sample)} y1={16} y2={24} stroke="#9aa0a9"/><text x={x(l.sample)+4} y={13} fill="#b2b7bd" fontSize={10}>{l.label}</text></g>)}<text x={7} y={14} fill="#9aa0a9" fontSize={8}>{s.timing_map?'小节':'秒'}</text></svg></div><svg className={'daw-piano-grid tool-'+tool} width={Math.max(240,width)} height={top+128*row} viewBox={`0 0 ${Math.max(240,width)} ${top+128*row}`} onPointerDown={begin} onPointerMove={move} onPointerUp={finish} onPointerCancel={cancelGesture} aria-label="可编辑钢琴卷帘">
        {Array.from({length:128},(_,i)=>127-i).map(pitch=><g key={pitch}><rect x={left} y={y(pitch)} width={pixels} height={row} fill={[1,3,6,8,10].includes(pitch%12)?canvasTheme.rowDark:canvasTheme.row} stroke={canvasTheme.grid} strokeWidth=".35"/><rect x={0} y={y(pitch)} width={left-2} height={row-.4} fill={[1,3,6,8,10].includes(pitch%12)?canvasTheme.keyDark:canvasTheme.key}/>{pitch%12===0&&<text x={7} y={y(pitch)+10} fill={canvasTheme.keyText} fontSize={9}>{label(pitch)}</text>}</g>)}
        {displayLines.map(l=><line key={l.sample} x1={x(l.sample)} x2={x(l.sample)} y1={top} y2={top+128*row} stroke={l.major?canvasTheme.majorGrid:canvasTheme.grid} strokeWidth={l.major?1:.6}/>)}
        {displayNotes.map(n=><g key={n.id} data-note-id={n.id} data-erasing={erasing.includes(n.id)} data-pitch={n.pitch} data-start={n.start_sample} data-end={n.end_sample} aria-label={`${label(n.pitch)} ${(n.start_sample/rate).toFixed(2)}秒`}><rect x={Math.max(left,x(n.start_sample))} y={y(n.pitch)+1} width={Math.max(3,x(n.end_sample)-Math.max(left,x(n.start_sample)))} height={row-2} rx={1} fill={noteIds.includes(n.id)?canvasTheme.selected:palette[Math.max(0,tracks.findIndex(t=>t.id===selected))%palette.length]} stroke={noteIds.includes(n.id)?'#ffe2c7':'#0e0e10'} strokeWidth={.8}/><rect data-action="resize" data-note-id={n.id} data-erasing={erasing.includes(n.id)} x={Math.max(left,x(n.end_sample)-5)} y={y(n.pitch)+1} width={6} height={row-2} fill="transparent" className="note-resize"/></g>)}
        {gesture?.kind==='box'&&<rect x={Math.min(gesture.x,gesture.endX)} y={Math.min(gesture.y,gesture.endY)} width={Math.abs(gesture.endX-gesture.x)} height={Math.abs(gesture.endY-gesture.y)} fill="#979ea720" stroke="#a9aeb5" pointerEvents="none"/>}
        <line x1={x(position)} x2={x(position)} y1={0} y2={top+128*row} stroke={canvasTheme.playhead} pointerEvents="none"/>
        <rect x={left} y={0} width={pixels} height={top} fill="#202023"/>{labelLines.map(l=><text key={l.sample} x={x(l.sample)+4} y={16} fill="#aeb3ba" fontSize={10}>{l.label}</text>)}
      </svg></div><div className="pitch-navigator"><button aria-label="音区向上" onClick={()=>pianoScroll.current?.scrollBy({top:-row*6})}>↑</button><input aria-label="卷帘音高位置" type="range" min={0} max={Math.max(0,top+25+128*row-pianoViewportHeight)} step={row} value={pitchScroll} onChange={e=>scrollPitch(+e.target.value)} onKeyDown={e=>{if(e.key==='Home'||e.key==='End'){e.preventDefault();scrollPitch(e.key==='Home'?0:pianoScroll.current?.scrollHeight??0);}}}/><button aria-label="音区向下" onClick={()=>pianoScroll.current?.scrollBy({top:row*6})}>↓</button></div></div><PanelResize label="调整卷帘高度" axis="y" value={pianoHeight} min={180} max={1200} onChange={setPianoHeight} reset={386}/>
      <div className="daw-velocity-label">力度 <small>拖动柱形调整所选音符 · Ctrl+C / Ctrl+V · Delete</small></div><div className="daw-velocity" style={{height:velocityHeight}}><svg width={Math.max(240,width)} height={velocityHeight} preserveAspectRatio="none" viewBox={`0 0 ${Math.max(240,width)} 126`} onPointerDown={begin} onPointerMove={move} onPointerUp={finish} onPointerCancel={cancelGesture} aria-label="力度编辑器">{visibleNotes.map(n=><rect key={n.id} data-note-id={n.id} data-erasing={erasing.includes(n.id)} data-action="velocity" x={x(n.start_sample)} y={top+100-(previews.get(n.id)?.velocity??n.velocity)*100} width={5} height={(previews.get(n.id)?.velocity??n.velocity)*100} fill={noteIds.includes(n.id)?canvasTheme.selected:palette[Math.max(0,tracks.findIndex(t=>t.id===selected))%palette.length]}/>)}<line x1={left} x2={left+pixels} y1={125} y2={125} stroke="#50565e"/></svg></div><PanelResize label="调整力度栏高度" axis="y" value={velocityHeight} min={60} max={400} onChange={setVelocityHeight} reset={126}/>
      {activeNote&&<form className="daw-note-properties" onSubmit={e=>{e.preventDefault();void changeNotes([userNote(activeNote,revision.id,{pitch:fields.pitch,start_sample:Math.round(fields.start*rate),end_sample:Math.round(fields.end*rate),velocity:fields.velocity/100})],'精确修改音符');}}>
        {(['pitch','start','end','velocity'] as const).map(key=><label key={key}>{({pitch:'音高 MIDI',start:'起点 秒',end:'终点 秒',velocity:'力度 %'})[key]}<input aria-label={key==='pitch'?'音符音高':key==='start'?'音符起点秒':key==='end'?'音符终点秒':'音符力度百分比'} type="number" value={fields[key]} min={key==='velocity'?1:0} max={key==='pitch'?127:key==='velocity'?100:duration/rate} step={key==='start'||key==='end'?'any':1} onChange={e=>setFields({...fields,[key]:+e.target.value})}/></label>)}<button disabled={!noteEditing}>应用音符属性</button></form>}</>}
    </>}
    {view==='playlist'&&<>
      <div className="daw-playlist-scroll" ref={playlistScroll}><div className="daw-playlist-ruler"><span><button aria-label="新增乐器轨道" title="新增乐器轨道" disabled={!editable||busy} onClick={()=>void addTrack()}>＋</button>{s.timing_map?'小节':'时间 / 秒'}</span><svg width={laneWidth} height={24} onPointerDown={e=>{if(e.button===0)onSeek(Math.max(0,Math.min(duration,sample(e.clientX-e.currentTarget.getBoundingClientRect().left))));}}>{displayLines.map(l=><g key={l.sample}><line x1={x(l.sample)} x2={x(l.sample)} y1={l.major?3:16} y2={24} stroke="#5e656f"/>{labelSamples.has(l.sample)&&<text x={x(l.sample)+4} y={13} fill="#a1a7af" fontSize={10}>{l.label}</text>}</g>)}</svg></div>
        {tracks.map((t,i)=>{const {clips,count,notesPath}=playlistRows.get(t.id)!;
          return <div className={'daw-playlist-row '+(selected===t.id?'is-selected':'')} key={t.id} data-track-id={t.id} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();if(dragTrack.current&&editable&&!busy)void reorder(dragTrack.current,t.id);dragTrack.current=null;}}>
            <button className="daw-lane-label" aria-label={'编辑轨道 '+trackName(t)} onClick={()=>onSelect(t.id)} onDoubleClick={()=>{onSelect(t.id);setView('piano');}} draggable={editable&&!busy} onDragStart={()=>{dragTrack.current=t.id;}} onDragEnd={()=>{dragTrack.current=null;}}><i style={{background:palette[i%palette.length]}}/><strong>{trackName(t)}</strong><small>{t.kind==='audio'?'AUDIO':t.kind==='bus'?'BUS':'MIDI'} · {audibility(tracks,mix,t.id).muted?'MUTE':audibility(tracks,mix,t.id).solo?'SOLO':(s.notes??[]).filter(n=>n.track_id===t.id).length+' notes'}</small></button>
            <svg width={laneWidth} height={66} viewBox={`0 0 ${laneWidth} 66`} onPointerMove={clipMove} onPointerUp={clipFinish} onPointerCancel={cancelGesture} onDoubleClick={()=>{onSelect(t.id);setView('piano');}}>
              {displayLines.map(l=><line key={l.sample} x1={x(l.sample)} x2={x(l.sample)} y1={0} y2={66} stroke={l.major?canvasTheme.majorGrid:canvasTheme.grid}/>)}
              {count>0&&<g aria-label="未编组 MIDI 摘要" data-preview-note-count={count}><rect x={gridLeft} y={7} width={pixels} height={52} rx={4} fill={palette[i%palette.length]} fillOpacity={.15}/><rect x={gridLeft} y={7} width={pixels} height={2} rx={1} fill={palette[i%palette.length]} fillOpacity={.75}/><text x={gridLeft+7} y={20} fill={palette[i%palette.length]} fontSize={9}>MIDI · {count} 个可见音符 · 未编组</text><path d={notesPath} fill={palette[i%palette.length]} fillOpacity={.85}/></g>}
              {clips.map(c=>{const p=clipPreview?.id===c.id?clipPreview:c;return <g key={c.id} data-clip-id={c.id} data-erasing={erasing.includes(c.id)} onPointerDown={e=>clipBegin(e as unknown as ReactPointerEvent<SVGSVGElement>,c)}><rect x={x(p.start_sample)} y={6} width={Math.max(5,p.duration_samples/span*pixels)} height={54} rx={4} fill={palette[i%palette.length]} fillOpacity={.83} stroke={clipId===c.id?canvasTheme.selected:palette[i%palette.length]}/><text x={x(p.start_sample)+6} y={22} fill="#e9eaec" fontSize={10}>{c.asset_id?'AUDIO':'MIDI'} · {(p.duration_samples/rate).toFixed(2)}s{c.loop?' ↻':''}</text><rect data-action="resize" x={x(p.start_sample+p.duration_samples)-7} y={6} width={8} height={54} fill="transparent" className="note-resize"/></g>;})}
              <line x1={x(position)} x2={x(position)} y1={0} y2={66} stroke={canvasTheme.playhead} pointerEvents="none"/>
            </svg>
          </div>;})}
        {pendingTrack&&!tracks.some(t=>t.id===pendingTrack.id)&&<div className="daw-playlist-row daw-pending-track" data-pending-track={pendingTrack.id} aria-label="新增乐器轨道" aria-busy="true"><div className="daw-lane-label"><i style={{background:palette[tracks.length%palette.length]}}/><strong>{pendingTrack.name}</strong><small>MIDI · 0 notes</small></div><div className="daw-pending-lane"/></div>}
        <div className="daw-playlist-fill" aria-hidden="true"><div className="daw-empty-lane-label"/><svg width={laneWidth} height="100%" viewBox={`0 0 ${laneWidth} 1`} preserveAspectRatio="none">
          {displayLines.map(l=><line key={l.sample} x1={x(l.sample)} x2={x(l.sample)} y1={0} y2={1} stroke={l.major?canvasTheme.majorGrid:canvasTheme.grid}/>)}
          <line x1={x(position)} x2={x(position)} y1={0} y2={1} stroke={canvasTheme.playhead}/>
        </svg></div>
      </div>
      {chosenClip&&<div className="daw-clip-actions"><span>{chosenClip.asset_id?'音频':'MIDI'}片段 · {(chosenClip.start_sample/rate).toFixed(3)}s</span><button disabled={!editable||busy} onClick={()=>void duplicateClip()}>复制到后方</button><button disabled={!editable||busy} onClick={()=>void submit([{op:'arrange_clip',id:chosenClip.id,action:'split',sample:Math.round(position),new_id:uuid()}],'在播放头分割片段',true,[chosenClip.track_id])}>播放头分割</button><button disabled={!editable||busy} onClick={()=>void deleteClip()}>删除片段</button></div>}
    </>}
    {view==='mixer'&&<><div className="daw-mixer-actions"><strong>MIXER</strong><span>音量 / 声像 / 静音与独奏</span>{onMixChange?<span className="daw-mix-status" role="status">{mixSaving?'正在保存…':'即时试听 · 点击保存混音'}</span>:<button disabled={!editable||busy||!Object.keys(mix).length} onClick={()=>void submit(Object.entries(mix).map(([id,patch])=>({op:'put',collection:'tracks',entity:{...tracks.find(t=>t.id===id)!,...patch,provenance:{origin:'user',source_id:'ui:'+revision.id,review:'unreviewed'}}})),'保存混音设置',true,Object.keys(mix))}>保存混音</button>}</div><div className="daw-mixer">{tracks.map((t,i)=>{const draft={...t,mute:false,solo:false,...mix[t.id]},state=audibility(tracks,mix,t.id);return <div className={'daw-mixer-strip '+(selected===t.id?'is-selected':'')} key={t.id} style={{borderTopColor:palette[i%palette.length]}} data-mixer-track={t.id}>
      <button className="daw-channel-number" onClick={()=>onSelect(t.id)}>{String(i+1).padStart(2,'0')}</button><strong>{trackName(t)}</strong>
      <div className="daw-mute-solo"><button aria-label={'工程静音 '+trackName(t)} aria-pressed={state.muted} data-muted-by-solo={state.soloMuted} title={state.soloMuted?'其他轨道独奏时暂时静音':'静音此轨'} disabled={!editable||busy} onClick={()=>mixChange(t.id,{mute:!draft.mute})}>M</button><button aria-label={'工程独奏 '+trackName(t)} aria-pressed={draft.solo??false} disabled={!editable||busy} onClick={()=>mixChange(t.id,{solo:!draft.solo})}>S</button></div>
      <label>声像<input aria-label={'声像 '+trackName(t)} type="range" min={-1} max={1} step={.01} value={draft.pan??0} disabled={!editable||busy} onChange={e=>mixChange(t.id,{pan:+e.target.value})}/><small>{((draft.pan??0)*100).toFixed(0)}</small></label>
      <div className="daw-fader"><meter className="daw-live-meter" aria-label={'实际电平 '+trackName(t)} min={-60} max={0} value={livePeaks[t.id]??-60}/><input aria-label={'音量推子 '+trackName(t)} type="range" min={-60} max={12} step={.1} value={Math.max(-60,Math.min(12,draft.gain_db??0))} disabled={!editable||busy} onChange={e=>mixChange(t.id,{gain_db:+e.target.value})}/><div className="daw-fader-scale"><span>+12</span><span>0</span><span>−12</span><span>−36</span><span>−60</span></div></div><label>dB<input aria-label={'音量数值 '+trackName(t)} type="number" min={-120} max={24} step={.1} value={draft.gain_db??0} disabled={!editable||busy} onChange={e=>mixChange(t.id,{gain_db:+e.target.value})}/></label><small>{mixSaving?'正在保存':'独立混音'} · {t.kind}</small>
    </div>;})}</div></>}
    <div hidden={view!=='overview'}>{overview}</div>
    </div>
    {menu&&<ContextMenu {...menu} onClose={()=>setMenu(null)}/>}<div className="daw-editor-footer"><span>{editable?(mixingOnly?'混音设置单独保存':'音符自动保存 · 独奏 / 静音仅用于监听'):'历史版本受保护 · 切回当前版本后编辑'}</span><span>{s.timing_map?'网格跟随当前节拍':'无节拍图 · 使用源秒钟'} · 滚轮上下 · Alt+滚轮音高缩放 · Ctrl+滚轮时间缩放</span></div>
  </div>;
}
