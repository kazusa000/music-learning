import {useEffect,useRef,useState} from 'react';
import type {ReactNode} from 'react';

export function AutoHideToolbar({title,position,playing,loading,onPlay,onBack,children}:{title:string;position:string;playing:boolean;loading:boolean;onPlay:()=>void;onBack:()=>void;children:ReactNode}){
  const [pinned,setPinned]=useState(()=>{try{return localStorage.getItem('studio.toolbar-pinned')==='true';}catch{return false;}}),[open,setOpen]=useState(false);
  const root=useRef<HTMLDivElement>(null),timer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const expanded=pinned||open;
  function clear(){if(timer.current)clearTimeout(timer.current);timer.current=null;}
  function hide(){clear();timer.current=setTimeout(()=>{const active=document.activeElement;if(root.current?.contains(active)&&active?.matches('input,textarea,select,:focus-visible'))return;setOpen(false);},450);}
  useEffect(()=>{try{localStorage.setItem('studio.toolbar-pinned',String(pinned));}catch{/* optional preference */}},[pinned]);
  useEffect(()=>()=>clear(),[]);
  return <div ref={root} className={'toolbar-dock '+(pinned?'is-pinned':'is-auto')} data-expanded={expanded} onPointerEnter={()=>{clear();setOpen(true);}} onPointerLeave={()=>{if(!pinned)hide();}}
    onFocus={()=>{clear();setOpen(true);}} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node|null))hide();}}
    onKeyDown={e=>{if(e.key==='Escape'&&!pinned){e.preventDefault();(document.activeElement as HTMLElement)?.blur();clear();setOpen(false);}}}>
    <div className="toolbar-peek"><button aria-label="返回音乐库" title="返回音乐库" onClick={onBack}>‹</button><button className="peek-play" aria-label={playing?'快速暂停':'快速播放'} disabled={loading} onClick={onPlay}>{loading?'···':playing?'Ⅱ':'▶'}</button><span className="peek-title">{title}</span><time>{position}</time><span className="peek-spacer"/>
      <button aria-label="展开或收起工具栏" aria-expanded={expanded} disabled={pinned} onClick={()=>{clear();setOpen(!open);}}>{pinned?'常驻':expanded?'收起':'工具栏'} <span aria-hidden="true">{expanded?'⌃':'⌄'}</span></button>
      <button className="toolbar-pin" aria-label="锁定顶部工具栏" aria-pressed={pinned} title={pinned?'取消锁定，离开后自动收起':'锁定展开，始终显示播放工具栏'} onClick={()=>{setPinned(!pinned);setOpen(!pinned);}}>
        <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d={pinned?'M6 9V6a4 4 0 0 1 8 0v3':'M6 9V6a4 4 0 0 1 8-1'}/><rect x="4" y="9" width="12" height="9" rx="2"/><path d="M10 12v3"/></svg>{pinned?'已锁定':'自动收起'}
      </button></div>
    <div className="toolbar-drawer" hidden={!expanded} inert={!expanded}>{children}</div>
  </div>;
}
