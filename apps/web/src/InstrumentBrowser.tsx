import {useEffect,useState} from 'react';
import type {InstrumentCategories} from '../../../packages/contracts/types';
import {call} from './api';

export type InstrumentChoice={id:string;name:string;presetId?:string;presetName?:string};
type Preset={id:string;name:string;instrument_ids:string[];usage:string;audio_verified:boolean;classification_basis:string;source:{library_id:string;library_name:string;backend:string}};
type Results={total:number;items:Preset[];next_offset:number|null};

export function InstrumentBrowser({enabled,onChoose}:{enabled:boolean;onChoose:(choice:InstrumentChoice)=>void}){
  const [directory,setDirectory]=useState<InstrumentCategories|null>(null);
  const [instrument,setInstrument]=useState('acoustic-piano');
  const [search,setSearch]=useState(''),[verified,setVerified]=useState(false),[offset,setOffset]=useState(0);
  const [presets,setPresets]=useState<Results|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
  const category=directory?.items.find(c=>c.id===instrument);
  useEffect(()=>{let live=true;void call<InstrumentCategories>('instruments.categories').then(r=>{if(live)setDirectory(r);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[]);
  useEffect(()=>{let live=true;setLoading(true);setPresets(null);setError('');
    void call<Results>('instruments.list',{instrument_id:instrument,search,audio_verified:verified?true:null,offset,limit:20})
      .then(r=>{if(live)setPresets(r);}).catch(e=>{if(live)setError(e.message);}).finally(()=>{if(live)setLoading(false);});
    return()=>{live=false;};},[instrument,search,verified,offset]);
  const pick=(preset?:Preset)=>{if(category)onChoose({id:instrument,name:category.name,presetId:preset?.id,presetName:preset?.name});};
  return <section className="instrument-browser inspector-section" aria-label="统一乐器目录">
    <p className="eyebrow">INSTRUMENTS</p><h3>先选乐器，再找声音。</h3>
    <p className="instrument-intro">各音源库的音色汇集在同一乐器下，由 AI 按编曲需要选择。</p>
    <label className="instrument-select">乐器<select aria-label="选择乐器" value={instrument} onChange={e=>{setInstrument(e.target.value);setOffset(0);setSearch('');}}>
      {directory?.items.filter(c=>!c.parent_id).map(group=><optgroup label={group.name} key={group.id}>
        <option value={group.id}>{group.name} · 全部（{group.preset_count}）</option>
        {directory.items.filter(c=>c.parent_id===group.id).map(c=><option key={c.id} value={c.id}>{c.name}（{c.preset_count}）</option>)}
      </optgroup>)}
    </select></label>
    {category&&<div className="instrument-summary"><strong>{category.name}</strong><span>{category.preset_count} 个音色 · {category.verified_count} 个已验证</span>
      <button disabled={!enabled||!category.preset_count||instrument==='unclassified'} onClick={()=>pick()}>使用此乐器 · 交给 AI 选音色 ↗</button></div>}
    {!enabled&&<small className="instrument-hint">选择当前版本中的一个声部，即可准备换乐器。</small>}
    <div className="instrument-filters"><input aria-label="搜索当前乐器音色" placeholder="搜索音色、奏法或用途" value={search} onChange={e=>{setSearch(e.target.value);setOffset(0);}}/>
      <label><input type="checkbox" checked={verified} onChange={e=>{setVerified(e.target.checked);setOffset(0);}}/>仅已验证</label></div>
    {error&&<p role="alert" className="discussion-error">{error}</p>}{loading&&<p role="status">正在查找音色…</p>}
    {presets&&<><p className="instrument-results" role="status">找到 {presets.total} 个音色{instrument==='unclassified'?' · 乐器身份待核对':''}</p>
      <div className="instrument-preset-list">{presets.items.map(p=><article className="instrument-preset" key={p.id} data-preset-id={p.id}>
        <div><strong>{p.name}</strong><small className={p.audio_verified?'verified':'untested'}>{p.audio_verified?'已验证':'待发声验证'}</small></div><p>{p.usage}</p>
        <details><summary>音色来源与归类</summary><dl><dt>音源库</dt><dd>{p.source.library_name}</dd><dt>加载方式</dt><dd>{p.source.backend}</dd><dt>乐器</dt><dd>{p.instrument_ids.map(id=>directory?.items.find(c=>c.id===id)?.name??id).join(' / ')}</dd><dt>归类依据</dt><dd>{({'curated-registry':'精选明确映射','source-category':'原库目录标签','catalog-assignment':'目录显式标注','unclassified':'尚未确认'} as Record<string,string>)[p.classification_basis]??p.classification_basis}</dd></dl></details>
        <button disabled={!enabled||instrument==='unclassified'} onClick={()=>pick(p)}>指定此音色 ↗</button>
      </article>)}</div>
      {!presets.total&&<p className="instrument-hint">暂无符合条件的音色。后续音源库会继续归入这里。</p>}
      <div className="instrument-pages"><button disabled={!offset||loading} onClick={()=>setOffset(Math.max(0,offset-20))}>上一页</button><span>{presets.total?offset+1:0}–{offset+presets.items.length} / {presets.total}</span><button disabled={presets.next_offset===null||loading} onClick={()=>setOffset(presets.next_offset!)}>下一页</button></div>
    </>}
    <p className="instrument-hint">选择后进入改编草稿；发送后才执行捕获、修改与渲染。已验证表示发声与状态恢复通过。</p>
  </section>;
}
