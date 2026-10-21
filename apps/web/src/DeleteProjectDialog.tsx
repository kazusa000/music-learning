import {useEffect,useRef} from 'react';
import {Icon} from './Icon';

export function DeleteProjectDialog({title,busy,onCancel,onConfirm}:{title:string;busy:boolean;onCancel:()=>void;onConfirm:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const node=dialog.current!;node.showModal();return()=>node.close();},[]);
  return <dialog ref={dialog} className="delete-project-dialog" aria-labelledby="delete-project-title" onCancel={event=>{event.preventDefault();if(!busy)onCancel();}}>
    <div className="delete-project-symbol"><Icon name="trash" size={23}/></div>
    <h2 id="delete-project-title">彻底删除工程？</h2>
    <p className="delete-project-name">{title}</p>
    <p>删除全部版本、讨论和工程专属音频，不保留备份，无法恢复。</p>
    <small>其他工程共用的素材、外部原曲和音源库保留。</small>
    <footer><button autoFocus disabled={busy} onClick={onCancel}>取消</button><button className="danger-button" disabled={busy} onClick={onConfirm}>{busy?'正在提交…':'彻底删除'}</button></footer>
  </dialog>;
}
