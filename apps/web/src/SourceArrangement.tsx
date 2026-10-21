import {useEffect,useMemo,useRef,useState} from 'react';
import type {Annotation,NoteEvent,Revision,SourceReference} from '../../../packages/contracts/types';
import {useDisplayScale} from './useDisplayScale';
import {gridLines} from './dawEditing';
import {canvasTheme,trackColors} from './visual';
import './sourceArrangement.css';

type Wave={duration_samples:number;levels:Array<{samples_per_bucket:number;buckets:number[][][]}>};
const chordLabel=(text:string)=>text==='N'?'—':text.replace(':hdim7','ø7').replace(/:maj(?=$|\/)/,'').replace(':maj','maj').replace(':min','m').replace(':aug','+').replaceAll(':','').replaceAll('#','♯').replace(/([A-G])b/g,'$1♭');
const waveformCache=new Map<string,Promise<Wave>>();
function loadWaveform(sha:string){
  let request=waveformCache.get(sha);if(request)return request;
  request=fetch('/v1/assets/'+sha).then(r=>{if(!r.ok)throw new Error('原曲波形读取失败');return r.json() as Promise<Wave>;});
  waveformCache.set(sha,request);void request.catch(()=>{if(waveformCache.get(sha)===request)waveformCache.delete(sha);});
  while(waveformCache.size>8)waveformCache.delete(waveformCache.keys().next().value!);
  return request;
}
export function SourceArrangement({revision,reference,start,end,position,selected,onSelect,onSeek,onViewport,onUpdate,busy,editable}:{
  revision:Revision;reference:SourceReference;start:number;end:number;position:number;selected:string;
  onSelect:(id:string)=>void;onSeek:(sample:number)=>void;onViewport:(factor:number,anchor:number,pan?:number)=>void;
  onUpdate:(patch:{notes?:NoteEvent[];annotations?:Annotation[]})=>Promise<void>;busy:boolean;editable:boolean;
}){
  const displayScale=useDisplayScale();
  const host=useRef<HTMLDivElement>(null),canvas=useRef<HTMLCanvasElement>(null);
  const [width,setWidth]=useState(800),[waves,setWaves]=useState<Record<string,Wave>>({}),[error,setError]=useState('');
  const [waveError,setWaveError]=useState(''),[waveEpoch,setWaveEpoch]=useState(0);
  const waveKey=JSON.stringify(reference.waveforms??{}),waveLoading=Object.keys(waves).length<Object.keys(reference.waveforms??{}).length&&!waveError;
  const [editing,setEditing]=useState<Annotation|null>(null),[note,setNote]=useState<NoteEvent|null>(null);
  const rate=revision.snapshot.sample_rate??44100,span=Math.max(1,end-start);
  const sectionName:Record<string,string>={intro:'前奏',verse:'主歌','pre-chorus':'预副歌',chorus:'副歌',bridge:'桥段',interlude:'间奏',outro:'尾奏',instrumental:'器乐段',solo:'独奏'};
  const x=(sample:number)=>(sample-start)/span*width;
  const lines=useMemo(()=>gridLines(revision.snapshot,start,end,1).filter(l=>l.major),[revision,start,end]);
  const rulerLabels=useMemo(()=>{let previous=-Infinity;return lines.filter(l=>{const px=(l.sample-start)/span*width;if(px-previous<44)return false;previous=px;return true;});},[lines,start,span,width]);
  const visibleChords=(reference.annotations??[]).filter(a=>a.kind==='chord'&&a.end_sample>start&&a.start_sample<end);
  const denseChords=visibleChords.length>width/56;
  const overviewChords=useMemo(()=>{let previous=-Infinity;return (reference.annotations??[]).filter(a=>a.kind==='chord'&&a.end_sample>start&&a.start_sample<end).filter(a=>{const px=Math.max(0,(a.start_sample-start)/span*width);if(px-previous<78)return false;previous=px;return true;});},[reference,start,end,span,width]);
  const currentChord=(reference.annotations??[]).find(a=>a.kind==='chord'&&a.start_sample<=position&&a.end_sample>position);
  const pointer=useRef<{x:number;start:number}|null>(null),viewport=useRef(onViewport);viewport.current=onViewport;
  useEffect(()=>{const node=host.current;if(!node)return;const observer=new ResizeObserver(entries=>setWidth(entries[0].contentRect.width));observer.observe(node);
    const wheel=(e:WheelEvent)=>{if(e.ctrlKey||e.metaKey||e.shiftKey||Math.abs(e.deltaX)>Math.abs(e.deltaY)){e.preventDefault();const b=node.getBoundingClientRect(),anchor=(e.clientX-b.left)/b.width;if(e.ctrlKey||e.metaKey)viewport.current(Math.exp(-Math.max(-150,Math.min(150,e.deltaY))*.008),anchor);else viewport.current(1,anchor,(e.shiftKey?e.deltaY:e.deltaX)/b.width);}};
    node.addEventListener('wheel',wheel,{passive:false});return()=>{observer.disconnect();node.removeEventListener('wheel',wheel);};},[]);
  useEffect(()=>{let alive=true;setWaves({});setNote(null);setEditing(null);setWaveError('');
    void Promise.all(Object.entries((reference.waveforms??{})).map(async([id,sha])=>[id,await loadWaveform(sha)] as const)).then(items=>{if(alive)setWaves(Object.fromEntries(items));}).catch(e=>{if(alive)setWaveError(e.message);});return()=>{alive=false;};},[waveKey,waveEpoch]);
  useEffect(()=>{const node=canvas.current;if(!node)return;const dpr=window.devicePixelRatio||1,height=reference.tracks.length*150;
    node.width=Math.round(width*dpr);node.height=Math.round(height*dpr);const ctx=node.getContext('2d')!;ctx.scale(dpr,dpr);ctx.clearRect(0,0,width,height);
    reference.tracks.forEach((track,i)=>{const y=i*150,color=trackColors[i%trackColors.length];ctx.fillStyle=i%2?'#19191c':'#151518';ctx.fillRect(0,y,width,150);
      for(const l of lines){ctx.strokeStyle=canvasTheme.grid;ctx.beginPath();ctx.moveTo(x(l.sample),y);ctx.lineTo(x(l.sample),y+150);ctx.stroke();}
      const wave=waves[track.id];if(wave){const wanted=span/Math.max(1,width),level=[...wave.levels].reverse().find(l=>l.samples_per_bucket<=wanted)??wave.levels[0];ctx.fillStyle=color;ctx.globalAlpha=.75;
        const first=Math.max(0,Math.floor(start/level.samples_per_bucket)),last=Math.min(level.buckets.length,Math.ceil(end/level.samples_per_bucket));
        for(let j=first;j<last;j++){const bucket=level.buckets[j],lo=Math.min(...bucket.map(c=>c[0])),hi=Math.max(...bucket.map(c=>c[1]));ctx.fillRect(x(j*level.samples_per_bucket),y+75-hi*62,Math.max(1,level.samples_per_bucket/span*width),Math.max(1,(hi-lo)*62));}ctx.globalAlpha=1;
      }else if(track.kind==='midi'){const notes=(reference.notes??[]).filter(n=>n.end_sample>start&&n.start_sample<end),low=Math.min(48,...notes.map(n=>n.pitch)),high=Math.max(72,...notes.map(n=>n.pitch));ctx.fillStyle=color;
        for(const n of notes)ctx.fillRect(Math.max(0,x(n.start_sample)),y+124-(n.pitch-low)/(high-low+1)*100,Math.max(2,x(Math.min(end,n.end_sample))-Math.max(0,x(n.start_sample))),4);
      }
      ctx.fillStyle='#aba5a0';ctx.font='11px system-ui';ctx.fillText(track.kind==='midi'?'人声旋律 · MIDI · 不发声':track.name+' · 音频',12,y+20);
      ctx.fillStyle='#ffffff0b';ctx.fillRect(0,y+149,width,1);
    });
  },[reference,waves,width,start,end,lines,displayScale]);
  async function save(patch:{notes?:NoteEvent[];annotations?:Annotation[]}){try{setError('');await onUpdate(patch);setEditing(null);setNote(null);}catch(e){setError((e as Error).message);}}
  function chooseNote(e:React.MouseEvent<HTMLCanvasElement>){const box=e.currentTarget.getBoundingClientRect(),row=Math.floor((e.clientY-box.top)/150),track=reference.tracks[row];if(!track)return;onSelect(track.id);const sample=Math.round(start+(e.clientX-box.left)/box.width*span);onSeek(sample);
    if(track.kind==='midi'){const notes=(reference.notes??[]).filter(n=>n.start_sample<=sample&&n.end_sample>=sample);setNote(notes[0]??null);}}
  return <section className="source-arrangement" aria-label="原曲编排" ref={host} onContextMenu={e=>{if(!(e.target as Element).closest('input,textarea'))e.preventDefault();}} onPointerDown={e=>{if(e.button!==1)return;e.preventDefault();e.currentTarget.setPointerCapture(e.pointerId);pointer.current={x:e.clientX,start};}} onPointerMove={e=>{if(pointer.current){const dx=e.clientX-pointer.current.x;pointer.current.x=e.clientX;onViewport(1,.5,-dx/width);}}} onPointerUp={()=>{pointer.current=null;}} onPointerCancel={()=>{pointer.current=null;}} onAuxClick={e=>e.preventDefault()}>
    <div className="source-reference-heading"><strong>原曲编排</strong><span>{reference.has_vocals?'人声与伴奏 · 同步播放':'纯音乐'}</span>{waveLoading&&<span className="source-wave-loading" role="status">读取波形…</span>}{waveError&&<button onClick={()=>setWaveEpoch(n=>n+1)}>重试波形</button>}<div className="source-current-harmony"><span>原曲和弦</span><strong>{currentChord?.text==='N'?'无和弦':currentChord?chordLabel(currentChord.text):'—'}</strong></div><small>原曲参考</small></div>
    {(['structure','chord'] as const).map(kind=><div className={'source-label-lane '+kind} key={kind} aria-label={kind==='chord'?'原曲和弦':'原曲段落'}>{(reference.annotations??[]).filter(a=>a.kind===kind&&a.end_sample>start&&a.start_sample<end).map(a=><button key={a.id} data-annotation-id={a.id} aria-label={a.text} className={a.start_sample<=position&&a.end_sample>position?'is-playing':''} style={{left:Math.max(0,x(a.start_sample)),width:Math.max(.1,x(Math.min(end,a.end_sample))-Math.max(0,x(a.start_sample))),padding:x(Math.min(end,a.end_sample))-Math.max(0,x(a.start_sample))<40?0:undefined}} title={a.text+' · 双击修正'} onClick={()=>onSeek(a.start_sample)} onDoubleClick={()=>{setNote(null);setEditing(a);}}>{x(Math.min(end,a.end_sample))-Math.max(0,x(a.start_sample))>=(kind==='structure'?28:40)&&!(kind==='chord'&&denseChords)?(kind==='structure'?(sectionName[a.text]??a.text):chordLabel(a.text)):null}</button>)}{kind==='chord'&&denseChords&&overviewChords.map(a=><span className="source-chord-overview" key={'overview:'+a.id} style={{left:Math.max(0,x(a.start_sample))+3}}>{chordLabel(a.text)}</span>)}</div>)}
    <div className="source-ruler" aria-label="固定小节标尺">{rulerLabels.map(l=><button key={l.sample} style={{left:x(l.sample)}} onClick={()=>onSeek(l.sample)}>{l.label}</button>)}</div>
    <div className="source-wave-scroll"><div className="source-wave-surface" style={{minHeight:reference.tracks.length*150}}><canvas ref={canvas} data-waveform-tracks={Object.keys(waves).length} style={{width:'100%',height:reference.tracks.length*150}} aria-label="原曲音频波形与人声旋律" data-tracks={reference.tracks.length} onClick={chooseNote}/><div className="source-playhead" style={{left:x(position)}}/>{reference.tracks.map((track,i)=><div className={'source-lane-hit '+(selected===track.id?'is-selected':'')} key={track.id} style={{top:i*150,height:150}}/>)}</div></div>
    {(editing||note)&&<div className="source-correction"><strong>{editing?'修正'+(editing.kind==='chord'?'和弦':editing.kind==='structure'?'段落':'调性'):'修正旋律音符'}</strong>{editing?<><input aria-label="原曲标注文字" value={editing.text} onChange={e=>setEditing({...editing,text:e.target.value})}/><button disabled={!editable||busy||!editing.text.trim()} onClick={()=>void save({annotations:(reference.annotations??[]).map(a=>a.id===editing.id?{...editing,provenance:{origin:'user',source_id:'reference-editor',review:'accepted'}}:a)})}>保存</button></>:note&&<><label>音高<input aria-label="人声旋律音高" type="number" min={0} max={127} value={note.pitch} onChange={e=>setNote({...note,pitch:+e.target.value})}/></label><label>起点秒<input aria-label="人声旋律起点" type="number" step={.01} value={note.start_sample/rate} onChange={e=>setNote({...note,start_sample:Math.round(+e.target.value*rate)})}/></label><label>终点秒<input aria-label="人声旋律终点" type="number" step={.01} value={note.end_sample/rate} onChange={e=>setNote({...note,end_sample:Math.round(+e.target.value*rate)})}/></label><button disabled={!editable||busy} onClick={()=>void save({notes:(reference.notes??[]).map(n=>n.id===note.id?{...note,provenance:{origin:'user',source_id:'reference-editor',review:'accepted'}}:n)})}>保存</button><button disabled={!editable||busy} onClick={()=>void save({notes:(reference.notes??[]).filter(n=>n.id!==note.id)})}>删除音符</button></>}<button onClick={()=>{setNote(null);setEditing(null);}}>取消</button></div>}
    {(error||waveError)&&<p role="status">{error||waveError}</p>}
  </section>;
}
