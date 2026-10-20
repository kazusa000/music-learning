import {useEffect,useState} from 'react';
import type {InstrumentCategories,InstrumentModelItem,InstrumentModels} from '../../../packages/contracts/types';
import {call} from './api';
import type {PresetChoice} from './manualPreset';
import './instrumentBrowser.css';

type Preset={id:string;name:string;variant_name:string;model:{id:string};source:{library_name:string}};
type Results={total:number;items:Preset[];next_offset:number|null};

export function InstrumentBrowser({enabled,currentPreset,targetName,onChoose}:{enabled:boolean;currentPreset?:string|null;targetName?:string;onChoose:(choice:PresetChoice)=>void}){
  const [directory,setDirectory]=useState<InstrumentCategories|null>(null);
  const [instrument,setInstrument]=useState('acoustic-piano');
  const [model,setModel]=useState<InstrumentModelItem|null>(null);
  const [search,setSearch]=useState(''),[offset,setOffset]=useState(0),[retry,setRetry]=useState(0);
  const [models,setModels]=useState<InstrumentModels|null>(null),[presets,setPresets]=useState<Results|null>(null);
  const [pending,setPending]=useState<Preset|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
  const [directoryError,setDirectoryError]=useState('');
  const category=directory?.items.find(c=>c.id===instrument);
  useEffect(()=>{let live=true;setDirectoryError('');void call<InstrumentCategories>('instruments.categories').then(r=>{if(live)setDirectory(r);}).catch(e=>{if(live)setDirectoryError(e.message);});return()=>{live=false;};},[retry]);
  useEffect(()=>{let live=true;setLoading(true);setPresets(null);setModels(null);setError('');
    const timer=setTimeout(()=>{
      const request=model?call<Results>('instruments.list',{instrument_id:instrument,model_id:model.id,search,offset,limit:20}):
        call<InstrumentModels>('instruments.models',{instrument_id:instrument,current_preset_id:currentPreset??null,search,offset,limit:20});
      void request.then(r=>{if(live){if(model)setPresets(r as Results);else setModels(r as InstrumentModels);}})
        .catch(e=>{if(live)setError(e.message);}).finally(()=>{if(live)setLoading(false);});
    },search?180:0);
    return()=>{live=false;clearTimeout(timer);};},[instrument,model,search,offset,retry,currentPreset]);
  const navigate=(next:InstrumentModelItem|null)=>{setModel(next);setSearch('');setOffset(0);setPending(null);setPresets(null);setModels(null);};
  const result=model?presets:models;
  const canApply=enabled&&!loading&&!error&&!directoryError&&!!category&&!!model&&!!pending&&pending.model.id===model.id&&pending.id!==currentPreset;
  return <section className="instrument-browser inspector-section" aria-label="音色列表">
    <label className="instrument-select">乐器<select aria-label="选择乐器" value={instrument} onChange={e=>{setInstrument(e.target.value);navigate(null);}}>
      {directory?.items.filter(c=>!c.parent_id).map(group=><optgroup label={group.name} key={group.id}>
        <option value={group.id}>{group.name}</option>
        {directory.items.filter(c=>c.parent_id===group.id).map(c=><option key={c.id} value={c.id}>{c.name}</option>)}
      </optgroup>)}
    </select></label>
    <div className="instrument-model-heading">{model?<><button className="model-back" aria-label="返回型号列表" onClick={()=>navigate(null)}>‹ 型号</button><div><strong>{model.name}</strong><small>{model.source.library_name}{model.kind==='layer'?' · 叠层组合':''}</small></div></>:<strong>选择型号</strong>}</div>
    <input className="preset-search" aria-label={model?'搜索变体':'搜索型号或变体'} placeholder={model?'搜索变体':'搜索型号或变体'} value={search} onChange={e=>{setSearch(e.target.value);setOffset(0);}}/>
    {(error||directoryError)&&<div className="preset-list-error"><p role="alert" className="discussion-error">{error||directoryError}</p><button onClick={()=>setRetry(n=>n+1)}>重试读取</button></div>}
    {loading&&<div className="preset-list-loading" role="status" aria-label={model?'读取变体列表':'读取型号列表'}><i/><i/><i/></div>}
    <div className="instrument-preset-list" role={model?'radiogroup':undefined} aria-label={model?'选择变体':'型号列表'} onKeyDown={e=>{
      if(!model||!presets?.items.length||!['ArrowDown','ArrowRight','ArrowUp','ArrowLeft','Home','End'].includes(e.key))return;
      e.preventDefault();const buttons=Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('[role="radio"]'));
      const index=buttons.indexOf(document.activeElement as HTMLButtonElement);
      const next=e.key==='Home'?0:e.key==='End'?buttons.length-1:(Math.max(0,index)+(e.key==='ArrowUp'||e.key==='ArrowLeft'?-1:1)+buttons.length)%buttons.length;
      setPending(presets.items[next]);buttons[next]?.focus();
    }}>
      {!model&&models?.items.map(m=><button className={'preset-option model-option '+(m.id===models.current_model_id?'is-current':'')} key={m.id} data-model-id={m.id} onClick={()=>navigate(m)}>
        <span><strong>{m.name}</strong><small>{m.source.library_name}{m.kind==='layer'?' · 叠层组合':''}</small></span><span className="preset-option-action">{m.id===models.current_model_id?'当前 · ':''}{m.variant_count} 变体 ›</span>
      </button>)}
      {model&&presets?.items.map((p,i)=><button role="radio" aria-checked={pending?.id===p.id} tabIndex={pending?.id===p.id||(!pending&&i===0)?0:-1} className={'preset-option variant-option '+(pending?.id===p.id?'is-selected ':'')+(p.id===currentPreset?'is-current':'')} key={p.id} data-preset-id={p.id} onClick={()=>setPending(p)}>
        <span><strong>{p.variant_name}</strong></span><span className="preset-option-action">{p.id===currentPreset?'当前':pending?.id===p.id?'已选':'○'}</span>
      </button>)}
      {result&&!result.total&&<p className="preset-empty">{model?'没有匹配的变体':'没有匹配的型号'}</p>}
    </div>
    {result&&result.total>20&&<div className="instrument-pages"><button aria-label="上一页" disabled={!offset||loading} onClick={()=>setOffset(Math.max(0,offset-20))}>‹</button><span>{Math.floor(offset/20)+1} / {Math.ceil(result.total/20)}</span><button aria-label="下一页" disabled={result.next_offset===null||loading} onClick={()=>setOffset(result.next_offset!)}>›</button></div>}
    {model&&<div className="preset-confirm">
      <div><span>{pending?.variant_name??'选择一个变体'}</span><small>{targetName?'应用到：'+targetName:'先选择音轨'}</small></div>
      <button className="primary" disabled={!canApply} onClick={()=>{if(canApply&&pending&&category){const choice={id:instrument,name:category.name,presetId:pending.id,presetName:pending.name};setPending(null);onChoose(choice);}}}>确认应用</button>
    </div>}
  </section>;
}
