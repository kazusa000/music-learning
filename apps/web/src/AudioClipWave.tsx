import {useEffect,useMemo,useState} from 'react';
import type {Clip} from '../../../packages/contracts/types';

export function AudioClipWave({waveform,clip,start,end,width,color}:{waveform:string;clip:Clip;start:number;end:number;width:number;color:string}){
  const [wave,setWave]=useState<{levels:Array<{samples_per_bucket:number;buckets:number[][][]}>}|null>(null);
  useEffect(()=>{let active=true;void fetch('/v1/assets/'+waveform).then(r=>{if(!r.ok)throw new Error('waveform');return r.json();}).then(data=>{if(active)setWave(data);}).catch(()=>{});return()=>{active=false;};},[waveform]);
  const path=useMemo(()=>{if(!wave)return '';const span=Math.max(1,end-start),wanted=span/width,level=[...wave.levels].reverse().find(l=>l.samples_per_bucket<=wanted)??wave.levels[0];const offset=clip.source_offset_samples??0;
    const begin=Math.max(0,Math.floor((start-clip.start_sample+offset)/level.samples_per_bucket)),finish=Math.min(level.buckets.length,Math.ceil((end-clip.start_sample+offset)/level.samples_per_bucket));
    const parts=[];for(let i=begin;i<finish;i++){const sample=i*level.samples_per_bucket-offset+clip.start_sample;if(sample<clip.start_sample||sample>clip.start_sample+clip.duration_samples)continue;const x=(sample-start)/span*width,b=level.buckets[i],lo=Math.min(...b.map(c=>c[0])),hi=Math.max(...b.map(c=>c[1]));parts.push(`M${x.toFixed(1)},${(38-hi*20).toFixed(1)}v${Math.max(.7,(hi-lo)*20).toFixed(1)}`);}return parts.join('');},[wave,clip,start,end,width]);
  return <path d={path} stroke={color} strokeWidth={1} opacity={.9} pointerEvents="none" aria-label="音频波形"/>;
}
