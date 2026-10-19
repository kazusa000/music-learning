import {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import type {Project, Job, Revision, StageRecord, PreprocessingView, PreprocessingResult} from '../../../packages/contracts/types';
import {call, connect, result} from './api';
import {Workbench} from './Workbench';
import {publishDomain} from './events';
import './style.css';

const connection = connect();
const statuses: Record<string, string> = {queued: '排队中', running: '正在处理', cancel_requested: '正在取消',
  cancelled: '已取消', succeeded: '已完成', failed: '处理失败'};
const kinds: Record<string, string> = {'asset.import': '原曲导入与检查', 'plugin.inspect': '读取插件状态', 'plugin.scan': '扫描插件', 'project.preprocess': '整曲预处理', 'project.render':'工程音频渲染', 'audio.measure':'音频测量', 'project.export':'导出指定版本', 'application.backup':'完整工程备份', 'application.restore':'恢复到独立目录', 'application.diagnose':'本机环境诊断'};
const projectStatuses: Record<string, string> = {empty:'等待导入', imported:'原曲已保存', queued:'等待整曲处理', processing:'整曲处理中', ready:'整曲材料已齐备', failed:'处理失败', cancelled:'处理已取消'};
const stageNames: Record<string, string> = {normalize:'音频规范化', demucs:'音频分轨', muscriptor:'多声部转录', sheetsage:'节拍与和声结构', assemble:'事件对齐', render:'VST3 整曲与逐轨重建', validate:'完整性检查'};
const short = (value: string) => value.slice(0, 8);

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<string | null>(new URLSearchParams(location.search).get('project') ?? localStorage.getItem('selected-project'));
  const activeSelection = useRef(selected);
  const [revision, setRevision] = useState<Revision | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [versionCount, setVersionCount] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [publication, setPublication] = useState<PreprocessingResult | null>(null);
  const [stages, setStages] = useState<Record<string, StageRecord[]>>({});
  const [workspace, setWorkspace] = useState(new URLSearchParams(location.search).get('view') === 'workbench');
  const detailCache = useRef<{id:string; revision:Revision; publication:PreprocessingResult | null} | null>(null);
  const refreshGeneration = useRef(0);
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
    const list = await call<Project[]>('projects.list', {limit: 1000});
    if (activeSelection.current !== selected || generation !== refreshGeneration.current) return;
    setProjects(list);
    if (selected && list.some(p => p.id === selected)) {
      const headId = list.find(p => p.id === selected)!.current_revision;
      const cached = detailCache.current?.id === headId ? detailCache.current : null;
      const [head, readiness, tasks, versions, renders] = await Promise.all([
        cached ? Promise.resolve(cached.revision) : call<Revision>('revisions.get', {project_id: selected, revision_id:headId}),
        cached ? Promise.resolve({publication:cached.publication}) : call<PreprocessingView>('preprocessing.get', {project_id:selected, revision_id:headId}),
        call<Job[]>('jobs.list', {project_id: selected, limit: 1000}),
        call<unknown[]>('revisions.list', {project_id: selected, limit: 1000}),
        call<Array<PreprocessingResult & {start_sample:number;end_sample:number}>>('renders.list', {project_id:selected,revision_id:headId}),
      ]);
      if (activeSelection.current !== selected || generation !== refreshGeneration.current) return;
      const media = renders.filter(r => r.start_sample===0&&r.end_sample===head.snapshot.duration_samples).at(-1) ?? readiness.publication;
      detailCache.current = {id:headId, revision:head, publication:media};
      setRevision(head); setPublication(media); setJobs(tasks); setVersionCount(versions.length);
      const details = await Promise.all(tasks.filter(j => j.kind === 'project.preprocess').map(async job =>
        [job.id, await call<StageRecord[]>('jobs.stages', {job_id:job.id})] as const));
      if (activeSelection.current === selected && generation === refreshGeneration.current) setStages(Object.fromEntries(details));
    } else {setRevision(null); setJobs([]); setVersionCount(0);}
  }, [selected]);

  useEffect(() => {
    let active = true;
    let stream: EventSource | null = null;
    let debounce: ReturnType<typeof setTimeout> | undefined;
    const update = () => {void refresh().catch(e => {if (active) setError(String(e.message));});};
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
    }).catch(e => setError(e.message));
    const timer = setInterval(update, 15000);
    return () => {active = false; clearInterval(timer); clearTimeout(debounce); stream?.close();};
  }, [refresh]);

  async function importFile(file: File) {
    setBusy(true); setError('');
    try {
      const created = await call<Project>('projects.create', {title: file.name.replace(/\.[^.]+$/, ''), kind: 'research'}, undefined, true);
      selectProject(created.id);
      const upload = await result<{upload_id: string}>(await fetch('/v1/uploads', {method: 'PUT', body: file,
        headers: {'Content-Type': 'application/octet-stream', 'Idempotency-Key': crypto.randomUUID()}}));
      await call('jobs.submit', {project_id: created.id, kind: 'asset.import', payload: {upload_id: upload.upload_id, preprocess:true}}, created.current_revision, true);
      await refresh();
    } catch (e) {setError((e as Error).message);} finally {setBusy(false);}
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
      else await call('jobs.submit', {project_id:project.id, kind:'project.preprocess', payload:{}}, project.current_revision, true);
      await refresh();
    } catch(e) {setError((e as Error).message);}
  }

  return <main className={workspace ? 'workspace-page' : 'library-page'}>
    <header><a className="brand" href="/">m<span>·</span>l <b>Music Learning</b></a>
      <span className="connection">{connected ? '● 本机已连接' : '○ 正在连接'}</span></header>
    {!workspace && <section className="intro"><div><p className="eyebrow">YOUR PERSONAL MUSIC STUDIO</p><h1>听见音乐。<br/><span>看见它的可能。</span></h1>
      <p>从一首喜欢的作品开始，探索和声、节奏与配器。<br/>让每一次理解，成为下一次创造的起点。</p><span className="hero-caption">ANALYZE <i/> ARRANGE <i/> CREATE</span></div><div className="hero-art" aria-hidden="true"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><div className="hero-wave">{Array.from({length:55},(_,i)=><i key={i} style={{height:12+Math.abs(Math.sin(i*.77)*Math.cos(i*.19))*130+'px'}}/>)}</div><span>THE SHAPE OF SOUND</span></div></section>}
    {error && <div role="alert">{error}<button onClick={() => setError('')} aria-label="关闭提示">×</button></div>}
    {workspace && project && revision ? <Workbench key={project.id} title={project.title} revision={revision} publication={publication} jobs={jobs} onCommitted={refresh}
      onBack={() => {setWorkspace(false); history.replaceState(null, '', '/');}}/> : <div className="layout"><aside>
      <label className={'import ' + (busy ? 'disabled' : '')}><strong>{busy ? '正在导入…' : '＋ 选择音乐'}</strong>
        <span>FLAC / WAV / MP3 / OGG 等</span><input aria-label="选择音乐" type="file" accept="audio/*,.flac,.wav,.mp3,.ogg,.m4a"
          disabled={busy} onChange={e => {const file = e.target.files?.[0]; if (file) void importFile(file); e.target.value = '';}}/></label>
      <div className="list-title">工程 <span>{projects.length}</span></div>
      <nav aria-label="工程列表">{projects.map(p => <button key={p.id} aria-pressed={selected === p.id} onClick={() => {
        selectProject(p.id);
      }}><strong>{p.title}</strong><span>{projectStatuses[p.status]} · {short(p.id)}</span></button>)}</nav>
      {!projects.length && <p className="muted empty-list">还没有工程。选一首完整音乐，保存在本机。</p>}
    </aside><section className="detail" aria-label="工程详情">
      {!project ? <div className="welcome"><span>♫</span><h2>为音乐留下一个工程</h2><p>导入完整原曲，或从左侧继续已有研究。</p></div> : <>
        <div className="project-heading"><div><p className="eyebrow">研究工程</p><h2>{project.title}</h2></div>
          <span className="badge">{projectStatuses[project.status]}</span></div>
        <div className="facts"><div><span>版本</span><b>{versionCount}</b></div><div><span>当前版本</span><code>{short(project.current_revision)}</code></div>
          <div><span>原曲长度</span><b>{revision?.snapshot.duration_samples ? (revision.snapshot.duration_samples / (revision.snapshot.sample_rate ?? 44100)).toFixed(1) + ' 秒' : '—'}</b></div></div>
        {revision?.snapshot.source_asset_id && <section className="original"><h3>原曲</h3>
          <audio controls preload="metadata" src={'/v1/assets/' + revision.snapshot.source_asset_id} aria-label="原曲试听"/>
          <p className="muted">{publication ? '整曲材料已齐备，识别结果与音色替代仍需核对。' : '原曲已保留，全部分析与重建通过检查后开放工作台。'}</p>
          {(publication || (revision.snapshot.tracks?.length ?? 0)>0) && <button className="primary-button" onClick={() => {setWorkspace(true); history.replaceState(null,'',`/?project=${project.id}&view=workbench`);}}>打开音乐工作台 <span>↗</span></button>}
          {!publication && !jobs.some(j => j.kind === 'project.preprocess' && ['queued','running','cancel_requested'].includes(j.status)) &&
            <button className="primary-button" onClick={() => void startPreprocessing()}>开始整曲分析</button>}
        </section>}
        <section className="tasks"><h3>任务记录 <span>{jobs.length}</span></h3>
          {!jobs.length && <p className="muted">暂无任务。</p>}
          {jobs.map(job => <div className="task" key={job.id} data-job-id={job.id} data-status={job.status}>
            <div><strong>{kinds[job.kind]}</strong><p>{short(job.id)} · 第 {job.attempt} 次执行</p>
              {job.error && <p className="failure">{String(job.error.code)} · {String(job.error.message)}</p>}</div>
            <span className={'status ' + job.status}>{statuses[job.status]}</span>
            {['queued', 'running'].includes(job.status) && <button className="cancel" onClick={() => void cancel(job)}>取消</button>}
            {['failed','cancelled'].includes(job.status) && !['application.backup','application.restore'].includes(job.kind) && <button className="cancel" onClick={() => void startPreprocessing(job)}>重试</button>}
            {(stages[job.id]?.length ?? 0) > 0 && <ol className="stage-list">{stages[job.id].map(stage => <li key={stage.stage} data-stage={stage.stage} className={stage.status}>
              <span>{stageNames[stage.stage]}</span><span>{stage.cache_hit ? '已复用校验产物' : stage.status === 'pending' ? '等待' : statuses[stage.status] ?? stage.status}
                {typeof stage.progress?.completed === 'number' && typeof stage.progress?.total === 'number' ? ` · ${stage.progress.completed}/${stage.progress.total} 块` : ''}</span>
            </li>)}</ol>}
          </div>)}
        </section>
        <footer>工程保存在本机 · 每个版本独立保留 · {new Date(project.created_at * 1000).toLocaleDateString('zh-CN')}</footer>
      </>}
    </section></div>}
  </main>;
}

createRoot(document.getElementById('root')!).render(<App/>);
