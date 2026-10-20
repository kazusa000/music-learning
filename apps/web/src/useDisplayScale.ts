import {useEffect,useState} from 'react';

// Moving between monitors can change backing pixels without changing CSS width.
export function useDisplayScale(){
  const [scale,setScale]=useState(()=>devicePixelRatio||1);
  useEffect(()=>{
    let query:MediaQueryList;
    const watch=()=>{
      query?.removeEventListener('change',watch);
      setScale(devicePixelRatio||1);
      query=matchMedia(`(resolution: ${devicePixelRatio||1}dppx)`);
      query.addEventListener('change',watch);
    };
    watch();window.addEventListener('resize',watch);
    return()=>{query.removeEventListener('change',watch);window.removeEventListener('resize',watch);};
  },[]);
  return scale;
}
