import {useEffect,useRef,useState} from 'react';
import type {Revision,SetProjectTiming} from '../../../packages/contracts/types';
import type {EditRequest} from './DawEditor';
import {commonMeters,projectTiming} from './projectTiming';

export function TimingControls({revision,position,disabled,onEdit}:{revision:Revision;position:number;disabled:boolean;onEdit:(request:EditRequest)=>Promise<void>}){
  const timing=projectTiming(revision.snapshot,position),bpm=timing?+timing.bpm.toFixed(2):null;
  const [draft,setDraft]=useState(String(bpm??'')),[error,setError]=useState('');
  const focused=useRef(false),saving=useRef(false),cancelBlur=useRef(false);
  const [custom,setCustom]=useState(false),[numerator,setNumerator]=useState(timing?.numerator??4),[denominator,setDenominator]=useState(timing?.denominator??4);
  useEffect(()=>{if(!focused.current)setDraft(String(bpm??''));},[revision.id,bpm]);
  const signature=timing?`${timing.numerator}/${timing.denominator}`:'';
  async function update(patch:Pick<SetProjectTiming,'tempo'|'numerator'|'denominator'>){
    if(disabled||saving.current)return;
    saving.current=true;setError('');
    try{await onEdit({operations:[{op:'project_timing',...patch,reference_sample:Math.max(0,Math.min(revision.snapshot.duration_samples??0,Math.round(position)))}],
      trackIds:(revision.snapshot.tracks??[]).map(t=>t.id),structural:true,description:patch.tempo!==undefined?`速度调整为 ${patch.tempo} BPM`:`拍号调整为 ${patch.numerator}/${patch.denominator}`});setCustom(false);}
    catch(e){setError((e as Error).message);setDraft(String(bpm??''));}
    finally{saving.current=false;}
  }
  function commitTempo(){
    focused.current=false;
    if(cancelBlur.current){cancelBlur.current=false;return;}
    const tempo=Number(draft);
    if(!Number.isFinite(tempo)||!draft.trim()||tempo<20||tempo>300){setDraft(String(bpm??''));setError('速度范围为 20–300 BPM。');return;}
    if(bpm!==null&&Math.abs(tempo-bpm)>.005)void update({tempo});
  }
  return <div className="project-timing" aria-label="速度与拍号" onPointerDown={e=>e.stopPropagation()}>
    <label className="tempo-control" title={timing?.variable?'当前位置速度；修改时按比例调整整曲速度，保留速度变化。':'整曲速度；BPM 以四分音符计，MIDI 跟随变速。'}><input aria-label="工程速度 BPM" type="number" min={20} max={300} step={.1} value={draft} disabled={disabled||!timing} onFocus={()=>{focused.current=true;setError('');}} onChange={e=>setDraft(e.target.value)} onBlur={commitTempo} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();e.currentTarget.blur();}if(e.key==='Escape'){cancelBlur.current=true;setDraft(String(bpm??''));focused.current=false;e.currentTarget.blur();}}}/><span>{timing?.variable?'BPM ≈':'BPM'}</span></label>
    <select aria-label="工程拍号" title="修改整曲拍号；保持音符时刻和速度。" disabled={disabled||!timing} value={custom?'custom':signature} onChange={e=>{if(e.target.value==='custom'){setNumerator(timing?.numerator??4);setDenominator(timing?.denominator??4);setCustom(true);return;}const [n,d]=e.target.value.split('/').map(Number);void update({numerator:n,denominator:d as SetProjectTiming["denominator"]});}}>
      {!timing&&<option value="">—/—</option>}{signature&&!commonMeters.includes(signature)&&<option value={signature}>{signature}</option>}{commonMeters.map(m=><option value={m} key={m}>{m}</option>)}<option value="custom">自定…</option>
    </select>
    {custom&&<form className="timing-custom" onSubmit={e=>{e.preventDefault();void update({numerator,denominator});}}><label>每小节<input aria-label="拍号分子" type="number" min={1} max={32} required value={numerator} onChange={e=>setNumerator(+e.target.value)}/></label><span>/</span><label>音符单位<select aria-label="拍号分母" value={denominator} onChange={e=>setDenominator(+e.target.value as NonNullable<SetProjectTiming["denominator"]>)}>{[1,2,4,8,16,32].map(d=><option key={d}>{d}</option>)}</select></label><button type="submit" disabled={disabled}>应用</button><button type="button" onClick={()=>setCustom(false)}>取消</button></form>}
    {error&&<div className="timing-error" role="alert">{error}<button onClick={()=>setError('')} aria-label="关闭速度提示">×</button></div>}
  </div>;
}
