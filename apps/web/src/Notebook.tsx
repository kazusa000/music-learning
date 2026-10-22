import {useCallback,useEffect,useState} from 'react';
import type {Conversation,Revision} from '../../../packages/contracts/types';
import {call} from './api';
import {subscribeDomain} from './events';
import {MusicMarkdown} from './MusicMarkdown';
import {Icon} from './Icon';
import './notebook.css';

export function Notebook({revision,headId}:{revision:Revision;headId:string}){
  const key='notebook.drafts.'+revision.project_id;
  const [notes,setNotes]=useState<Conversation[]>([]),[selected,setSelected]=useState<string|null>(null);
  const [drafts,setDrafts]=useState<Record<string,string>>(()=>{try{return JSON.parse(localStorage.getItem(key)??'{}');}catch{return {};}});
  const [preview,setPreview]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const note=notes.find(n=>n.id===selected),draftKey=selected??'new',content=drafts[draftKey]??note?.content??'';
  const dirty=drafts[draftKey]!==undefined;
  useEffect(()=>{localStorage.setItem(key,JSON.stringify(drafts));},[key,drafts]);
  const refresh=useCallback(async()=>setNotes(await call<Conversation[]>('notes.list',{project_id:revision.project_id,limit:1000})),[revision.project_id]);
  useEffect(()=>{let live=true;const update=()=>{if(live)void refresh().catch(e=>setError(e.message));};update();const unsub=subscribeDomain(e=>{if(e.type==='connection.ready'||e.project_id===revision.project_id&&e.type.startsWith('note.'))update();});return()=>{live=false;unsub();};},[refresh,revision.project_id]);
  function discardDraft(id:string){setDrafts(current=>{const next={...current};delete next[id];return next;});}
  async function save(){if(!content.trim()||busy)return;setBusy(true);setError('');try{
    const saved=await call<Conversation>('notes.save',{project_id:revision.project_id,revision_id:revision.id,note_id:selected,content},headId,true);
    discardDraft(draftKey);setSelected(saved.id);await refresh();
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function remove(){if(!selected||busy)return;setBusy(true);setError('');try{await call('notes.delete',{project_id:revision.project_id,note_id:selected},headId,true);discardDraft(selected);setSelected(null);await refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <section className="notebook" aria-label="工程笔记">
    <div className="notebook-heading"><div><strong>工程笔记</strong><small>{notes.length} 条 · 与 AI 共享</small></div><button aria-label="新建笔记" onClick={()=>{setSelected(null);setPreview(false);}}><Icon name="plus"/>新建</button></div>
    <div className="notebook-list" aria-label="笔记列表">{notes.map(n=><button key={n.id} aria-pressed={selected===n.id} onClick={()=>{setSelected(n.id);setPreview(false);}}><strong>{n.content.split('\n').find(s=>s.trim())?.replace(/^#+\s*/,'')??'笔记'}</strong><small>{n.context?.origin==='ai'||n.context?.origin==='interpretation'?'AI':'我的笔记'} · {new Date(n.created_at*1000).toLocaleDateString('zh-CN')}</small></button>)}</div>
    <div className="notebook-editor"><div className="notebook-tools"><span>{selected?'编辑笔记':'新笔记'}{dirty?' · 草稿':''}</span><button aria-pressed={preview} onClick={()=>setPreview(!preview)}>{preview?'继续编辑':'预览'}</button></div>
      {preview?<div className="notebook-preview"><MusicMarkdown text={content||'还没有内容。'}/></div>:<textarea aria-label="笔记内容" placeholder={'记下和弦思路、配器安排，或让 AI 帮你整理…\n\n支持 Markdown'} value={content} maxLength={12000} onChange={e=>setDrafts(current=>({...current,[draftKey]:e.target.value}))} onKeyDown={e=>{if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();void save();}}}/> }
    </div>
    {error&&<p role="alert">{error}</p>}
    <div className="notebook-footer"><span>{dirty?'草稿已暂存本机':'在对话里也可以让 AI 读写笔记'}</span>{selected&&<button disabled={busy} onClick={()=>void remove()} aria-label="删除笔记"><Icon name="trash" size={14}/></button>}<button className="primary-button" disabled={busy||!content.trim()} onClick={()=>void save()}>{busy?'保存中…':'保存笔记'}</button></div>
  </section>;
}
