import {useEffect,useRef,useState} from 'react';
import {Icon} from './Icon';
const time=(n:number)=>`${Math.floor(n/60)}:${Math.floor(n%60).toString().padStart(2,'0')}`;
export function AudioPreview({label,assetId,duration,onSelect,initial=false}:{label:string;assetId:string;duration:number;onSelect:(audio:HTMLAudioElement)=>void;initial?:boolean}){
  const audio=useRef<HTMLAudioElement>(null),[playing,setPlaying]=useState(false),[position,setPosition]=useState(0),[length,setLength]=useState(duration),[error,setError]=useState('');
  useEffect(()=>{if(initial&&audio.current)onSelect(audio.current);},[initial,assetId]);
  useEffect(()=>{const element=audio.current;return()=>element?.pause();},[assetId]);
  return <div className="audio-preview"><audio ref={audio} aria-label={label} src={'/v1/assets/'+assetId} preload="auto"
    onLoadedMetadata={e=>setLength(e.currentTarget.duration)} onTimeUpdate={e=>setPosition(e.currentTarget.currentTime)}
    onPlay={e=>{setPlaying(true);onSelect(e.currentTarget);document.querySelectorAll('audio').forEach(a=>{if(a!==e.currentTarget)a.pause();});}}
    onPause={()=>setPlaying(false)} onEnded={()=>setPlaying(false)}/>
    <button aria-label={(playing?'暂停':'播放')+label} onClick={()=>{const a=audio.current;if(!a)return;if(playing)a.pause();else void a.play().catch(e=>setError(e.message));}}><Icon name={playing?'pause':'play'} size={13}/></button>
    <input aria-label={label+'位置'} type="range" min={0} max={length||duration} step={.1} value={position} onChange={e=>{if(audio.current)audio.current.currentTime=+e.target.value;setPosition(+e.target.value);}}/>
    <span>{time(position)+' / '+time(length||duration)}</span>{error&&<small role="alert">{error}</small>}
  </div>;
}
