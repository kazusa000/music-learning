import type {Revision} from '../../../packages/contracts/types';
import {InstrumentBrowser} from './InstrumentBrowser';
import type {InstrumentChoice} from './InstrumentBrowser';
import {trackName} from './dawEditing';

type PluginInfo={name:string;parameters_readback:Array<{id:string;name:string}>};

export function SoundPanel({revision,selected,onSelect,enabled,pluginInfo,onChoose}:{
  revision:Revision;selected:string;onSelect:(id:string)=>void;enabled:boolean;
  pluginInfo:Record<string,PluginInfo>;onChoose:(choice:InstrumentChoice)=>void;
}){
  const tracks=revision.snapshot.tracks??[];
  const track=tracks.find(t=>t.id===selected);
  const plugins=(revision.snapshot.plugins??[]).filter(p=>p.track_id===selected).sort((a,b)=>a.order-b.order);
  return <div className="sound-panel">
    <section className="inspector-section sound-current" aria-label="当前音轨音色与效果">
      <label className="sound-track-select">音轨<select aria-label="音色与效果的音轨" value={selected} onChange={e=>onSelect(e.target.value)}>
        <option value="all">请选择一个音轨</option>
        {tracks.map(t=><option key={t.id} value={t.id}>{trackName(t)}</option>)}
      </select></label>
      {track?<><h3>当前使用</h3><div className="sound-chain">
        {plugins.map((plugin,index)=><details className="plugin-card sound-slot" key={plugin.id} data-plugin-id={plugin.id}>
          <summary><span className="sound-slot-number">{index+1}</span><div><strong>{plugin.preset_name??pluginInfo[plugin.id]?.name??'未命名插件'}</strong><small>{pluginInfo[plugin.id]?.name??'VST3'} · {plugin.bypass?'已旁通':'已启用'}</small></div><span className="sound-slot-expand">参数</span></summary>
          <p>VST3 · {plugin.version} · {Object.keys(plugin.parameters??{}).length} 个参数</p>
          <div className="parameter-list">{Object.entries(plugin.parameters??{}).slice(0,24).map(([id,value])=><div key={id}><span>{pluginInfo[plugin.id]?.parameters_readback.find(p=>p.id===id)?.name??id}</span><b>{value.toFixed(3)}</b></div>)}</div>
          {Object.keys(plugin.parameters??{}).length>24&&<p>显示前 24 个参数</p>}
        </details>)}
        {!plugins.length&&<p className="sound-empty">{track.kind==='audio'?'当前播放音频素材，未使用插件。':'尚未设置音色，从下方选择乐器。'}</p>}
      </div></>:<p className="sound-empty">先选音轨，再查看或更换它的音色。</p>}
    </section>
    <InstrumentBrowser enabled={enabled&&!!track} onChoose={onChoose}/>
  </div>;
}
