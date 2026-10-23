import {useEffect,useRef,useState} from 'react';
import type {LoadProgress} from './engine/projectLoading';
import './workspace-loading.css';

export function WorkspaceLoading({progress,loadKey,onRetry}:{progress:LoadProgress;loadKey:string;onRetry?:()=>void}){
  const [visible,setVisible]=useState(true),[elapsed,setElapsed]=useState(0);
  const start=useRef(performance.now());
  const active=progress.phase!=='ready'&&progress.phase!=='error';
  useEffect(()=>{start.current=performance.now();setElapsed(0);},[loadKey]);
  useEffect(()=>{if(!active)return;const timer=setInterval(()=>setElapsed(Math.floor((performance.now()-start.current)/1000)),1000);
    return()=>clearInterval(timer);},[loadKey,active]);
  useEffect(()=>{setVisible(true);if(progress.phase!=='ready')return;
    const timer=setTimeout(()=>setVisible(false),1200);return()=>clearTimeout(timer);},[loadKey,progress.phase]);
  if(!visible)return null;
  const value=progress.percent;
  return <section className="workspace-loading" data-phase={progress.phase} aria-label="工程加载状态" aria-busy={active}>
    <div className="workspace-loading-main"><span className="workspace-loading-mark" aria-hidden="true">{progress.phase==='ready'?'✓':progress.phase==='error'?'!':<i/>}</span>
      <div className="workspace-loading-copy"><div className="workspace-loading-heading"><strong role="status">{progress.title}</strong>
        <span>{progress.total>0?`${progress.ready} / ${progress.total} 个音源就绪`:''}{active&&elapsed>0?` · 已用 ${elapsed} 秒`:''}</span></div>
        <p>{progress.detail}</p></div>
      {value!==null&&<span className="workspace-loading-percent">{value}<small>%</small></span>}
      {progress.phase==='error'&&onRetry&&<button className="workspace-loading-retry" onClick={onRetry}>重新加载</button>}
      {!!progress.tracks.length&&<details className="workspace-loading-details"><summary>详情</summary><ul>{progress.tracks.map(track=><li key={track.id} data-status={track.status}>
        <span>{track.name}</span><small>{track.stage}</small>{track.error&&<p>{track.error}</p>}</li>)}</ul></details>}
    </div>
    <div className={'workspace-loading-bar'+(value===null&&active?' is-indeterminate':'')} role="progressbar" aria-label="工程加载进度"
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={value??undefined} aria-valuetext={value===null||!progress.total?progress.title:`${progress.ready} / ${progress.total} 个音源就绪，${value}%`}>
      <i style={{width:value===null?'26%':`${value}%`}}/>
    </div>
  </section>;
}

export function WorkspaceOpening({error,onRetry}:{error?:string;onRetry:()=>void}){
  return <div className="workspace-opening"><WorkspaceLoading loadKey="metadata" onRetry={onRetry} progress={{
    phase:error?'error':'preparing',title:error?'工程读取失败':'正在打开工程',detail:error||'读取编排、音轨和保存的版本',
    percent:null,ready:0,total:0,tracks:[],
  }}/></div>;
}
