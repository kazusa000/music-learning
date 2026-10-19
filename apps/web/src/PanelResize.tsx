import {useEffect,useRef,useState} from 'react';

const clamp=(value:number,min:number,max:number)=>Math.max(min,Math.min(max,value));
export function usePanelSize(key:string,initial:number,min:number,max:number){
  const [value,setValue]=useState(()=>{try{const stored=localStorage.getItem(key),n=stored===null?initial:Number(stored);return Number.isFinite(n)?clamp(n,min,max):initial;}catch{return initial;}});
  useEffect(()=>{const timer=setTimeout(()=>{try{localStorage.setItem(key,String(value));}catch{/* optional view preference */}},150);return()=>clearTimeout(timer);},[key,value]);
  return [value,setValue] as const;
}

export function PanelResize({label,axis='x',value,min,max,onChange,reset,reverse=false}:{label:string;axis?:'x'|'y';value:number;min:number;max:number;onChange:(value:number)=>void;reset:number;reverse?:boolean}){
  const drag=useRef<{pointer:number;at:number;value:number}|null>(null);
  return <div className={'panel-splitter panel-splitter-'+axis} role="separator" aria-label={label} aria-orientation={axis==='x'?'vertical':'horizontal'} aria-valuemin={min} aria-valuemax={max} aria-valuenow={Math.round(value)} tabIndex={0}
    title={label+'：拖动调整，双击恢复默认，方向键微调'}
    onDoubleClick={()=>onChange(clamp(reset,min,max))}
    onPointerDown={e=>{if(e.button!==0)return;e.preventDefault();e.currentTarget.focus({preventScroll:true});drag.current={pointer:e.pointerId,at:axis==='x'?e.clientX:e.clientY,value};e.currentTarget.setPointerCapture(e.pointerId);}}
    onPointerMove={e=>{const g=drag.current;if(!g||g.pointer!==e.pointerId)return;onChange(clamp(g.value+((axis==='x'?e.clientX:e.clientY)-g.at)*(reverse?-1:1),min,max));}}
    onPointerUp={e=>{drag.current=null;if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);}}
    onPointerCancel={()=>{if(drag.current)onChange(drag.current.value);drag.current=null;}}
    onKeyDown={e=>{const direction=axis==='x'?(e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0):(e.key==='ArrowDown'?1:e.key==='ArrowUp'?-1:0);if(direction){e.preventDefault();onChange(clamp(value+direction*(reverse?-1:1)*(e.shiftKey?40:10),min,max));}else if(e.key==='Home'){e.preventDefault();onChange(min);}else if(e.key==='End'){e.preventDefault();onChange(max);}}}><i/></div>;
}
