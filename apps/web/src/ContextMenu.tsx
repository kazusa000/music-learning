import {useEffect,useLayoutEffect,useRef} from 'react';
import {createPortal} from 'react-dom';

export type MenuItem={label:string;action:()=>void;disabled?:boolean;danger?:boolean;shortcut?:string};
export function ContextMenu({x,y,items,onClose}:{x:number;y:number;items:MenuItem[];onClose:()=>void}){
  const ref=useRef<HTMLDivElement>(null),previous=useRef(document.activeElement as HTMLElement|null),close=useRef(onClose);close.current=onClose;
  useLayoutEffect(()=>{const node=ref.current;if(!node)return;node.style.left=Math.max(6,Math.min(x,innerWidth-node.offsetWidth-6))+'px';node.style.top=Math.max(6,Math.min(y,innerHeight-node.offsetHeight-6))+'px';node.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();},[x,y]);
  useEffect(()=>{const outside=(e:PointerEvent)=>{if(!ref.current?.contains(e.target as Node))close.current();};window.addEventListener('pointerdown',outside,true);return()=>{window.removeEventListener('pointerdown',outside,true);previous.current?.focus({preventScroll:true});};},[]);
  return createPortal(<div ref={ref} className="studio-context-menu" role="menu" style={{left:x,top:y}} onContextMenu={e=>e.preventDefault()} onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();onClose();}if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();const all=Array.from(ref.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')??[]);const i=all.indexOf(document.activeElement as HTMLButtonElement);all[(i+(e.key==='ArrowDown'?1:-1)+all.length)%all.length]?.focus();}}}>{items.map((item,i)=><button key={i} role="menuitem" className={item.danger?'danger':''} disabled={item.disabled} onClick={()=>{onClose();item.action();}}><span>{item.label}</span>{item.shortcut&&<small>{item.shortcut}</small>}</button>)}</div>,document.body);
}
