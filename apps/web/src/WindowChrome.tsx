import {useEffect,useState} from 'react';
import type {PointerEvent} from 'react';

declare global { interface Window { mlDesktop?:{post:(action:string)=>void}; chrome?:{webview?:{addEventListener:(name:string,callback:(event:{data:unknown})=>void)=>void;removeEventListener:(name:string,callback:(event:{data:unknown})=>void)=>void}} } }
export function dragWindow(e:PointerEvent<HTMLElement>){
  if(e.button===0&&window.mlDesktop&&!(e.target as HTMLElement).closest('button,input,select,textarea,a,label,details,[role=menu],[role=slider],[role=separator]')){e.preventDefault();window.mlDesktop.post('drag');}
}
export function WindowControls(){
  const [maximized,setMaximized]=useState(false);
  useEffect(()=>{const bridge=window.chrome?.webview;if(!bridge)return;const change=(event:{data:unknown})=>{const value=event.data as {type?:string;maximized?:boolean};if(value.type==='window-state')setMaximized(!!value.maximized);};bridge.addEventListener('message',change);return()=>bridge.removeEventListener('message',change);},[]);
  if(!window.mlDesktop)return null;
  return <div className="window-controls"><button aria-label="最小化窗口" onClick={()=>window.mlDesktop?.post('minimize')}><svg viewBox="0 0 16 16"><path d="M3 8h10"/></svg></button><button aria-label={maximized?'还原窗口':'最大化窗口'} onClick={()=>window.mlDesktop?.post('maximize')}><svg viewBox="0 0 16 16">{maximized?<path d="M5 5V3h8v8h-2M3 5h8v8H3z"/>:<path d="M3 3h10v10H3z"/>}</svg></button><button className="window-close" aria-label="关闭窗口" onClick={()=>window.mlDesktop?.post('close')}><svg viewBox="0 0 16 16"><path d="m3 3 10 10M13 3 3 13"/></svg></button></div>;
}
