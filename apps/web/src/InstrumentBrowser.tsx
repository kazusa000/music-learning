import {useEffect,useState} from 'react';
import type {InstrumentCategories} from '../../../packages/contracts/types';
import {call} from './api';
import type {PresetChoice} from './manualPreset';

type Preset={id:string;name:string;source:{library_name:string}};
type Results={total:number;items:Preset[];next_offset:number|null};

export function InstrumentBrowser({enabled,currentPreset,onChoose}:{enabled:boolean;currentPreset?:string|null;onChoose:(choice:PresetChoice)=>void}){
  const [directory,setDirectory]=useState<InstrumentCategories|null>(null);
  const [instrument,setInstrument]=useState('acoustic-piano');
  const [search,setSearch]=useState(''),[offset,setOffset]=useState(0);
  const [presets,setPresets]=useState<Results|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
  const category=directory?.items.find(c=>c.id===instrument);
  useEffect(()=>{let live=true;void call<InstrumentCategories>('instruments.categories').then(r=>{if(live)setDirectory(r);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[]);
  useEffect(()=>{let live=true;setLoading(true);setPresets(null);setError('');
    const timer=setTimeout(()=>{void call<Results>('instruments.list',{instrument_id:instrument,search,audio_verified:null,offset,limit:20})
      .then(r=>{if(live)setPresets(r);}).catch(e=>{if(live)setError(e.message);}).finally(()=>{if(live)setLoading(false);});},search?180:0);
    return()=>{live=false;clearTimeout(timer);};},[instrument,search,offset]);
  return <section className="instrument-browser inspector-section" aria-label="音色列表">
    <label className="instrument-select">乐器<select aria-label="选择乐器" value={instrument} onChange={e=>{setInstrument(e.target.value);setOffset(0);setSearch('');}}>
      {directory?.items.filter(c=>!c.parent_id).map(group=><optgroup label={group.name} key={group.id}>
        <option value={group.id}>{group.name}</option>
        {directory.items.filter(c=>c.parent_id===group.id).map(c=><option key={c.id} value={c.id}>{c.name}</option>)}
      </optgroup>)}
    </select></label>
    <input className="preset-search" aria-label="搜索音色" placeholder="搜索音色" value={search} onChange={e=>{setSearch(e.target.value);setOffset(0);}}/>
    {error&&<p role="alert" className="discussion-error">{error}</p>}
    {loading&&<div className="preset-list-loading" role="status" aria-label="读取音色列表"><i/><i/><i/></div>}
    {presets&&<><div className="instrument-preset-list">{presets.items.map(p=><button className={'preset-option '+(p.id===currentPreset?'is-current':'')} key={p.id} data-preset-id={p.id}
      disabled={!enabled||!category||p.id===currentPreset} aria-label={'切换到 '+p.name} aria-current={p.id===currentPreset?'true':undefined}
      onClick={()=>{if(category)onChoose({id:instrument,name:category.name,presetId:p.id,presetName:p.name});}}>
      <span><strong>{p.name}</strong><small>{p.source.library_name}</small></span><span className="preset-option-action">{p.id===currentPreset?'当前':'切换'}</span>
    </button>)}</div>
      {!presets.total&&<p className="preset-empty">没有匹配的音色</p>}
      {presets.total>20&&<div className="instrument-pages"><button aria-label="上一页音色" disabled={!offset||loading} onClick={()=>setOffset(Math.max(0,offset-20))}>‹</button><span>{Math.floor(offset/20)+1} / {Math.ceil(presets.total/20)}</span><button aria-label="下一页音色" disabled={presets.next_offset===null||loading} onClick={()=>setOffset(presets.next_offset!)}>›</button></div>}
    </>}
  </section>;
}
