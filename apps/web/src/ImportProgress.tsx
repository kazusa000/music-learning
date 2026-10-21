import type {Job,StageRecord} from '../../../packages/contracts/types';

const steps=[['normalize','整理音频',5],['beat_this','识别速度与小节',10],['muscriptor','还原多乐器音符',65],['assemble','建立音轨与音色',5],['render','准备重建试听',8],['calibrate','校准试听响度',4],['validate','检查完整工程',3]] as const;

export function ImportProgress({job,stages,onCancel}:{job:Job;stages:StageRecord[];onCancel:()=>void}){
  const finished=job.status==='succeeded';
  const active=steps.find(([key])=>stages.find(s=>s.stage===key)?.status==='running');
  const fraction=(s:StageRecord|undefined)=>{
    if(s?.status==='succeeded')return 1;
    const {completed,total}=s?.progress??{};
    return typeof completed==='number'&&typeof total==='number'&&total>0?Math.max(0,Math.min(1,completed/total)):0;
  };
  const progress=finished?100:Math.floor(steps.reduce((n,[key,,weight])=>n+weight*fraction(stages.find(s=>s.stage===key)),0));
  const current=stages.find(s=>s.stage===active?.[0]);
  const done=current?.progress?.completed,total=current?.progress?.total;
  const failed=job.status==='failed'||job.status==='cancelled';
  return <section className={'import-progress '+(failed?'has-failed':'')} aria-label="整曲导入进度" data-status={job.status}>
    <div className="import-progress-heading"><span className="import-orbit" aria-hidden="true">♪</span><div><span className="import-eyebrow">整曲导入</span><h3>{finished?'工程已准备好':failed?(job.status==='cancelled'?'导入已取消':'导入未完成'):active?.[1]??'正在等待处理'}</h3></div><strong>{progress}<small>%</small></strong></div>
    <div className="import-progress-track" role="progressbar" aria-label="工程准备进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><i style={{width:progress+'%'}}/></div>
    <div className="import-progress-meta"><span>{typeof done==='number'&&typeof total==='number'&&active?.[0]==='muscriptor'?`已转录 ${done} / ${total} 个音频块`:'音频 → 音符 → 可编辑工程'}</span>{['queued','running'].includes(job.status)&&<button onClick={onCancel}>取消</button>}</div>
    <ol className="import-steps">{steps.map(([key,label],i)=>{const stage=stages.find(s=>s.stage===key);return <li key={key} data-status={stage?.status??'pending'}><i>{stage?.status==='succeeded'?'✓':i+1}</i><span>{label}</span></li>;})}</ol>
    {job.error&&<p className="failure">{String(job.error.message??'请查看处理记录后重试。')}</p>}
  </section>;
}
