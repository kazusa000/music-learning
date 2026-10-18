import {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import type {Project, Job, Revision} from '../../../packages/contracts/types';
import {call, connect, result} from './api';
import './style.css';

const connection = connect();
const statuses: Record<string, string> = {queued: '排队中', running: '正在处理', cancel_requested: '正在取消',
  cancelled: '已取消', succeeded: '已完成', failed: '处理失败'};
const kinds: Record<string, string> = {'asset.import': '原曲导入与检查', 'plugin.inspect': '读取插件状态', 'plugin.scan': '扫描插件'};
const short = (value: string) => value.slice(0, 8);

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<string | null>(localStorage.getItem('selected-project'));
  const activeSelection = useRef(selected);
  const [revision, setRevision] = useState<Revision | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [versionCount, setVersionCount] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const project = projects.find(p => p.id === selected);

  function selectProject(id: string) {
    activeSelection.current = id;
    setSelected(id); setRevision(null); setJobs([]); setVersionCount(0);
    localStorage.setItem('selected-project', id);
  }

  const refresh = useCallback(async () => {
    await connection;
    const list = await call<Project[]>('projects.list', {limit: 1000});
    if (activeSelection.current !== selected) return;
    setProjects(list);
    if (selected && list.some(p => p.id === selected)) {
      const [head, tasks, versions] = await Promise.all([
        call<Revision>('revisions.get', {project_id: selected}),
        call<Job[]>('jobs.list', {project_id: selected, limit: 1000}),
        call<unknown[]>('revisions.list', {project_id: selected, limit: 1000}),
      ]);
      if (activeSelection.current !== selected) return;
      setRevision(head); setJobs(tasks); setVersionCount(versions.length);
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
      stream = new EventSource('/v1/events');
      stream.onopen = () => {setConnected(true); update();};
      stream.onerror = () => setConnected(false);
      stream.addEventListener('domain', () => {clearTimeout(debounce); debounce = setTimeout(update, 120);});
    }).catch(e => setError(e.message));
    const timer = setInterval(update, 3000);
    return () => {active = false; clearInterval(timer); clearTimeout(debounce); stream?.close();};
  }, [refresh]);

  async function importFile(file: File) {
    setBusy(true); setError('');
    try {
      const created = await call<Project>('projects.create', {title: file.name.replace(/\.[^.]+$/, ''), kind: 'research'}, undefined, true);
      selectProject(created.id);
      const upload = await result<{upload_id: string}>(await fetch('/v1/uploads', {method: 'PUT', body: file,
        headers: {'Content-Type': 'application/octet-stream', 'Idempotency-Key': crypto.randomUUID()}}));
      await call('jobs.submit', {project_id: created.id, kind: 'asset.import', payload: {upload_id: upload.upload_id}}, created.current_revision, true);
      await refresh();
    } catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }

  async function cancel(job: Job) {
    if (!project) return;
    try {await call('jobs.cancel', {job_id: job.id}, project.current_revision, true); await refresh();}
    catch (e) {setError((e as Error).message);}
  }

  return <main>
    <header><a className="brand" href="/">m<span>·</span>l <b>Music Learning</b></a>
      <span className="connection">{connected ? '● 本机已连接' : '○ 正在连接'}</span></header>
    <section className="intro"><p className="eyebrow">你的音乐研究库</p><h1>从一首音乐开始。</h1>
      <p>保存原曲与工程，让每一次分析、讨论和改编都有迹可循。</p></section>
    {error && <div role="alert">{error}<button onClick={() => setError('')} aria-label="关闭提示">×</button></div>}
    <div className="layout"><aside>
      <label className={'import ' + (busy ? 'disabled' : '')}><strong>{busy ? '正在导入…' : '＋ 选择音乐'}</strong>
        <span>FLAC / WAV / MP3 / OGG 等</span><input aria-label="选择音乐" type="file" accept="audio/*,.flac,.wav,.mp3,.ogg,.m4a"
          disabled={busy} onChange={e => {const file = e.target.files?.[0]; if (file) void importFile(file); e.target.value = '';}}/></label>
      <div className="list-title">工程 <span>{projects.length}</span></div>
      <nav aria-label="工程列表">{projects.map(p => <button key={p.id} aria-pressed={selected === p.id} onClick={() => {
        selectProject(p.id);
      }}><strong>{p.title}</strong><span>{p.status === 'imported' ? '原曲已保存' : '等待导入'} · {short(p.id)}</span></button>)}</nav>
      {!projects.length && <p className="muted empty-list">还没有工程。选一首完整音乐，保存在本机。</p>}
    </aside><section className="detail" aria-label="工程详情">
      {!project ? <div className="welcome"><span>♫</span><h2>为音乐留下一个工程</h2><p>导入完整原曲，或从左侧继续已有研究。</p></div> : <>
        <div className="project-heading"><div><p className="eyebrow">研究工程</p><h2>{project.title}</h2></div>
          <span className="badge">{project.status === 'imported' ? '原曲已保存' : '等待导入'}</span></div>
        <div className="facts"><div><span>版本</span><b>{versionCount}</b></div><div><span>当前版本</span><code>{short(project.current_revision)}</code></div>
          <div><span>原曲长度</span><b>{revision?.snapshot.duration_samples ? (revision.snapshot.duration_samples / (revision.snapshot.sample_rate ?? 44100)).toFixed(1) + ' 秒' : '—'}</b></div></div>
        {revision?.snapshot.source_asset_id && <section className="original"><h3>原曲</h3>
          <audio controls preload="metadata" src={'/v1/assets/' + revision.snapshot.source_asset_id} aria-label="原曲试听"/>
          <p className="muted">原曲已保留。整曲分析尚未开始，分析完成后将开放音乐工作台。</p></section>}
        <section className="tasks"><h3>任务记录 <span>{jobs.length}</span></h3>
          {!jobs.length && <p className="muted">暂无任务。</p>}
          {jobs.map(job => <div className="task" key={job.id} data-job-id={job.id} data-status={job.status}>
            <div><strong>{kinds[job.kind]}</strong><p>{short(job.id)} · 第 {job.attempt} 次执行</p>
              {job.error && <p className="failure">{String(job.error.code)} · {String(job.error.message)}</p>}</div>
            <span className={'status ' + job.status}>{statuses[job.status]}</span>
            {['queued', 'running'].includes(job.status) && <button className="cancel" onClick={() => void cancel(job)}>取消</button>}
          </div>)}
        </section>
        <footer>工程保存在本机 · 每个版本独立保留 · {new Date(project.created_at * 1000).toLocaleDateString('zh-CN')}</footer>
      </>}
    </section></div>
  </main>;
}

createRoot(document.getElementById('root')!).render(<App/>);
