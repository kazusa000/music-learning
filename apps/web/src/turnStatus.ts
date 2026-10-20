import type {DialogueTurn,Job} from '../../../packages/contracts/types';

export function turnStatus(turn:DialogueTurn,jobs:Job[]){
  if(turn.context.arrangement)return '工程已更新';
  if(turn.status==='queued')return '等待回复';
  if(turn.status==='running')return '正在处理';
  if(turn.status==='cancel_requested')return '正在停止';
  if(turn.status==='cancelled')return '已停止';
  const captures=turn.context.preset_captures as string[]|undefined;
  if(captures?.some(id=>jobs.some(j=>j.id===id&&['failed','cancelled'].includes(j.status))))return '音色未更换';
  if(turn.tools.some(t=>t.tool==='music_edit'&&!t.success))return '修改未完成';
  return turn.status==='succeeded'?'回复结束':'回复中断';
}
