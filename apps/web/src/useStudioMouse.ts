import {useEffect} from 'react';

export function useStudioMouse(){
  useEffect(()=>{
    let drag:{node:HTMLElement;x:number;y:number;pointer:number}|null=null;
    const down=(e:PointerEvent)=>{
      if(e.button!==1||e.defaultPrevented)return;const target=e.target as Element;
      if(!target.closest('main')||target.closest('.daw-editor,input,textarea,select,[contenteditable=true]'))return;
      e.preventDefault();
      let node=target as HTMLElement;
      while(node&&node.tagName!=='MAIN'){
        const style=getComputedStyle(node);
        if(/auto|scroll/.test(style.overflowY)&&node.scrollHeight>node.clientHeight){drag={node,x:e.clientX,y:e.clientY,pointer:e.pointerId};node.setPointerCapture(e.pointerId);node.dataset.panning='true';return;}
        node=node.parentElement!;
      }
    };
    const move=(e:PointerEvent)=>{if(!drag)return;e.preventDefault();drag.node.scrollTop-=e.clientY-drag.y;drag.node.scrollLeft-=e.clientX-drag.x;drag.x=e.clientX;drag.y=e.clientY;};
    const end=()=>{const previous=drag;drag=null;if(previous){delete previous.node.dataset.panning;if(previous.node.hasPointerCapture(previous.pointer))previous.node.releasePointerCapture(previous.pointer);}};
    const context=(e:MouseEvent)=>{const target=e.target as Element;if(target.closest('main')&&!target.closest('input,textarea,[contenteditable=true]'))e.preventDefault();};
    const aux=(e:MouseEvent)=>{if(e.button===1&&(e.target as Element).closest('main'))e.preventDefault();};
    document.addEventListener('pointerdown',down);document.addEventListener('pointermove',move);document.addEventListener('pointerup',end);document.addEventListener('pointercancel',end);document.addEventListener('contextmenu',context);document.addEventListener('auxclick',aux);window.addEventListener('blur',end);
    return()=>{end();document.removeEventListener('pointerdown',down);document.removeEventListener('pointermove',move);document.removeEventListener('pointerup',end);document.removeEventListener('pointercancel',end);document.removeEventListener('contextmenu',context);document.removeEventListener('auxclick',aux);window.removeEventListener('blur',end);};
  },[]);
}
