import {useEffect,useRef,useState} from 'react';
import {Transport} from './transport';
const time=(n:number)=>`${Math.floor(n/60)}:${Math.floor(n%60).toString().padStart(2,'0')}`;
export function AudioPreview({label,assetId,duration,onSelect,initial=false,previewGain}:{label:string;assetId:string;duration:number;onSelect:(audio:HTMLAudioElement)=>void;initial?:boolean;previewGain?:number}){
  const preview=useRef<Transport|null>(null),gain=useRef<GainNode|null>(null),media=useRef<MediaElementAudioSourceNode|null>(null);
  const [matched,setMatched]=useState(true),[preparing,setPreparing]=useState(false);
  useEffect(()=>{const transport=new Transport();preview.current=transport;const timer=setTimeout(()=>{if(previewGain===undefined)void transport.buffer(assetId).catch(()=>{});},750);return()=>{clearTimeout(timer);void transport.close();preview.current=null;media.current=null;gain.current=null;};},[assetId]);
  useEffect(()=>{const transport=preview.current;if(gain.current&&transport?.context&&previewGain!==undefined)gain.current.gain.setTargetAtTime(matched?previewGain:1,transport.context.currentTime,.01);},[previewGain,matched]);
  async function level(){const transport=preview.current;if(!transport||!audio.current)return;setPreparing(true);
    try{await transport.activate();if(!media.current){media.current=transport.context!.createMediaElementSource(audio.current);gain.current=transport.context!.createGain();gain.current.gain.value=0;media.current.connect(gain.current);gain.current.connect(transport.context!.destination);}
      if(previewGain===undefined)await transport.buffer(assetId);gain.current!.gain.setTargetAtTime(matched?(previewGain??transport.gain(assetId)):1,transport.context!.currentTime,.01);
    }catch(e){audio.current.pause();setError((e as Error).message);}finally{setPreparing(false);}
  }
  const audio=useRef<HTMLAudioElement>(null),[playing,setPlaying]=useState(false),[position,setPosition]=useState(0),[length,setLength]=useState(duration),[error,setError]=useState('');
  useEffect(()=>{if(initial&&audio.current)onSelect(audio.current);},[initial,assetId]);
  useEffect(()=>{const element=audio.current;return()=>element?.pause();},[assetId]);
  return <div className="audio-preview"><audio ref={audio} aria-label={label} src={'/v1/assets/'+assetId} preload="metadata"
    onLoadedMetadata={e=>setLength(e.currentTarget.duration)} onTimeUpdate={e=>setPosition(e.currentTarget.currentTime)}
    onPlay={e=>{void level();setPlaying(true);onSelect(e.currentTarget);document.querySelectorAll('audio').forEach(a=>{if(a!==e.currentTarget)a.pause();});}}
    onPause={()=>setPlaying(false)} onEnded={()=>setPlaying(false)}/>
    <button aria-label={(playing?'暂停':'播放')+label} onClick={()=>{const a=audio.current;if(!a)return;if(playing)a.pause();else void a.play().catch(e=>setError(e.message));}}>{playing?'Ⅱ':'▶'}</button>
    <input aria-label={label+'位置'} type="range" min={0} max={length||duration} step={.1} value={position} onChange={e=>{if(audio.current)audio.current.currentTime=+e.target.value;setPosition(+e.target.value);}}/>
    <span>{preparing?'准备试听…':time(position)+' / '+time(length||duration)}</span><label className="level-match"><input aria-label={label+'电平匹配'} type="checkbox" checked={matched} onChange={e=>{setMatched(e.target.checked);const transport=preview.current;if(transport){transport.setMatched(e.target.checked);if(gain.current&&transport.context)gain.current.gain.setTargetAtTime(e.target.checked?(previewGain??transport.gain(assetId)):1,transport.context.currentTime,.01);}}}/>电平匹配</label>{error&&<small role="alert">{error}</small>}
  </div>;
}
