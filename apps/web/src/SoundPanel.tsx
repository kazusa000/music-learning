import {useEffect,useState} from 'react';
import type {Revision} from '../../../packages/contracts/types';
import {InstrumentBrowser} from './InstrumentBrowser';
import type {PresetChoice,SoundChange} from './manualPreset';
import {trackName} from './dawEditing';

type PluginInfo={name:string;is_instrument?:boolean;metadata_asset_id?:string;parameters_readback:Array<{id:string;name:string}>};
export function SoundPanel({revision,selected,onSelect,enabled,pluginInfo,onChoose,change,onRetry,metadataError,onReloadMetadata}:{
  revision:Revision;selected:string;onSelect:(id:string)=>void;enabled:boolean;
  pluginInfo:Record<string,PluginInfo>;onChoose:(choice:PresetChoice,targetId:string|null)=>void;
  change:SoundChange|null;onRetry:()=>void;metadataError:string;onReloadMetadata:()=>void;
}){
  const tracks=revision.snapshot.tracks??[],track=tracks.find(t=>t.id===selected);
  const plugins=(revision.snapshot.plugins??[]).filter(p=>p.track_id===selected).sort((a,b)=>a.order-b.order);
  const instruments=plugins.filter(p=>pluginInfo[p.id]?.is_instrument);
  const [slot,setSlot]=useState('');
  const instrument=instruments.find(p=>p.id===slot)??instruments[0];
  const [now,setNow]=useState(Date.now());
  const busy=!!change&&!['done','error'].includes(change.phase);
  useEffect(()=>{if(!busy)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[busy]);
  const completeMetadata=plugins.every(p=>pluginInfo[p.id]?.metadata_asset_id===p.descriptor_asset_id);
  return <div className="sound-panel">
    <section className="inspector-section sound-current">
      <label className="sound-track-select">音轨<select aria-label="音色与效果的音轨" value={selected} onChange={e=>onSelect(e.target.value)}>
        <option value="all">选择音轨</option>{tracks.map(t=><option key={t.id} value={t.id}>{trackName(t)}</option>)}
      </select></label>
      {instruments.length>1?<label className="sound-track-select sound-slot-picker">乐器插槽<select aria-label="要更换的乐器插槽" value={instrument?.id??''} onChange={e=>setSlot(e.target.value)}>{instruments.map(p=><option key={p.id} value={p.id}>{p.preset_name??pluginInfo[p.id].name}</option>)}</select></label>:
        instrument&&<div className="sound-active-name">{instrument.preset_name??pluginInfo[instrument.id]?.name}</div>}
    </section>
    {change&&<section className={'sound-switch-progress is-'+change.phase} aria-label="音色切换进度" aria-busy={busy}>
      <div className="sound-switch-title"><strong>{change.presetName}</strong><span>{change.phase==='done'?'已切换':change.phase==='error'?'未完成':Math.max(0,Math.floor((now-change.startedAt)/1000))+'s'}</span></div>
      <small>{change.trackName}</small>
      <div className="sound-progress-bar" role="progressbar" aria-label="切换音色" aria-valuemin={0} aria-valuemax={3} aria-valuenow={change.completed} aria-valuetext={change.phase==='error'?change.message:change.phase==='done'?'切换完成':['读取音色','应用到音轨','加载声音'][change.completed]}>
        {[0,1,2].map(step=><i key={step} className={step<change.completed?'is-complete':busy&&step===change.completed?'is-loading':''}/>)}
      </div>
      <div className="sound-progress-steps">{['读取音色','应用到音轨','加载声音'].map((label,i)=><span key={label} className={change.completed===i&&busy?'is-active':''}>{label}</span>)}</div>
      {change.phase==='error'&&<div className="sound-switch-error"><p role="alert">{change.applied?'音色已应用，声音未就绪：':''}{change.message}</p><button onClick={onRetry}>{change.applied?'重试加载':'重试'}</button></div>}
    </section>}
    {metadataError&&<div className="sound-metadata-error"><p role="alert">{metadataError}</p><button onClick={onReloadMetadata}>重新读取</button></div>}
    <InstrumentBrowser enabled={enabled&&track?.kind==='instrument'&&completeMetadata&&!busy} currentPreset={instrument?.preset_id} onChoose={choice=>onChoose(choice,instrument?.id??null)}/>
    {!!plugins.length&&<details className="inspector-section sound-effects"><summary>效果与参数</summary><div className="sound-chain">
      {plugins.map(plugin=><details className="plugin-card sound-slot" key={plugin.id} data-plugin-id={plugin.id}><summary><div><strong>{plugin.preset_name??pluginInfo[plugin.id]?.name??'VST3'}</strong></div></summary>
        <div className="parameter-list">{Object.entries(plugin.parameters??{}).slice(0,24).map(([id,value])=><div key={id}><span>{pluginInfo[plugin.id]?.parameters_readback.find(p=>p.id===id)?.name??id}</span><b>{value.toFixed(3)}</b></div>)}</div>
      </details>)}
    </div></details>}
  </div>;
}
