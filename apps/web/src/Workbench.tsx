import {useEffect, useRef, useState} from 'react';
import type {PreprocessingResult, Revision} from '../../../packages/contracts/types';
import './workbench.css';

const time = (seconds: number) => `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;

export function Workbench({title, revision, publication, onBack}: {
  title: string; revision: Revision; publication: PreprocessingResult; onBack: () => void;
}) {
  const snapshot = revision.snapshot;
  const rate = snapshot.sample_rate ?? 44100;
  const duration = (snapshot.duration_samples ?? 0) / rate;
  const tracks = snapshot.tracks ?? [];
  const [selected, setSelected] = useState<string>('all');
  const [mode, setMode] = useState<string>('original');
  const [position, setPosition] = useState(0);
  const [waveform, setWaveform] = useState<Array<Array<[number, number]>>>([]);
  const canvas = useRef<HTMLCanvasElement>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const [width, setWidth] = useState(900);
  const pendingPosition = useRef(0);
  const notes = (snapshot.notes ?? []).filter(n => selected === 'all' || n.track_id === selected);
  const source = mode === 'original' ? snapshot.source_asset_id : mode === 'mix' ? publication.mix_asset_id : publication.track_renders[mode];

  useEffect(() => {
    let active = true;
    const id = publication.waveforms['waveforms/source.json'];
    if (id) void fetch('/v1/assets/' + id).then(r => r.json()).then(data => {
      if (active) setWaveform(data.levels.find((l: {buckets: unknown[]}) => l.buckets.length < 1600)?.buckets ?? data.levels.at(-1).buckets);
    }).catch(() => {if (active) setWaveform([]);});
    return () => {active = false;};
  }, [publication]);

  useEffect(() => {
    if (!canvas.current) return;
    const observer = new ResizeObserver(entries => setWidth(entries[0].contentRect.width));
    observer.observe(canvas.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const node = canvas.current;
    if (!node || !duration) return;
    const dpr = devicePixelRatio;
    node.width = Math.round(width * dpr); node.height = 330 * dpr;
    const ctx = node.getContext('2d')!;
    ctx.scale(dpr, dpr);
    ctx.fillStyle = '#14191b'; ctx.fillRect(0, 0, width, 330);
    const left = 45, available = width - left - 10;
    const pitches = notes.map(n => n.pitch);
    const low = Math.max(0, (pitches.length ? Math.min(...pitches) : 48) - 2);
    const high = Math.min(127, (pitches.length ? Math.max(...pitches) : 84) + 2);
    const row = 218 / Math.max(1, high - low + 1);
    ctx.font = '10px Segoe UI';
    for (let pitch = low; pitch <= high; pitch++) {
      const y = 103 + (high - pitch) * row;
      ctx.fillStyle = [1,3,6,8,10].includes(pitch % 12) ? '#1b2225' : '#20292b';
      ctx.fillRect(left, y, available, Math.max(1, row - .4));
      if (pitch % 12 === 0) {ctx.fillStyle = '#929f99'; ctx.fillText(`C${pitch / 12 - 1}`, 7, y + row);}
    }
    for (let seconds = 0; seconds <= duration; seconds += duration > 240 ? 30 : 15) {
      const x = left + seconds / duration * available;
      ctx.strokeStyle = '#36403e';ctx.beginPath();ctx.moveTo(x, 18);ctx.lineTo(x, 328);ctx.stroke();
      ctx.fillStyle = '#9faaa5';ctx.fillText(time(seconds), x + 3, 12);
    }
    waveform.forEach((channels, i) => {
      const x = left + i / waveform.length * available;
      const [min, max] = channels[0];
      ctx.strokeStyle = '#829f93';ctx.beginPath();ctx.moveTo(x, 57 + min * 29);ctx.lineTo(x, 57 + max * 29);ctx.stroke();
    });
    const colors = ['#d7ad7d','#86b4a1','#b39bd1','#d68e7c','#8eaad0','#b8be7b'];
    for (const note of notes) {
      const x = left + note.start_sample / rate / duration * available;
      const w = (note.end_sample - note.start_sample) / rate / duration * available;
      ctx.fillStyle = colors[Math.max(0, tracks.findIndex(t => t.id === note.track_id)) % colors.length];
      ctx.fillRect(x, 103 + (high - note.pitch) * row, Math.max(1.2, w), Math.max(1, row - .5));
    }
    const x = left + Math.min(duration, position) / duration * available;
    ctx.strokeStyle = '#f2e7d9';ctx.beginPath();ctx.moveTo(x, 20);ctx.lineTo(x, 328);ctx.stroke();
  }, [width, notes, tracks, position, duration, rate, waveform]);

  function changeMode(value: string) {
    pendingPosition.current = audio.current?.currentTime ?? position;
    setMode(value);
  }

  function seek(seconds: number) {
    if (audio.current) audio.current.currentTime = Math.min(seconds, audio.current.duration || duration);
    setPosition(seconds);
  }

  return <section className="workbench" aria-label="音乐工作台">
    <div className="workbench-heading"><div><button className="text-button" onClick={onBack}>← 返回音乐库</button>
      <h2>{title}</h2><p className="muted">版本 {revision.id.slice(0, 8)} · {tracks.length} 个 MIDI 声部 · {(snapshot.notes ?? []).length.toLocaleString()} 个音符</p></div>
      <span className="badge">整曲材料已齐备</span></div>
    <p className="candidate-notice">识别结果待核对。重建使用替代音色，人声旋律由音源重放。</p>
    <div className="workbench-grid"><aside className="voice-list"><h3>MIDI 声部</h3>
      <button aria-pressed={selected === 'all'} onClick={() => setSelected('all')}>全部声部</button>
      {tracks.map((track, i) => <button key={track.id} aria-pressed={selected === track.id} onClick={() => setSelected(track.id)}>
        <strong>{i + 1}. {track.role === 'drums' ? '鼓' : track.instrument_candidates?.[0] ?? track.name}</strong>
        <span>{(snapshot.notes ?? []).filter(n => n.track_id === track.id).length} 个音符 · {(snapshot.plugins ?? []).filter(p => p.track_id === track.id).length} 个音源</span>
      </button>)}
      <h3>音频分轨</h3><p className="muted">与 MIDI 声部分开保存</p>
      {(snapshot.stems ?? []).map(stem => <a key={stem.id} href={'/v1/assets/' + stem.asset_id} download>{stem.name} ↗</a>)}
    </aside><div className="music-content">
      <div className="audition"><label>试听 <select aria-label="试听方式" value={mode} onChange={e => changeMode(e.target.value)}>
        <option value="original">原曲</option><option value="mix">整曲重建</option>
        {tracks.map((track, i) => <option key={track.id} value={track.id}>重建声部 {i + 1} · {track.instrument_candidates?.[0]}</option>)}
      </select></label><span>{time(position)} / {time(duration)}</span></div>
      <audio ref={audio} controls preload="metadata" aria-label="工作台试听" src={'/v1/assets/' + source}
        onTimeUpdate={e => setPosition(e.currentTarget.currentTime)} onLoadedMetadata={e => {
          if (pendingPosition.current) e.currentTarget.currentTime = Math.min(pendingPosition.current, e.currentTarget.duration);
          pendingPosition.current = 0;
        }}/>
      <div className="sections" aria-label="段落候选">{(snapshot.annotations ?? []).filter(a => a.kind === 'section').map(section =>
        <button key={section.id} onClick={() => seek(section.start_sample / rate)}>{time(section.start_sample / rate)} {section.text}</button>)}</div>
      <h3>波形与钢琴卷帘 <span>整曲 · 只读预览</span></h3>
      <canvas ref={canvas} className="piano-roll" role="img" aria-label="整曲波形与钢琴卷帘" onClick={event => {
        const bounds = event.currentTarget.getBoundingClientRect();
        seek(Math.max(0, Math.min(1, (event.clientX - bounds.left - 45) / (bounds.width - 55))) * duration);
      }}/>
      <h3>当前和弦候选</h3><div className="chord-readback">{(snapshot.annotations ?? []).filter(a => a.kind === 'chord' &&
        a.start_sample / rate <= position && a.end_sample / rate > position).map(a => <strong key={a.id}>{a.text}</strong>)}</div>
      <details className="analysis-issues"><summary>来源与待核对问题 · {publication.issues.length}</summary>
        <ul>{publication.issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul></details>
    </div></div>
  </section>;
}
