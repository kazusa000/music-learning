import {useEffect,useRef,useState} from 'react';
import type {Citation,Conversation,DialogueTurn,Job,Revision} from '../../../packages/contracts/types';
import {call} from './api';

const time=(s:number)=>`${Math.floor(s/60).toString().padStart(2,'0')}:${(s%60).toFixed(1).padStart(4,'0')}`;
const active=(t:DialogueTurn)=>['queued','running','cancel_requested'].includes(t.status);
const statusText:Record<string,string>={queued:'等待讨论',running:'正在研究',cancel_requested:'正在停止',cancelled:'已停止',failed:'未完成',succeeded:'已完成'};

export function Discussion({revision,headId,start,end,trackId,onLocate,jobs,onCompare}:{revision:Revision;headId:string;start:number;end:number;trackId:string|null;onLocate:(citation:Citation)=>void;jobs:Job[];onCompare:(id:string)=>void}){
  const [turns,setTurns]=useState<DialogueTurn[]>([]),[notes,setNotes]=useState<Conversation[]>([]);
  const [text,setText]=useState(''),[login,setLogin]=useState('isolated'),[scope,setScope]=useState('whole');
  const [editing,setEditing]=useState(false),[structural,setStructural]=useState(false);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[showNotes,setShowNotes]=useState(false);
  const history=useRef<HTMLDivElement>(null),follow=useRef(true);
  const last=turns.at(-1);
  useEffect(()=>{follow.current=true;},[last?.id,showNotes]);
  useEffect(()=>{if(follow.current&&history.current)history.current.scrollTop=history.current.scrollHeight;},[last?.id,last?.text,last?.status,showNotes,notes.length]);
  const rate=revision.snapshot.sample_rate??44100,duration=revision.snapshot.duration_samples??0;
  const a=scope==='whole'?0:Math.round(start*rate),b=scope==='whole'?duration:Math.round(end*rate);
  async function refresh(){
    const [items,messages]=await Promise.all([call<DialogueTurn[]>('dialogue.list',{project_id:revision.project_id,limit:1000}),
      call<Conversation[]>('conversations.list',{project_id:revision.project_id,limit:1000})]);
    setTurns(items);setNotes(messages.filter(m=>m.role==='note'));
  }
  useEffect(()=>{let live=true;const update=async()=>{
    const [items,messages]=await Promise.all([call<DialogueTurn[]>('dialogue.list',{project_id:revision.project_id,limit:1000}),call<Conversation[]>('conversations.list',{project_id:revision.project_id,limit:1000})]);
    if(live){setTurns(items);setNotes(messages.filter(m=>m.role==='note'));}
  };void update().catch(e=>{if(live)setError(e.message);});const timer=setInterval(()=>void update().catch(()=>{}),900);
    return()=>{live=false;clearInterval(timer);};},[revision.project_id]);
  async function submit(){if(!text.trim())return;setBusy(true);setError('');try{
    await call('dialogue.submit',{project_id:revision.project_id,revision_id:revision.id,text,start_sample:a,end_sample:b,
      track_id:editing?trackId:(scope==='whole'?null:trackId),login_mode:login,edit_scope:editing?{track_ids:trackId?[trackId]:revision.snapshot.tracks?.map(t=>t.id)??[],start_sample:structural?0:a,end_sample:structural?duration:b,structural}:null},headId,true);setText('');await refresh();
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function control(turn:DialogueTurn,operation:'dialogue.cancel'|'dialogue.retry'){
    try{await call(operation,{project_id:revision.project_id,turn_id:turn.id},headId,true);await refresh();}catch(e){setError((e as Error).message);}}
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
  function reply(turn:DialogueTurn){
    const chunks=turn.text.split(/(\[[^\]]*\]\(music:\/\/citation\/[a-f0-9]{32}\))/g);
    return chunks.map((chunk,i)=>{const match=/^\[([^\]]*)\]\(music:\/\/citation\/([a-f0-9]{32})\)$/.exec(chunk);
      const citation=match&&turn.citations.find(c=>c.id===match[2]);
      return citation?<button key={i} className="inline-citation" onClick={()=>onLocate(citation)}>{match![1]} ↗</button>:<span key={i}>{chunk.split(/(\*\*[^*]+\*\*)/g).map((part,j)=>part.startsWith('**')?<strong key={j}>{part.slice(2,-2)}</strong>:part)}</span>;});
  }
  return <div className="discussion" aria-label="工程讨论">
    <div className="discussion-heading"><span className="eyebrow">STUDY WITH AI</span><h3>让音乐，成为对话。</h3><p>基于工程事件研究和声、配器与节奏。</p><div className="discussion-toggle"><button aria-pressed={!showNotes} onClick={()=>setShowNotes(false)}>讨论 {turns.length}</button><button aria-pressed={showNotes} onClick={()=>setShowNotes(true)}>笔记 {notes.length}</button></div></div>
    <div className="discussion-history" ref={history} onScroll={e=>{const n=e.currentTarget;follow.current=n.scrollHeight-n.scrollTop-n.clientHeight<65;}} aria-live="polite">{showNotes?notes.map(note=><article className="discussion-note" key={note.id}><small>{note.context?.origin==='user'?'用户笔记':'AI 解释 · 待核对'} · {String(note.context?.state)} · {note.revision_id.slice(0,8)}</small><p>{note.content}</p></article>):<>
      {!turns.length&&<div className="discussion-empty"><span>✧</span><strong>从一个好问题开始。</strong><p>这首歌如何积累张力？<br/>这段贝斯怎样与和弦呼应？</p><button onClick={()=>setText('请先查询整曲摘要，从和声、配器与节奏三个角度提出值得深入研究的问题，并给出工程引用。')}>研究整曲结构 ↗</button></div>}
      {turns.map(turn=><article key={turn.id} className="discussion-turn" data-turn={turn.id} data-status={turn.status}>
        <div className="question-bubble">{String(turn.payload.text)}</div><div className="turn-meta"><i className={active(turn)?'pulsing':''}/><strong>Codex</strong><span>{statusText[turn.status]} · {turn.revision_id.slice(0,8)}</span></div>
        <small className="turn-range">{time(Number(turn.context.start_sample)/Number(turn.context.sample_rate))}–{time(Number(turn.context.end_sample)/Number(turn.context.sample_rate))} · {turn.context.track_id?'指定声部':'全部声部'}</small>
        {turn.text&&<div className="discussion-reply">{reply(turn)}</div>}{turn.error&&<p className="discussion-error">{String(turn.error.code)} · {String(turn.error.message)}</p>}
        {!!turn.citations.length&&<div className="citation-list">{turn.citations.map(c=><button key={c.id} onClick={()=>onLocate(c)} title={c.revision_id}>{c.label} ↗</button>)}</div>}
        {!!turn.tools.length&&<details className="tool-history"><summary>{turn.tools.length} 条工程查询 · 查看依据</summary>{turn.tools.map((tool,i)=><details key={i}><summary>{String((tool.arguments as Record<string,unknown>)?.action??'summary')} · {tool.success?'完成':'失败'}</summary><pre>{JSON.stringify(tool,null,2)}</pre></details>)}</details>}
        <div className="turn-actions">{active(turn)?<button onClick={()=>void control(turn,'dialogue.cancel')}>停止</button>:turn.status==='succeeded'?<button onClick={()=>void save(turn)}>保存为研究笔记</button>:<button onClick={()=>void control(turn,'dialogue.retry')}>重试此问题</button>}</div>
        {!!turn.context.arrangement&&(()=>{const receipt=turn.context.arrangement as {revision_id:string;before_revision:string;render_job_id:string;diff:Record<string,{added?:string[];removed?:string[];changed?:string[]}>};
          const render=jobs.find(j=>j.id===receipt.render_job_id),exports=jobs.filter(j=>j.kind==='project.export'&&j.revision_id===receipt.revision_id);
          return <div className="arrangement-receipt" data-arrangement-revision={receipt.revision_id}><small>已提交新版本 {receipt.revision_id.slice(0,8)}</small><strong>{render?.status==='succeeded'?'● 试听已生成':render?.status==='failed'?'× 渲染失败':render?.status==='cancelled'?'○ 渲染已取消':'◌ 正在生成试听'}</strong><p>{Object.entries(receipt.diff).filter(([key])=>key!=='settings').filter(([,v])=>(v.added?.length??0)+(v.removed?.length??0)+(v.changed?.length??0)>0).map(([key,v])=>`${key} +${v.added?.length??0} −${v.removed?.length??0} Δ${v.changed?.length??0}`).join(' · ')}</p>
            {render?.error&&<p className="discussion-error">{String(render.error.code)}</p>}<div><button onClick={()=>onCompare(receipt.before_revision)}>对比修改</button><button onClick={()=>void exportVersion(turn,'midi')}>导出 MIDI</button><button disabled={render?.status!=='succeeded'} onClick={()=>void exportVersion(turn,'wav')}>导出 WAV</button><button disabled={render?.status!=='succeeded'} onClick={()=>void exportVersion(turn,'hybrid')}>生成贝斯混合</button></div>
            {exports.slice(-4).map(job=><p key={job.id}>{String(job.payload.format).toUpperCase()} · {statusText[job.status]} {job.status==='succeeded'&&typeof job.result?.asset_id==='string'&&<a href={'/v1/assets/'+job.result.asset_id} download>下载 ↗</a>}{job.error&&<span className="discussion-error"> {String(job.error.code)}</span>}</p>)}</div>;})()}
      </article>)}</>}
    </div>
    <div className="discussion-compose"><div className="discussion-context"><select aria-label="讨论范围" value={scope} onChange={e=>setScope(e.target.value)}><option value="whole">整曲</option><option value="selection">循环区间 / 所选声部</option></select><code>{revision.id.slice(0,8)}</code></div><small>{time(a/rate)}–{time(b/rate)} · {(scope==='selection'||editing)&&trackId?'指定声部':'全部声部'} · 版本已固定</small>
      <div className="discussion-intent"><button aria-label="仅讨论模式" aria-pressed={!editing} onClick={()=>setEditing(false)}>讨论</button><button aria-label="允许改编模式" aria-pressed={editing} onClick={()=>{setEditing(true);setScope('selection');}}>改编</button>{editing&&<label><input type="checkbox" aria-label="允许整轨插件和自动化" checked={structural} onChange={e=>setStructural(e.target.checked)}/>允许整轨插件 / 自动化</label>}</div>
      <textarea aria-label="讨论问题" placeholder="研究和弦、配器、节奏……" value={text} onChange={e=>setText(e.target.value)} rows={3} maxLength={12000}/>
      <div className="compose-actions"><select aria-label="Codex 登录方式" value={login} onChange={e=>setLogin(e.target.value)}><option value="isolated">应用独立登录</option><option value="existing">使用既有 Codex 登录</option></select><button disabled={!text.trim()||busy} onClick={()=>void save()}>记笔记</button><button className="send-discussion" disabled={!text.trim()||busy||turns.some(active)} onClick={()=>void submit()}>{busy?'保存中':editing?'改编 ↗':'发送 ↗'}</button></div>
      <p className="discussion-footnote">使用 Codex 账户额度。基于事件与候选分析，未向模型发送音频。</p>{error&&<p className="discussion-error" role="alert">{error}</p>}
    </div>
  </div>;
}
