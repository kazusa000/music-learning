import {useState} from 'react';
import {call} from './api';
import {usePreferences} from './Preferences';
import './settings.css';
export function SettingsPage({onBack}:{onBack:()=>void}){
  const {view,prefs,error,update,refresh}=usePreferences();
  const [models,setModels]=useState<Array<{id:string;name:string}>>([]),[loading,setLoading]=useState(false),[saving,setSaving]=useState(false),[notice,setNotice]=useState('');
  async function loadModels(){if(loading)return;setLoading(true);try{const data=await call<{models:Array<{id:string;name:string}>}>('dialogue.models',{login_mode:prefs.login_mode});setModels(data.models);setNotice('');}catch(e){setNotice((e as Error).message);}finally{setLoading(false);}}
  async function change(patch:Parameters<typeof update>[0]){setSaving(true);try{await update(patch);setNotice('已保存');}catch(e){setNotice((e as Error).message);}finally{setSaving(false);}}
  return <section className="settings-page" aria-label="应用设置"><div className="settings-heading"><button onClick={onBack}>‹ 返回音乐库</button><span>{saving?'保存中…':notice}</span></div><h1>设置</h1>
    {(error||(!notice.startsWith('已')&&notice))&&<p role="alert">{error||notice}</p>}
    <div className="settings-section"><div><h2>AI 助手</h2><p>讨论与改编共用这些默认设置，也可以在会话里随时切换。</p></div><div className="settings-fields">
      <label>默认模型<div className="settings-inline"><select aria-label="默认 AI 模型" value={prefs.default_model??''} disabled={saving} onFocus={()=>{if(!models.length)void loadModels();}} onChange={e=>void change({default_model:e.target.value||null})}><option value="">跟随 Codex 默认模型</option>{prefs.default_model&&!models.some(m=>m.id===prefs.default_model)&&<option value={prefs.default_model}>{prefs.default_model}</option>}{models.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select><button disabled={loading} onClick={()=>void loadModels()}>{loading?'读取中…':'刷新'}</button></div></label>
      <label>登录方式<select aria-label="AI 登录方式" value={prefs.login_mode} disabled={saving} onChange={e=>{setModels([]);void change({login_mode:e.target.value as 'existing'|'isolated'});}}><option value="existing">使用本机已有 Codex 登录</option><option value="isolated">使用此应用的独立登录</option></select><small>使用本机登录状态，不复制账号凭据。</small></label>
    </div></div>
    <div className="settings-section"><div><h2>播放</h2><p>调音量、静音、独奏直接作用于混音；编辑只更新相关音轨。</p></div><div className="settings-fields"><label className="settings-check"><input type="checkbox" aria-label="默认开启电平匹配" checked={prefs.level_match} onChange={e=>void change({level_match:e.target.checked})}/>新试听默认开启电平匹配</label><small>整曲混音只在点击导出时生成。</small></div></div>
    <div className="settings-section"><div><h2>音乐分析</h2><p>当前整曲预处理使用的模型。</p></div><div className="settings-models">{view?.analysis_models.map(m=><div key={m.id}><b>{m.name}</b><span>{m.purpose}</span></div>)}</div></div>
    <details className="settings-paths"><summary>本机文件与后台任务</summary><dl><dt>工程数据</dt><dd>{view?.runtime.data_root}</dd><dt>音源库</dt><dd>{view?.runtime.instrument_library??'尚未配置'}</dd><dt>VST3 搜索位置</dt><dd>{view?.runtime.plugin_roots.join('\n')}</dd></dl>{view?.background_verification&&<p>Keyscape 后台验证：{view.background_verification.status} · {view.background_verification.counts.passed??0} / {view.background_verification.total} 已通过</p>}<button onClick={()=>void refresh()}>刷新状态</button></details>
  </section>;
}
