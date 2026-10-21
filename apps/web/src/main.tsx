import './style.css';
import {PreferencesProvider} from './Preferences';
import {SettingsPage} from './SettingsPage';
import {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import type {CSSProperties} from 'react';
import {PanelResize,usePanelSize} from './PanelResize';
import type {Project, ProjectDeletion, Job, Revision, StageRecord, PreprocessingView, PreprocessingResult, PlaybackPlan} from '../../../packages/contracts/types';
import {call, connect} from './api';
import {Workbench} from './Workbench';
import {publishDomain} from './events';
import {AudioPreview} from './AudioPreview';
import {Icon} from './Icon';
import {ImportProgress} from './ImportProgress';
import {DeleteProjectDialog} from './DeleteProjectDialog';
import {listeningCalibration} from './listeningCalibration';
import {WindowControls,dragWindow} from './WindowChrome';
import {useStudioMouse} from './useStudioMouse';

const connection = connect();
const statuses: Record<string, string> = {queued: '排队中', running: '正在处理', cancel_requested: '正在取消',
  cancelled: '已取消', succeeded: '已完成', failed: '处理失败'};
const kinds: Record<string, string> = {'asset.import': '原曲导入与检查', 'plugin.inspect': '读取插件状态', 'plugin.scan': '扫描插件', 'project.preprocess': '整曲预处理', 'project.render':'导出混音', 'audio.measure':'音频测量', 'instrument.capture':'准备音色', 'project.export':'导出指定版本', 'application.backup':'完整工程备份', 'application.restore':'恢复到独立目录', 'application.diagnose':'本机环境诊断'};
const projectStatuses: Record<string, string> = {empty:'等待导入', imported:'原曲已保存', queued:'等待整曲处理', processing:'整曲处理中', ready:'可编辑 · 可试听', failed:'处理失败', cancelled:'处理已取消'};
const stageNames: Record<string, string> = {normalize:'音频规范化', beat_this:'速度与小节网格', muscriptor:'多乐器音符转录', assemble:'事件对齐', render:'VST3 整曲与逐轨重建', calibrate:'校准试听响度', validate:'完整性检查'};


function App() {
  useStudioMouse();
  const [settingsOpen,setSettingsOpen]=useState(new URLSearchParams(location.search).get('view')==='settings');
  const [libraryWidth,setLibraryWidth]=usePanelSize('library.sidebar-width',260,180,500);
  const [projects, setProjects] = useState<Project[]>([]);
  const [deletions,setDeletions]=useState<ProjectDeletion[]>([]);
  const [deleteTarget,setDeleteTarget]=useState<Project|null>(null);
  const [deleteSubmitting,setDeleteSubmitting]=useState(false);
  const [deleteNotice,setDeleteNotice]=useState('');
  const watchedDeletes=useRef(new Set<string>());
  const [selected, setSelected] = useState<string | null>(new URLSearchParams(location.search).get('project') ?? localStorage.getItem('selected-project'));
  const activeSelection = useRef(selected);
  const [revision, setRevision] = useState<Revision | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [versionCount, setVersionCount] = useState(0);
  const [error, setError] = useState('');
  const [refreshError,setRefreshError]=useState('');
  const [busy, setBusy] = useState(false);
  const [vocalMode,setVocalMode]=useState('auto');
  const [uploadProgress,setUploadProgress]=useState<number|null>(null);
  const [connected, setConnected] = useState(false);
  const [publication, setPublication] = useState<PreprocessingResult | null>(null);
  const [playbackReady,setPlaybackReady]=useState(false);
  const [stages, setStages] = useState<Record<string, StageRecord[]>>({});
  const [workspace, setWorkspace] = useState(new URLSearchParams(location.search).get('view') === 'workbench');
  const detailCache = useRef<{id:string; revision:Revision; publication:PreprocessingResult | null;playbackReady:boolean} | null>(null);
  const homeAudio=useRef<HTMLAudioElement|null>(null);
  useEffect(()=>{
    if(workspace||settingsOpen)return;
    const key=(event:KeyboardEvent)=>{
      const target=event.target as HTMLElement|null;
      if(event.code!=='Space'||event.repeat||event.ctrlKey||event.metaKey||event.altKey||target?.closest('input,textarea,select,[contenteditable="true"]'))return;
      const audio=homeAudio.current;if(!audio)return;
      event.preventDefault();event.stopPropagation();
      if(audio.paused)void audio.play().catch(e=>setError(e.message));else audio.pause();
    };
    window.addEventListener('keydown',key,true);return()=>window.removeEventListener('keydown',key,true);
  },[workspace,settingsOpen]);
  const refreshGeneration = useRef(0);
  const [showArchived,setShowArchived]=useState(false);
  const project = projects.find(p => p.id === selected);

  useEffect(() => {if (selected) localStorage.setItem('selected-project', selected);}, [selected]);

  function selectProject(id: string) {
    activeSelection.current = id;
    setSelected(id); setRevision(null); setJobs([]); setVersionCount(0);
    setPublication(null); setWorkspace(false); setStages({});
    history.replaceState(null, '', '/');
    localStorage.setItem('selected-project', id);
  }

  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
    await connection;
    const [list,deleting]=await Promise.all([
      call<Project[]>('projects.list', {limit:1000,include_archived:showArchived}),
      call<ProjectDeletion[]>('projects.deletions.list',{limit:1000}),
    ]);
    if (activeSelection.current !== selected || generation !== refreshGeneration.current) return;
    setProjects(list);
    setDeletions(deleting);
    for(const item of deleting){if(item.status==='completed'&&watchedDeletes.current.has(item.project_id)){
      watchedDeletes.current.delete(item.project_id);setDeleteNotice(`已彻底删除「${item.title}」，未创建备份。`);
    }}
    if(!list.some(p=>p.id===selected)&&list.length){selectProject(list[0].id);return;}
    if(!list.length&&selected){
      activeSelection.current=null;setSelected(null);localStorage.removeItem('selected-project');
      setWorkspace(false);history.replaceState(null,'','/');detailCache.current=null;
    }
    if(deleting.some(d=>d.project_id===selected&&d.status!=='completed'))return;
    if (selected && list.some(p => p.id === selected)) {
      const headId = list.find(p => p.id === selected)!.current_revision;
      const cached = detailCache.current?.id === headId ? detailCache.current : null;
      const [head, readiness, tasks, versions, renders, plan] = await Promise.all([
        cached ? Promise.resolve(cached.revision) : call<Revision>('revisions.get', {project_id: selected, revision_id:headId}),
        cached ? Promise.resolve({publication:cached.publication}) : call<PreprocessingView>('preprocessing.get', {project_id:selected, revision_id:headId}),
        call<Job[]>('jobs.list', {project_id: selected, limit: 1000}),
        call<unknown[]>('versions.list', {project_id: selected, limit: 1000}),
        call<Array<PreprocessingResult & {start_sample:number;end_sample:number}>>('renders.list', {project_id:selected,revision_id:headId}),
        cached?Promise.resolve({available:cached.playbackReady,reason:null}):call<PlaybackPlan>('playback.get',{project_id:selected,revision_id:headId}),
      ]);
      if (activeSelection.current !== selected || generation !== refreshGeneration.current) return;
      const media = renders.filter(r => r.start_sample===0&&r.end_sample===head.snapshot.duration_samples).at(-1) ?? readiness.publication;
      detailCache.current = {id:headId, revision:head, publication:media,playbackReady:plan.available||(!plan.reason&&(head.snapshot.tracks?.length??0)>0)};
      setPlaybackReady(plan.available||(!plan.reason&&(head.snapshot.tracks?.length??0)>0));
      setRevision(head); setPublication(media); setJobs(tasks); setVersionCount(versions.length);
      const details = await Promise.all(tasks.filter(j => j.kind === 'project.preprocess').map(async job =>
        [job.id, await call<StageRecord[]>('jobs.stages', {job_id:job.id})] as const));
      if (activeSelection.current === selected && generation === refreshGeneration.current) {setStages(Object.fromEntries(details));setRefreshError('');}
    } else {setRevision(null); setJobs([]); setVersionCount(0);setPublication(null);setPlaybackReady(false);setRefreshError('');}
  }, [selected,showArchived]);

  useEffect(() => {
    let active = true;
    let stream: EventSource | null = null;
    let debounce: ReturnType<typeof setTimeout> | undefined;
    const update = () => {void refresh().catch(e => {if (active) setRefreshError(String(e.message));});};
    void connection.then(() => {
      if (!active) return;
      update();
      stream = new EventSource('/v1/events?live_only=true');
      let opened=false;
      stream.onopen = () => {setConnected(true);if(opened){update();publishDomain({type:'connection.ready',project_id:null,revision_id:null,job_id:null});}opened=true;};
      stream.onerror = () => setConnected(false);
      stream.addEventListener('domain', event => {
        const domain=JSON.parse((event as MessageEvent).data);publishDomain(domain);
        if(domain.type.startsWith('dialogue.')||domain.type.startsWith('audition.')||domain.type==='note.saved')return;
        clearTimeout(debounce);debounce=setTimeout(update,120);
      });
    }).catch(e => setRefreshError(e.message));
    const timer = setInterval(update, 15000);
    return () => {active = false; clearInterval(timer); clearTimeout(debounce); stream?.close();};
  }, [refresh]);

  async function importFile(file: File) {
    setBusy(true); setError('');setUploadProgress(0);
    try {
      const created = await call<Project>('projects.create', {title: file.name.replace(/\.[^.]+$/, ''), kind: 'research'}, undefined, true);
      selectProject(created.id);
      const upload = await new Promise<{upload_id:string}>((resolve,reject)=>{
        const xhr=new XMLHttpRequest();xhr.open('PUT','/v1/uploads');
        xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.setRequestHeader('Idempotency-Key',crypto.randomUUID());
        xhr.upload.onprogress=event=>{if(event.lengthComputable)setUploadProgress(Math.round(event.loaded/event.total*100));};
        xhr.onerror=()=>reject(new Error('音频上传中断，请重试。'));
        xhr.onload=()=>{try{const data=JSON.parse(xhr.responseText);if(xhr.status>=200&&xhr.status<300&&data.ok)resolve(data.result);else reject(new Error(data.error?.message??'音频上传失败'));}catch{reject(new Error('无法读取上传结果'));}};
        xhr.send(file);
      });
      setUploadProgress(null);
      await call('jobs.submit', {project_id: created.id, kind: 'asset.import', payload: {upload_id: upload.upload_id, preprocess:true,vocal_mode:vocalMode}}, created.current_revision, true);
      await refresh();
    } catch (e) {setError((e as Error).message);} finally {setBusy(false);setUploadProgress(null);}
  }

  async function cancel(job: Job) {
    if (!project) return;
    try {await call('jobs.cancel', {job_id: job.id}, project.current_revision, true); await refresh();}
    catch (e) {setError((e as Error).message);}
  }

  async function startPreprocessing(job?: Job) {
    if (!project) return;
    try {
      if (job) await call('jobs.retry', {job_id:job.id}, project.current_revision, true);
      else await call('jobs.submit', {project_id:project.id, kind:'project.preprocess', payload:{vocal_mode:vocalMode}}, project.current_revision, true);
      await refresh();
    } catch(e) {setError((e as Error).message);}
  }

  async function organize(){
    if(!project)return;
    try{await call('projects.organize',{project_id:project.id,archived:!project.archived,display_title:project.display_title??project.title},project.current_revision,true);await refresh();}
    catch(e){setError((e as Error).message);}
  }

  async function deleteProject(target:{id:string;current_revision:string}){
    setDeleteSubmitting(true);setError('');setDeleteNotice('');
    if(target.id===activeSelection.current){homeAudio.current?.pause();homeAudio.current=null;}
    try{
      watchedDeletes.current.add(target.id);
      const receipt=await call<ProjectDeletion>('projects.delete',{project_id:target.id},target.current_revision,true);
      setDeletions(items=>[receipt,...items.filter(d=>d.project_id!==target.id)]);
      setDeleteTarget(null);detailCache.current=null;
      await refresh();
    }catch(e){setDeleteTarget(null);setError((e as Error).message);watchedDeletes.current.delete(target.id);}
    finally{setDeleteSubmitting(false);}
  }

  return <main className={workspace ? 'workspace-page' : 'library-page'}>
    <header hidden={workspace&&!settingsOpen&&!!project&&!!revision} onPointerDown={dragWindow}><a className="brand" href="/" aria-label="Music Learning 音乐库"><span className="brand-mark" aria-hidden="true"><i/><i/><i/></span><b>Music Learning</b><span className="brand-divider"/><small>{workspace?'Studio':'音乐库'}</small></a>
      <div className="header-actions">{!workspace&&<button className="header-settings" onClick={()=>{homeAudio.current?.pause();setSettingsOpen(true);history.replaceState(null,'','/?view=settings');}}>设置</button>}<span className={'connection '+(connected?'is-connected':'')}><i/>{connected ? '本机已连接' : '正在连接'}</span><WindowControls/></div></header>
    {(error||refreshError) && <div role="alert">{error||refreshError}<button onClick={() => {setError('');setRefreshError('');}} aria-label="关闭提示">×</button></div>}
    {!workspace&&deleteNotice&&<div className="deletion-notice" role="status">{deleteNotice}<button aria-label="关闭删除提示" onClick={()=>setDeleteNotice('')}>×</button></div>}
    {!workspace&&deletions.filter(d=>d.status!=='completed').map(d=><div key={d.project_id} className={'deletion-notice '+(d.status==='failed'?'has-failed':'')} role={d.status==='failed'?'alert':'status'}>
      <span>{d.status==='failed'?`「${d.title}」删除未完成：${d.error}`:`正在彻底删除「${d.title}」${d.status==='waiting'?' · 正在停止处理任务':' · 清理工程文件'}`}</span>
      {d.status==='failed'&&<button disabled={deleteSubmitting} onClick={()=>void deleteProject({id:d.project_id,current_revision:d.revision_id})}>重试删除</button>}
    </div>)}
    {deleteTarget&&<DeleteProjectDialog title={deleteTarget.display_title??deleteTarget.title} busy={deleteSubmitting} onCancel={()=>setDeleteTarget(null)} onConfirm={()=>void deleteProject(deleteTarget)}/>}
    {settingsOpen?<SettingsPage onBack={()=>{setSettingsOpen(false);history.replaceState(null,'','/');}}/>:workspace && project && revision ? <Workbench key={project.id} title={project.display_title??project.title} revision={revision} publication={publication} jobs={jobs} onCommitted={refresh} onReimport={async()=>{const sha=revision.snapshot.source_asset_id;if(!sha)throw new Error('此工程没有原曲');const response=await fetch('/v1/assets/'+sha);if(!response.ok)throw new Error('原曲读取失败');const blob=await response.blob();setWorkspace(false);history.replaceState(null,'','/');await importFile(new File([blob],(project.display_title??project.title)+' · 新编排.audio',{type:blob.type}));}}
      onBack={() => {setWorkspace(false); history.replaceState(null, '', '/');}}/> : <div className="layout resizable-library" style={{'--library-width':libraryWidth+'px'} as CSSProperties}><aside>
      <label className={'import ' + (busy ? 'disabled' : '')}><Icon name="upload" size={20}/><div className="import-copy"><strong>{busy ? `上传音频 ${uploadProgress??100}%` : '选择音乐'}</strong><span>FLAC / WAV / MP3 / OGG</span></div><input aria-label="选择音乐" type="file" accept="audio/*,.flac,.wav,.mp3,.ogg,.m4a"
          disabled={busy} onChange={e => {const file = e.target.files?.[0]; if (file) void importFile(file); e.target.value = '';}}/></label>
      <label className="import-vocal-mode">导入类型<select aria-label="导入人声类型" disabled={busy} value={vocalMode} onChange={e=>setVocalMode(e.target.value)}><option value="auto">自动判断</option><option value="vocal">有人声</option><option value="instrumental">纯音乐 · 跳过分离</option></select></label>
      <div className="list-title">歌曲 <span>{projects.length}</span></div><label className="archive-toggle"><input type="checkbox" checked={showArchived} onChange={e=>setShowArchived(e.target.checked)}/>显示归档工程</label>
      <nav aria-label="工程列表">{projects.map(p => <div className="project-row" key={p.id}><button className="project-open" aria-pressed={selected === p.id} disabled={deletions.some(d=>d.project_id===p.id&&d.status!=='completed')} onClick={() => {
        selectProject(p.id);
      }}><span className="project-miniature" aria-hidden="true"><Icon name="music" size={18}/></span><span className="project-list-copy"><strong>{p.display_title??p.title}</strong><span>{p.archived?'已归档 · ':''}{projectStatuses[p.status]}</span></span></button><button className="project-delete" aria-label={'删除工程 '+(p.display_title??p.title)} title="彻底删除工程" disabled={busy||deleteSubmitting||deletions.some(d=>d.project_id===p.id&&d.status!=='completed')} onClick={()=>setDeleteTarget(p)}><Icon name="trash" size={15}/></button></div>)}</nav>
      {!projects.length && <p className="muted empty-list">还没有工程。选一首完整音乐，保存在本机。</p>}
    </aside><PanelResize label="调整歌曲列表宽度" value={libraryWidth} min={180} max={500} onChange={setLibraryWidth} reset={260}/><section className="detail" aria-label="工程详情">
      {!project ? <div className="welcome"><h2>选择歌曲</h2><p>导入音频，或打开左侧已有歌曲。</p></div> : <>
        <div className="project-heading"><div className="project-identity"><div className="project-art" aria-hidden="true"><Icon name="wave" size={29}/></div><div><p>音乐工程</p><h2>{project.display_title??project.title}</h2></div></div>
          <div className="library-actions"><span className="badge">{projectStatuses[project.status]}</span><button onClick={()=>void organize()}>{project.archived?'恢复到歌曲列表':'归档工程'}</button><button className="project-delete-label" disabled={busy||deleteSubmitting||deletions.some(d=>d.project_id===project.id&&d.status!=='completed')} onClick={()=>setDeleteTarget(project)}><Icon name="trash" size={13}/>删除工程</button></div></div>
        <div className="facts"><div><span>保存版本</span><b>{versionCount}</b></div>
          <div><span>原曲长度</span><b>{revision?.snapshot.duration_samples ? (revision.snapshot.duration_samples / (revision.snapshot.sample_rate ?? 44100)).toFixed(1) + ' 秒' : '—'}</b></div></div>
        {revision?.snapshot.source_asset_id && <section className="original"><h3>原曲试听</h3>
          <AudioPreview key={'source-'+revision.id} label="原曲试听" assetId={revision.snapshot.source_asset_id} duration={(revision.snapshot.duration_samples??0)/(revision.snapshot.sample_rate??44100)} initial={!publication} onSelect={a=>{homeAudio.current=a;}}/>
          {publication&&<><h3>重建试听</h3><AudioPreview key={'mix-'+publication.mix_asset_id} label="重建试听" gainDb={listeningCalibration(revision.snapshot.listening_calibration)?.mix_gain_db??0} assetId={publication.mix_asset_id} duration={(revision.snapshot.duration_samples??0)/(revision.snapshot.sample_rate??44100)} initial onSelect={a=>{homeAudio.current=a;}}/></>}
          <p className="muted">{publication ? '已可播放重建、独奏声部、编辑音符和与 AI 讨论。' : playbackReady?'工程已可试听，打开工程继续编曲。':'正在准备可编辑的音乐工程。'}</p>
          {(publication || (revision.snapshot.tracks?.length ?? 0)>0) && <button className="primary-button" onClick={() => {setWorkspace(true); history.replaceState(null,'',`/?project=${project.id}&view=workbench`);}}><span>打开工程</span><Icon name="arrow" size={15}/></button>}
          {!publication && !(revision.snapshot.tracks?.length??0) && !jobs.some(j => j.kind === 'project.preprocess' && ['queued','running','cancel_requested'].includes(j.status)) &&
            <button className="primary-button" onClick={() => void startPreprocessing()}>开始整曲分析</button>}
        </section>}
        {jobs.filter(j=>j.kind==='project.preprocess').sort((a,b)=>b.created_at-a.created_at).slice(0,1).map(job=><ImportProgress key={job.id} job={job} stages={stages[job.id]??[]} onCancel={()=>void cancel(job)}/>)}
        <details className="tasks"><summary>处理记录 · {jobs.length}</summary>
          {!jobs.length && <p className="muted">暂无任务。</p>}
          {jobs.map(job => <div className="task" key={job.id} data-job-id={job.id} data-status={job.status}>
            <div><strong>{kinds[job.kind]}</strong>
              {job.error && <p className="failure">{String(job.error.code)} · {String(job.error.message)}</p>}</div>
            <span className={'status ' + job.status}>{statuses[job.status]}</span>
            {['queued', 'running'].includes(job.status) && <button className="cancel" onClick={() => void cancel(job)}>取消</button>}
            {['failed','cancelled'].includes(job.status) && !['application.backup','application.restore'].includes(job.kind) && <button className="cancel" onClick={() => void startPreprocessing(job)}>重试</button>}
            {(stages[job.id]?.length ?? 0) > 0 && <ol className="stage-list">{stages[job.id].map(stage => <li key={stage.stage} data-stage={stage.stage} className={stage.status}>
              <span>{stageNames[stage.stage]}</span><span>{stage.cache_hit ? '已复用校验产物' : stage.status === 'pending' ? '等待' : statuses[stage.status] ?? stage.status}
                {typeof stage.progress?.completed === 'number' && typeof stage.progress?.total === 'number' ? ` · ${stage.progress.completed}/${stage.progress.total} 块` : ''}</span>
            </li>)}</ol>}
          </div>)}
        </details>
        <footer>工程保存在本机 · 每个版本独立保留 · {new Date(project.created_at * 1000).toLocaleDateString('zh-CN')}</footer>
      </>}
    </section></div>}
  </main>;
}

createRoot(document.getElementById('root')!).render(<PreferencesProvider ready={connection}><App/></PreferencesProvider>);
