import {useEffect,useRef,useState} from 'react';
import {Icon} from './Icon';
import {NativeTransport} from './engine/NativeTransport';
export type PreviewControl={readonly paused:boolean;play:()=>Promise<void>;pause:()=>void};
const time=(n:number)=>`${Math.floor(n/60)}:${Math.floor(n%60).toString().padStart(2,'0')}`;
export function AudioPreview({label,projectId,revisionId,view,duration,onSelect,initial=false}:{label:string;projectId:string;revisionId:string;view:'original'|'arrangement';duration:number;onSelect:(audio:PreviewControl)=>void;initial?:boolean}){
  const player=useRef(new NativeTransport()),[playing,setPlaying]=useState(false),[position,setPosition]=useState(0),[error,setError]=useState('');
  const target={project:projectId,revision:revisionId,view};
  const control:PreviewControl={get paused(){return !player.current.ownsOutput||!player.current.playing;},
    play:async()=>{await player.current.apply(target,{playing:true,loop:false,start:0,end:Math.round(duration*(player.current.state.source_sample_rate??44100))});onSelect(control);},
    pause:()=>player.current.pause()};
  useEffect(()=>{
    const transport=new NativeTransport();player.current=transport;
    transport.onState=()=>{setPlaying(transport.ownsOutput&&transport.playing);if(transport.state.project_id===projectId)setPosition(transport.position);};
    transport.onError=e=>setError(e.message);transport.startPolling();
    if(initial)onSelect(control);
    return()=>{void transport.close();};
  },[projectId,revisionId,view]);
  return <div className="audio-preview" data-audio-clock="native">
    <button aria-label={(playing?'暂停':'播放')+label} onClick={()=>{if(playing)control.pause();else void control.play().catch(e=>setError(e.message));}}><Icon name={playing?'pause':'play'} size={13}/></button>
    <input aria-label={label+'位置'} type="range" min={0} max={duration} step={.1} value={Math.min(duration,position)} onChange={e=>{const seconds=+e.target.value;setPosition(seconds);void player.current.apply(target,{playing:!control.paused,sample:Math.round(seconds*(player.current.state.source_sample_rate??44100))}).catch(e=>setError(e.message));}}/>
    <span>{time(position)+' / '+time(duration)}</span>{error&&<small role="alert">{error}</small>}
  </div>;
}
