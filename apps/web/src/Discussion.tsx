import {usePreferences} from './Preferences';
import {Icon} from './Icon';
import {PanelResize,usePanelSize} from './PanelResize';
import {useCallback,useEffect,useRef,useState} from 'react';
import type {Citation,Conversation,DialogueTurn,Job,Revision,Track} from '../../../packages/contracts/types';
import {call} from './api';
import {MusicMarkdown} from './MusicMarkdown';
import {trackName} from './dawEditing';
import {subscribeDomain} from './events';

const time=(s:number)=>`${Math.floor(s/60).toString().padStart(2,'0')}:${(s%60).toFixed(1).padStart(4,'0')}`;
const active=(t:DialogueTurn)=>['queued','running','cancel_requested'].includes(t.status);
const statusText:Record<string,string>={queued:'等待讨论',running:'正在研究',cancel_requested:'正在停止',cancelled:'已停止',failed:'未完成',succeeded:'已完成'};
export type InstrumentDraft={id:string;text:string;revisionId:string;trackId:string};

function ToolHistory({tools}:{tools:DialogueTurn['tools']}){
  const [open,setOpen]=useState(false),[expanded,setExpanded]=useState<number|null>(null);
  return <details className="tool-history" onToggle={e=>setOpen(e.currentTarget.open)}><summary>{tools.length} 条工程查询 · 查看依据</summary>{open&&tools.map((tool,i)=><details key={i} onToggle={e=>{if(e.target===e.currentTarget)setExpanded(e.currentTarget.open?i:null);}}><summary>{String((tool.arguments as Record<string,unknown>)?.action??'summary')} · {tool.success?'完成':'失败'}</summary>{expanded===i&&<pre>{JSON.stringify(tool,null,2)}</pre>}</details>)}</details>;
}


const mergeTurns=(current:DialogueTurn[],items:DialogueTurn[])=>items.map(item=>{
  const previous=current.find(t=>t.id===item.id);return previous&&previous.updated_at>item.updated_at?previous:item;
});

export function Discussion({revision,headId,start,end,trackId,onLocate,jobs,onCompare,instrumentDraft,onConsumeDraft,expanded=false,onToggleExpand}:{revision:Revision;headId:string;start:number;end:number;trackId:string|null;onLocate:(citation:Citation)=>void;jobs:Job[];onCompare:(id:string)=>void;instrumentDraft?:InstrumentDraft|null;onConsumeDraft?:()=>void;expanded?:boolean;onToggleExpand?:()=>void}){
  const {prefs,update:updatePreferences}=usePreferences();
  type ChatSession={id:string;title:string;created_at:number};
  const sessionKey='discussion.session.'+revision.project_id;
  const [sessionId,setSessionId]=useState<string|null>(()=>localStorage.getItem(sessionKey));
  const [sessions,setSessions]=useState<ChatSession[]>([]),[model,setModel]=useState(prefs.default_model??'');
  const [models,setModels]=useState<Array<{id:string;name:string;is_default:boolean}>>([]),[modelsBusy,setModelsBusy]=useState(false);
  const sessionRef=useRef(sessionId);sessionRef.current=sessionId;
  const filteredTurns=(items:DialogueTurn[],id:string|null)=>items.filter(t=>(t.session_id??null)===id);
  useEffect(()=>{let live=true;void call<ChatSession[]>('dialogue.sessions.list',{project_id:revision.project_id,limit:1000}).then(items=>{if(live)setSessions(items);}).catch(()=>{});return()=>{live=false;};},[revision.project_id,sessionId]);
  function chooseSession(id:string|null){sessionRef.current=id;setSessionId(id);setTurns([]);setText('');setScope('whole');setDraftBinding(null);try{if(id)localStorage.setItem(sessionKey,id);else localStorage.removeItem(sessionKey);}catch{/* view preference */}}
  async function newSession(){setError('');try{const created=await call<ChatSession>('dialogue.sessions.create',{project_id:revision.project_id,title:'会话 '+(sessions.length+1)},headId,true);setSessions(current=>[created,...current]);chooseSession(created.id);setShowNotes(false);}catch(e){setError((e as Error).message);}}
  async function loadModels(force=false){if(modelsBusy)return;const requestedLogin=login;setModelsBusy(true);try{const result=await call<{models:Array<{id:string;name:string;is_default:boolean}>}>('dialogue.models',{login_mode:login,refresh:force});if(loginRef.current===requestedLogin)setModels(result.models);}catch(e){setError((e as Error).message);}finally{setModelsBusy(false);}}
  const [inputSize,setInputSize]=usePanelSize('discussion.input-height',84,64,320);
  const root=useRef<HTMLDivElement>(null),menu=useRef<HTMLDetailsElement>(null),settings=useRef<HTMLDetailsElement>(null);
  const [availableHeight,setAvailableHeight]=useState(window.innerHeight);
  useEffect(()=>{const node=root.current;if(!node)return;const observer=new ResizeObserver(entries=>setAvailableHeight(entries[0].contentRect.height));observer.observe(node);return()=>observer.disconnect();},[]);
  const maxInput=Math.max(64,Math.min(320,Math.floor(availableHeight*.4))),inputHeight=Math.min(inputSize,maxInput);
  useEffect(()=>{const close=(event:PointerEvent|KeyboardEvent)=>{
    if(event instanceof KeyboardEvent&&event.key!=='Escape')return;
    for(const node of [menu.current,settings.current])if(node&&(event instanceof KeyboardEvent||!node.contains(event.target as Node)))node.open=false;
  };document.addEventListener('pointerdown',close);document.addEventListener('keydown',close);return()=>{document.removeEventListener('pointerdown',close);document.removeEventListener('keydown',close);};},[]);
  const [turns,setTurns]=useState<DialogueTurn[]>([]),[notes,setNotes]=useState<Conversation[]>([]);
  const [text,setText]=useState(''),[login,setLogin]=useState<string>(prefs.login_mode),[scope,setScope]=useState('whole');
  useEffect(()=>{setModel(prefs.default_model??'');setLogin(prefs.login_mode);},[prefs.default_model,prefs.login_mode]);
  const loginRef=useRef(login);loginRef.current=login;
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[showNotes,setShowNotes]=useState(false);
  const [draftBinding,setDraftBinding]=useState<InstrumentDraft|null>(null);
  useEffect(()=>{if(!instrumentDraft)return;setText(instrumentDraft.text);setScope('whole');setDraftBinding(instrumentDraft);onConsumeDraft?.();},[instrumentDraft,onConsumeDraft]);
  const history=useRef<HTMLDivElement>(null),follow=useRef(true);
  const last=turns.at(-1);
  useEffect(()=>{follow.current=true;},[last?.id,showNotes]);
  useEffect(()=>{if(follow.current&&history.current)history.current.scrollTop=history.current.scrollHeight;},[last?.id,last?.text,last?.status,showNotes,notes.length]);
  const rate=revision.snapshot.sample_rate??44100,duration=revision.snapshot.duration_samples??0;
  const selectedTrack=revision.snapshot.tracks?.find(t=>t.id===trackId);
  const scopeName=selectedTrack?trackName(selectedTrack):'整首音乐';
  const a=scope==='whole'?0:Math.round(start*rate),b=scope==='whole'?duration:Math.round(end*rate);
  const refresh=useCallback(async()=>{
    const [items,messages]=await Promise.all([call<DialogueTurn[]>('dialogue.list',{project_id:revision.project_id,session_id:sessionId,limit:1000}),
      call<Conversation[]>('conversations.list',{project_id:revision.project_id,limit:1000})]);
    if(sessionRef.current!==sessionId)return;setTurns(current=>mergeTurns(current,filteredTurns(items,sessionId)));setNotes(messages.filter(m=>m.role==='note'));
  },[revision.project_id,sessionId]);
  useEffect(()=>{let live=true;const update=async()=>{
    const [items,messages]=await Promise.all([call<DialogueTurn[]>('dialogue.list',{project_id:revision.project_id,session_id:sessionId,limit:1000}),call<Conversation[]>('conversations.list',{project_id:revision.project_id,limit:1000})]);
    if(live){if(sessionRef.current!==sessionId)return;setTurns(current=>mergeTurns(current,filteredTurns(items,sessionId)));setNotes(messages.filter(m=>m.role==='note'));}
  };void update().catch(e=>{if(live)setError(e.message);});
    let pending:ReturnType<typeof setTimeout>|undefined;
    const unsubscribe=subscribeDomain(event=>{if(event.type==='connection.ready'||event.project_id===revision.project_id&&(event.type.startsWith('dialogue.')||event.type==='note.saved')){
      clearTimeout(pending);pending=setTimeout(()=>void update().catch(()=>{}),80);
    }});
    const timer=setInterval(()=>void update().catch(()=>{}),15000);
    return()=>{live=false;clearInterval(timer);clearTimeout(pending);unsubscribe();};},[revision.project_id,sessionId]);
  const activeIds=turns.filter(active).map(t=>t.id).join(',');
  useEffect(()=>{
    if(!activeIds)return;
    const fixedSession=sessionId;let live=true,running=false;
    const update=async()=>{if(running)return;running=true;try{
      const items=await Promise.all(activeIds.split(',').map(turn_id=>call<DialogueTurn>('dialogue.get',{project_id:revision.project_id,turn_id})));
      if(live&&sessionRef.current===fixedSession)setTurns(current=>current.map(turn=>{
        const item=items.find(t=>t.id===turn.id);return item&&item.updated_at>=turn.updated_at?item:turn;
      }));
    }finally{running=false;}};
    const timer=setInterval(()=>void update().catch(()=>{}),900);
    return()=>{live=false;clearInterval(timer);};
  },[revision.project_id,activeIds,sessionId]);
  function questionArgs(question:string){return {project_id:revision.project_id,revision_id:revision.id,session_id:sessionId,model:model||null,text:question,start_sample:a,end_sample:b,track_id:trackId,login_mode:login,edit_mode:'auto'};}
  async function submit(){if(!text.trim())return;const submittedSession=sessionId;setBusy(true);setError('');try{
    if(draftBinding&&(draftBinding.revisionId!==revision.id||draftBinding.trackId!==trackId))throw new Error('乐器草稿绑定的版本或声部已改变，请重新选择乐器。');
    await call('dialogue.submit',questionArgs(text),headId,true);if(sessionRef.current===submittedSession){setText('');setDraftBinding(null);await refresh();}
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function control(turn:DialogueTurn,operation:'dialogue.cancel'|'dialogue.retry'){
    try{if(operation==='dialogue.retry')await call('dialogue.submit',questionArgs(String(turn.payload.text)),headId,true);else await call(operation,{project_id:revision.project_id,turn_id:turn.id},headId,true);await refresh();}catch(e){setError((e as Error).message);}}
  async function save(turn?:DialogueTurn){try{
    const arrangement=turn?.context.arrangement as {revision_id:string}|undefined;
    await call('notes.save',{project_id:revision.project_id,revision_id:arrangement?.revision_id??turn?.revision_id??revision.id,content:turn?.text??text,
      start_sample:turn?.context.start_sample??a,end_sample:turn?.context.end_sample??b,
      track_id:turn?.context.track_id??(scope==='whole'?null:trackId),source_turn_id:turn?.id??null,state:'draft'},headId,true);
    if(!turn)setText('');await refresh();setShowNotes(true);
  }catch(e){setError((e as Error).message);}}
  async function exportVersion(turn:DialogueTurn,format:'midi'|'wav'|'hybrid'){
    const receipt=turn.context.arrangement as {revision_id:string;render_job_id:string}|undefined;
    if(!receipt)return;try{
      const scope=turn.context.edit_scope as {track_ids:string[];start_sample:number;end_sample:number};
      const bass=revision.snapshot.tracks?.find(t=>scope.track_ids.includes(t.id)&&(t.role==='bass'||t.instrument_candidates?.includes('GM:33')));
      if(format==='hybrid'&&!bass)throw new Error('该次修改未授权一个可识别的贝斯声部。');
      await call('jobs.submit',{project_id:revision.project_id,kind:'project.export',payload:{revision_id:receipt.revision_id,format,render_job_id:receipt.render_job_id,
        ...(format==='hybrid'?{replacement_track_id:bass!.id,start_sample:scope.start_sample,end_sample:scope.end_sample}:{} )}},headId,true);
      setError('导出任务已保存，完成后可下载或试听。');
    }catch(e){setError((e as Error).message);}
  }
  function reply(turn:DialogueTurn){return <MusicMarkdown text={turn.text} citations={turn.citations} onLocate={onLocate}/>;}
  function turnTrackName(turn:DialogueTurn){
    if(!turn.context.track_id)return '全部声部';
    const stored=turn.context.selected_track as Track|undefined;
    const track=stored??revision.snapshot.tracks?.find(t=>t.id===turn.context.track_id);
    return track?trackName(track):'指定声部';
  }
  return <div className="discussion" aria-label="工程讨论" ref={root}>
    <div className="discussion-heading">
      <div className="chat-session-select"><select aria-label="会话" title="切换会话" value={sessionId??''} onChange={e=>chooseSession(e.target.value||null)}><option value="">历史讨论</option>{sessions.map(s=><option key={s.id} value={s.id}>{s.title}</option>)}</select><select aria-label="对话模型" title="对话模型" value={model} onFocus={()=>{if(!models.length)void loadModels();}} onChange={e=>{setModel(e.target.value);void updatePreferences({default_model:e.target.value||null}).catch(e=>setError(e.message));}}><option value="">{modelsBusy?'读取模型中…':'默认模型'}</option>{model&&!models.some(m=>m.id===model)&&<option value={model}>{model}</option>}{models.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select></div>
      <button className="new-discussion" aria-label="＋ 新会话" title="新会话" onClick={()=>void newSession()}><Icon name="plus"/></button>
      {onToggleExpand&&<button className="expand-discussion" aria-label={expanded?'还原讨论':'放大讨论'} aria-pressed={expanded} title={expanded?'还原讨论':'放大讨论'} onClick={onToggleExpand}><Icon name={expanded?'collapse':'expand'}/></button>}
      <details className="discussion-menu discussion-popover" ref={menu}><summary aria-label="对话菜单" title="会话菜单"><Icon name="more"/></summary><div className="discussion-menu-content">
        <div className="discussion-toggle"><button aria-pressed={!showNotes} onClick={()=>{setShowNotes(false);if(menu.current)menu.current.open=false;}}>讨论 {turns.length}</button><button aria-pressed={showNotes} onClick={()=>{setShowNotes(true);if(menu.current)menu.current.open=false;}}>笔记 {notes.length}</button></div>
        <button aria-label="刷新可用模型" disabled={modelsBusy} onClick={()=>void loadModels(true)}>刷新模型列表</button>
        <button disabled={!text.trim()||busy} onClick={()=>{void save();if(menu.current)menu.current.open=false;}}>把输入存为笔记</button>
      </div></details>
    </div>
    <div className="discussion-history" ref={history} onScroll={e=>{const n=e.currentTarget;follow.current=n.scrollHeight-n.scrollTop-n.clientHeight<65;}} aria-live="polite">{showNotes?<>{!notes.length&&<p className="discussion-empty">还没有笔记。从回复下方保存，或在菜单中保存当前输入。</p>}{notes.map(note=><article className="discussion-note" key={note.id}><small>{note.context?.origin==='user'?'我的笔记':'AI 笔记'}</small><MusicMarkdown text={note.content}/></article>)}</>:<>
      {!turns.length&&<div className="discussion-empty"><strong>从这首歌开始</strong><p>选中一段音乐，或直接聊整曲。</p><div className="question-suggestions"><button onClick={()=>{setScope('whole');setText('请先查询整曲摘要，从和声、配器与节奏三个角度提出值得深入研究的问题，并给出工程引用。');}}>分析整曲结构</button><button onClick={()=>{setScope('selection');setText('请查询当前区间的和弦与声部，解释和声走向及各乐器的作用，并引用具体位置。');}}>聊选中片段</button><button onClick={()=>{setText('请保持旋律，试一个新的配器方案，应用到工程并实时试听。');}}>试一个改编</button></div></div>}
      {turns.map(turn=><article key={turn.id} className="discussion-turn" data-turn={turn.id} data-status={turn.status}>
        <div className="question-bubble">{String(turn.payload.text)}</div><div className="turn-meta"><i className={active(turn)?'pulsing':''}/><strong>{String((turn.context.provider as {model?:string}|undefined)?.model??turn.payload.model??'Codex')}</strong><span>{turn.error?.code==='invalid_citation'?'回复已保留':statusText[turn.status]}</span></div>
        <small className="turn-range">{time(Number(turn.context.start_sample)/Number(turn.context.sample_rate))}–{time(Number(turn.context.end_sample)/Number(turn.context.sample_rate))} · {turnTrackName(turn)}</small>
        {turn.text&&<div className="discussion-reply">{reply(turn)}</div>}{turn.error&&<p className={turn.error.code==='invalid_citation'?'discussion-notice':'discussion-error'}>{turn.error.code==='invalid_citation'?'回复已保留，部分位置链接不可用。':turn.error.code==='edit_not_applied'?'这轮没有修改工程。现在可以直接发送修改要求。':String(turn.error.message)}</p>}
        {!!turn.citations.length&&<div className="citation-list">{turn.citations.map(c=><button key={c.id} onClick={()=>onLocate(c)} title={c.revision_id}>{c.label} ↗</button>)}</div>}
        {!!turn.tools.length&&<ToolHistory tools={turn.tools}/>}
        <div className="turn-actions">{active(turn)?<button onClick={()=>void control(turn,'dialogue.cancel')}>停止</button>:turn.status==='succeeded'?<button onClick={()=>void save(turn)}>保存为研究笔记</button>:<button onClick={()=>void control(turn,'dialogue.retry')}>重试此问题</button>}</div>
        {!!turn.context.arrangement&&(()=>{const receipt=turn.context.arrangement as {revision_id:string;before_revision:string;render_job_id:string|null;playback_available?:boolean;diff:Record<string,{added?:string[];removed?:string[];changed?:string[]}>};
          const render=jobs.find(j=>j.id===receipt.render_job_id),exports=jobs.filter(j=>j.kind==='project.export'&&j.revision_id===receipt.revision_id);
          return <div className="arrangement-receipt" data-arrangement-revision={receipt.revision_id}><small>已更新工程</small><strong>{!receipt.render_job_id?'● 已接入逐轨实时播放':render?.status==='succeeded'?'● 已有导出音频':render?.status==='failed'?'× 导出失败':'◌ 正在导出'}</strong><p>{Object.entries(receipt.diff).filter(([key])=>key!=='settings').filter(([,v])=>(v.added?.length??0)+(v.removed?.length??0)+(v.changed?.length??0)>0).map(([key,v])=>`${key} +${v.added?.length??0} −${v.removed?.length??0} Δ${v.changed?.length??0}`).join(' · ')}</p>
            {render?.error&&<p className="discussion-error">{String(render.error.code)}</p>}<div><button onClick={()=>onCompare(receipt.before_revision)}>对比修改</button><button onClick={()=>void exportVersion(turn,'midi')}>导出 MIDI</button><button onClick={()=>void exportVersion(turn,'wav')}>导出 WAV</button><button disabled={render?.status!=='succeeded'} onClick={()=>void exportVersion(turn,'hybrid')}>生成贝斯混合</button></div>
            {exports.slice(-4).map(job=><p key={job.id}>{String(job.payload.format).toUpperCase()} · {statusText[job.status]} {job.status==='succeeded'&&typeof job.result?.asset_id==='string'&&<a href={'/v1/assets/'+job.result.asset_id} download>下载 ↗</a>}{job.error&&<span className="discussion-error"> {String(job.error.code)}</span>}</p>)}</div>;})()}
      </article>)}</>}
    </div>
    <PanelResize label="调整输入框高度" axis="y" value={inputHeight} min={64} max={maxInput} onChange={setInputSize} reset={84} reverse/>
    <div className="discussion-compose">
      <textarea aria-label="讨论问题" title="Enter 发送，Shift+Enter 换行" placeholder="聊音乐，或直接说想怎么改…" value={text} onChange={e=>setText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing&&e.nativeEvent.keyCode!==229&&!busy&&!turns.some(active)){e.preventDefault();void submit();}}} style={{height:inputHeight}} rows={3} maxLength={12000}/>
      <div className="compose-actions">
        <details className="discussion-settings discussion-popover" ref={settings}><summary aria-label="上下文与设置" title={`${scope==='whole'?'整曲':'选区'} · ${scopeName} · ${time(a/rate)}–${time(b/rate)}`}><span className="compose-target">{scopeName}</span><span>{scope==='whole'?'整曲':'选区'} ⌄</span></summary><div className="discussion-settings-content">
          <div className="discussion-context"><label htmlFor="discussion-scope">讨论上下文</label><select id="discussion-scope" aria-label="讨论上下文" value={scope} onChange={e=>setScope(e.target.value)}><option value="whole">整曲</option><option value="selection">手动指定当前循环片段</option></select></div>
          <div className="discussion-scope-summary"><span>{scopeName}</span><time>{time(a/rate)}–{time(b/rate)}</time></div>
          <label>Codex 登录方式 <select aria-label="Codex 登录方式" value={login} onChange={e=>{setLogin(e.target.value);setModels([]);void updatePreferences({login_mode:e.target.value as 'existing'|'isolated'}).catch(e=>setError(e.message));}}><option value="isolated">应用独立登录</option><option value="existing">使用既有 Codex 登录</option></select></label>
          <p>Enter 发送 · Shift+Enter 换行</p>
        </div></details>
        <button className="send-discussion" title="Enter 发送，Shift+Enter 换行" disabled={!text.trim()||busy||turns.some(active)} onClick={()=>void submit()}>{busy?'保存中':<>发送 <Icon name="send" size={13}/></>}</button>
      </div>
      {error&&<p className="discussion-error" role="alert">{error}</p>}
    </div>
  </div>;
}
