import {useEffect,useRef,useState} from 'react';
import {Icon} from './Icon';

export function NewCompositionDialog({busy,onCancel,onCreate}:{busy:boolean;onCancel:()=>void;onCreate:(args:{title:string;tempo:number;bars:number;beats_per_bar:number})=>Promise<void>}){
  const ref=useRef<HTMLDialogElement>(null);
  const [title,setTitle]=useState('未命名作品'),[tempo,setTempo]=useState(120),[bars,setBars]=useState(32),[beats,setBeats]=useState(4);
  useEffect(()=>{ref.current?.showModal();},[]);
  return <dialog className="composition-dialog" ref={ref} onCancel={e=>{e.preventDefault();if(!busy)onCancel();}}>
    <form onSubmit={e=>{e.preventDefault();void onCreate({title:title.trim(),tempo,bars,beats_per_bar:beats});}}>
      <div className="composition-dialog-heading"><span className="creation-symbol"><Icon name="piano" size={21}/></span><div><h2>新建编曲</h2><p>从一个空白乐器轨开始。</p></div><button type="button" aria-label="关闭新建编曲" disabled={busy} onClick={onCancel}>×</button></div>
      <label>作品名称<input autoFocus aria-label="作品名称" required maxLength={256} value={title} onChange={e=>setTitle(e.target.value)}/></label>
      <div className="composition-grid"><label>速度 · BPM<input aria-label="初始速度" type="number" required min={20} max={300} step={.1} value={tempo} onChange={e=>setTempo(+e.target.value)}/></label><label>拍号<select aria-label="初始拍号" value={beats} onChange={e=>setBeats(+e.target.value)}>{[2,3,4,5,6,7].map(n=><option key={n} value={n}>{n}/4</option>)}</select></label><label>长度 · 小节<input aria-label="初始小节数" type="number" required min={1} max={2048} value={bars} onChange={e=>setBars(+e.target.value)}/></label></div>
      <div className="composition-dialog-actions"><button type="button" disabled={busy} onClick={onCancel}>取消</button><button type="submit" className="primary-button" disabled={busy||!title.trim()}>{busy?'正在创建…':'进入编曲'}<Icon name="timeline" size={14}/></button></div>
    </form>
  </dialog>;
}
