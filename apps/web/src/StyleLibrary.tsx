import {useEffect,useMemo,useRef,useState} from 'react';
import type {CSSProperties} from 'react';
import {call} from './api';
import {Icon} from './Icon';
import {WindowControls} from './WindowChrome';
import './style-library.css';

type Category={id:string;name:string};
type Style={id:string;name:string;english:string;kind:string;year:number|null;family:string;aliases:string[];relatedFamilies:string[];yearDisplay:string|null;tags:string[]};
type Edge={source:string;target:string;kind:string};
type Detail=Style&{intro?:string;tempo:string|null;sections:Record<string,Array<{label:string;text:string}>>;tracks:Array<{title:string;artist?:string;year?:number}>;source_url:string;sources:Array<{label:string;url:string}>;relations:Edge[];related_styles:Style[]};
type Index={categories:Category[];items:Style[];total:number;edge_count:number};
type Graph={nodes:Style[];edges:Edge[]};
const colors=['#c9ad81','#b5b794','#dcb580','#82a7cb','#d8ae7e','#baad8c','#a2b4a0','#d18e7b','#cda16e','#bd8592','#c48ca9','#aa91ba','#93a3bb','#8fafaa','#d6a58e','#b8ac77','#9eb790','#b493c2','#a493b7','#839eae','#b4a0c1','#bcab83','#83aeb2','#a6acc8','#be9a90','#98a9c9','#8faab9','#b8af80','#d4a78e'];
const relationNames:Record<string,string>={evolution:'发展',influence:'影响',backlash:'回应',revival:'复兴'};
const sectionNames:Record<string,string>={rhythm:'节奏与律动',harmony:'和声与织体',palette:'配器思路',production:'制作与空间'};
const favoriteKey='studio.style-favorites.v1';
const selectedKey='studio.style-selected.v1';
const featured=['CITY POP','JAZZ FUSION / JAZZ ROCK','BOSSA NOVA','DEEP HOUSE','NEO SOUL','TRAP','SYNTHWAVE','DREAM POP','AMBIENT','FUNK','DISCO','BOOM BAP'];
function readSaved(){try{const saved=JSON.parse(localStorage.getItem(favoriteKey)??'[]');return Array.isArray(saved)?saved.filter((id:unknown)=>typeof id==='string') as string[]:[];}catch{return [];}}
function Art({color,seed=0}:{color:string;seed?:number}){
  return <svg className="style-art" viewBox="0 0 320 80" aria-hidden="true"><defs><linearGradient id={'style-glow-'+seed}><stop stopColor={color} stopOpacity=".32"/><stop offset="1" stopColor={color} stopOpacity=".02"/></linearGradient></defs><rect width="320" height="80" fill={'url(#style-glow-'+seed+')'}/>{Array.from({length:22},(_,i)=><path key={i} d={`M ${i*16-20} 90 Q ${130+(seed%5)*12} ${-70+i*7} ${340-i*3} -15`} fill="none" stroke={color} strokeWidth=".8" opacity={.08+(i%5)*.025}/>)}<circle cx={220+seed%38} cy="40" r="24" stroke={color} strokeWidth=".5" fill="none" opacity=".23"/></svg>;
}

function MapView({items,categories,graph,selected,onSelect}:{items:Style[];categories:Category[];graph:Graph|null;selected:string;onSelect:(id:string)=>void}){
  const [zoom,setZoom]=useState(1),[offset,setOffset]=useState({x:0,y:0}),[depth,setDepth]=useState(false);
  const drag=useRef<{x:number;y:number;ox:number;oy:number;moved:boolean}|null>(null),svg=useRef<SVGSVGElement>(null),suppressClick=useRef(false);
  const points=useMemo(()=>{
    const sorted=[...items].sort((a,b)=>(a.year??9999)-(b.year??9999)||a.id.localeCompare(b.id)),rowCounts=new Map<string,number>();
    return new Map(sorted.map(item=>{
      const row=Math.max(0,categories.findIndex(c=>c.id===item.family)),n=rowCounts.get(item.family)??0;rowCounts.set(item.family,n+1);
      const year=item.year===null?2036:item.year<1900?1895:Math.min(2026,item.year);
      return [item.id,{x:120+(year-1890)/150*1010+(depth?(n%7)*4:0),y:34+row*24+(n%5-2)*3-(depth?(n%7)*2:0),item}];
    }));
  },[items,categories,depth]);
  const edges=(graph?.edges??[]).filter(e=>points.has(e.source)&&points.has(e.target)&&(e.source===selected||e.target===selected));
  const nearby=new Set(edges.flatMap(e=>[e.source,e.target]));
  const viewW=1200/zoom,viewH=760/zoom;
  return <div className="style-map"><div className="style-map-tools"><span>年代与关联</span><div><button aria-pressed={!depth} onClick={()=>setDepth(false)}>平面</button><button aria-pressed={depth} onClick={()=>setDepth(true)}>层次</button><i/><button aria-label="缩小风格地图" onClick={()=>setZoom(z=>Math.max(.7,z/1.3))}>−</button><button aria-label="放大风格地图" onClick={()=>setZoom(z=>Math.min(8,z*1.3))}>＋</button><button onClick={()=>{setZoom(1);setOffset({x:0,y:0});}}>全图</button></div></div>
    <svg ref={svg} viewBox={`${offset.x} ${offset.y} ${viewW} ${viewH}`} role="group" aria-label="风格关系地图" onContextMenu={e=>e.preventDefault()} onWheel={e=>{e.stopPropagation();setZoom(z=>Math.max(.7,Math.min(8,z*(e.deltaY<0?1.12:1/1.12))));}} onPointerDown={e=>{if(e.button!==0&&e.button!==1&&e.button!==2)return;suppressClick.current=false;drag.current={x:e.clientX,y:e.clientY,ox:offset.x,oy:offset.y,moved:false};if(e.target===e.currentTarget)e.currentTarget.setPointerCapture(e.pointerId);}} onPointerMove={e=>{const d=drag.current;if(!d)return;const rect=e.currentTarget.getBoundingClientRect();const dx=e.clientX-d.x,dy=e.clientY-d.y;if(Math.abs(dx)+Math.abs(dy)>3)d.moved=true;if(d.moved)setOffset({x:d.ox-dx/rect.width*viewW,y:d.oy-dy/rect.height*viewH});}} onPointerUp={()=>{suppressClick.current=!!drag.current?.moved;drag.current=null;}} onPointerCancel={()=>{drag.current=null;}}>
      {[1900,1920,1940,1960,1980,2000,2020].map(y=><g key={y}><line x1={120+(y-1890)/150*1010} x2={120+(y-1890)/150*1010} y1="15" y2="750" stroke="#ffffff09"/><text x={120+(y-1890)/150*1010} y="16" fill="#767077" fontSize="10">{y}</text></g>)}
      {categories.map((c,i)=><g key={c.id}><line x1="110" x2="1165" y1={34+i*24} y2={34+i*24} stroke="#ffffff04"/><text x="5" y={38+i*24} fill={colors[i%colors.length]} fontSize="9">{c.name.length>9?c.name.slice(0,8)+'…':c.name}</text></g>)}
      {edges.map((e,i)=>{const a=points.get(e.source)!,b=points.get(e.target)!;return <path key={i} d={`M ${a.x} ${a.y} Q ${(a.x+b.x)/2} ${Math.min(a.y,b.y)-30} ${b.x} ${b.y}`} stroke="#d9b597" strokeWidth="1" strokeOpacity=".6" strokeDasharray={e.kind==='evolution'?undefined:'3 4'} fill="none"/>;})}
      {[...points.values()].map(({x,y,item})=><g key={item.id} role="button" aria-label={'查看风格 '+item.name} tabIndex={item.id===selected?0:-1} onKeyDown={e=>{if(e.key==='Enter')onSelect(item.id);}} onClick={()=>{if(!suppressClick.current)onSelect(item.id);}}><title>{item.name} · {item.english}</title><circle cx={x} cy={y} r={item.id===selected?5.5:nearby.has(item.id)?3.4:2.4} fill={item.id===selected?'#ffe0b1':colors[Math.max(0,categories.findIndex(c=>c.id===item.family))%colors.length]} opacity={item.id===selected||nearby.has(item.id)?1:.5}/>{(item.id===selected||nearby.has(item.id))&&<text x={x+8} y={y+3} fill="#e4d5c5" fontSize="10" pointerEvents="none">{item.name}</text>}</g>)}
    </svg><div className="style-map-hint">拖动平移 · 滚轮缩放 · 1900 年前压缩显示 <span>{items.length} 个节点</span></div>
  </div>;
}

export default function StyleLibrary({onClose,overlay=false}:{onClose:()=>void;overlay?:boolean}){
  const [index,setIndex]=useState<Index|null>(null),[graph,setGraph]=useState<Graph|null>(null),[error,setError]=useState(''),[retry,setRetry]=useState(0);
  const [query,setQuery]=useState(''),[category,setCategory]=useState('all'),[era,setEra]=useState('all'),[favorites,setFavorites]=useState<string[]>(readSaved),[onlyFavorites,setOnlyFavorites]=useState(false);
  const [selected,setSelected]=useState(()=>new URLSearchParams(location.search).get('style')??localStorage.getItem(selectedKey)??'asi03'),[detail,setDetail]=useState<Detail|null>(null),[detailError,setDetailError]=useState(''),[mode,setMode]=useState('browse'),[count,setCount]=useState(48),[mobileDetail,setMobileDetail]=useState(false);
  const dialog=useRef<HTMLDialogElement>(null),detailScroll=useRef<HTMLElement>(null),listScroll=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(overlay){dialog.current?.showModal();return()=>dialog.current?.close();}},[overlay]);
  useEffect(()=>{let live=true;setError('');void call<Index>('styles.index').then(x=>{if(live)setIndex(x);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[retry]);
  useEffect(()=>{let live=true;setDetail(null);setDetailError('');void call<Detail>('styles.get',{style_id:selected}).then(x=>{if(live){setDetail(x);detailScroll.current?.scrollTo({top:0});}}).catch(e=>{if(live)setDetailError(e.message);});return()=>{live=false;};},[selected,retry]);
  useEffect(()=>{if(mode!=='map'||graph)return;let live=true;void call<Graph>('styles.graph').then(x=>{if(live)setGraph(x);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[mode,graph,retry]);
  useEffect(()=>{setCount(48);listScroll.current?.scrollTo({top:0});},[query,category,era,onlyFavorites]);
  const all=index?.items??[],genres=all.filter(s=>s.kind==='genre'),categories=index?.categories??[];
  const filtered=useMemo(()=>{
    const terms=query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return genres.filter(s=>(category==='all'||s.family===category||s.relatedFamilies.includes(category))&&(!onlyFavorites||favorites.includes(s.id))&&
      (era==='all'||(era==='early'?s.year!==null&&s.year<1940:era==='unknown'?s.year===null:s.year!==null&&Math.floor(s.year/10)*10===+era))&&
      terms.every(t=>[s.name,s.english,...s.aliases,...s.tags].join(' ').toLocaleLowerCase().includes(t))).sort((a,b)=>{
        return (featured.indexOf(a.english)<0?99:featured.indexOf(a.english))-(featured.indexOf(b.english)<0?99:featured.indexOf(b.english))||a.name.localeCompare(b.name,'zh-CN');});
  },[index,query,category,era,onlyFavorites,favorites]);
  function choose(id:string){setSelected(id);setMobileDetail(true);localStorage.setItem(selectedKey,id);if(!overlay)history.replaceState(null,'','/?view=styles&style='+encodeURIComponent(id));}
  function toggleFavorite(id:string){setFavorites(current=>{const next=current.includes(id)?current.filter(x=>x!==id):[...current,id];localStorage.setItem(favoriteKey,JSON.stringify(next));return next;});}
  const categoryIndex=Math.max(0,categories.findIndex(c=>c.id===(detail?.family??'asi'))),accent=colors[categoryIndex%colors.length];
  const relatedById=new Map(detail?.related_styles.map(s=>[s.id,s])??[]);
  const content=<section className={'style-library'+(mobileDetail?' style-detail-open':'')} aria-label="音乐风格库" style={{'--style-accent':accent} as CSSProperties}>
    <div className="style-library-header"><button className="style-return" aria-label="返回工作室" onClick={onClose}><Icon name="back" size={16}/></button><div><h1>风格库 <span>STYLE ATLAS</span></h1><p>寻找声音的来路，发现下一种写法。</p></div><div className="style-header-meta"><span>{index?.total.toLocaleString()??'…'} 种风格</span><i/><span>{categories.length||'…'} 个分类</span></div>{overlay&&<WindowControls/>}</div>
    <div className="style-library-body"><nav className="style-categories" aria-label="风格分类"><div className="style-nav-heading">浏览音乐</div><button aria-pressed={category==='all'&&!onlyFavorites} onClick={()=>{setCategory('all');setOnlyFavorites(false);}}><Icon name="music" size={15}/>全部风格 <small>{index?.total??'—'}</small></button><button aria-pressed={onlyFavorites} onClick={()=>setOnlyFavorites(!onlyFavorites)}><span className="style-star">☆</span>我的收藏 <small>{favorites.length}</small></button><div className="style-nav-heading style-nav-families">分类</div><div className="style-family-list">{categories.map((c,i)=><button key={c.id} aria-pressed={category===c.id&&!onlyFavorites} onClick={()=>{setCategory(c.id);setOnlyFavorites(false);}}><i style={{background:colors[i%colors.length]}}/>{c.name}<small>{genres.filter(x=>x.family===c.id||x.relatedFamilies.includes(c.id)).length}</small></button>)}</div><a className="style-origin" href="https://wangshubianqu.com/music-map/" target="_blank" rel="noreferrer">参考：汪叔音乐风格地图 ↗</a></nav>
      <div className="style-browser"><div className="style-browser-toolbar"><label className="style-search"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/></svg><input aria-label="搜索音乐风格" placeholder="搜索风格、英文名或声音特点…" value={query} onChange={e=>setQuery(e.target.value)}/>{query&&<button aria-label="清空风格搜索" onClick={()=>setQuery('')}>×</button>}</label><select aria-label="风格年代" value={era} onChange={e=>setEra(e.target.value)}><option value="all">全部年代</option><option value="early">1940 年以前</option>{[1940,1950,1960,1970,1980,1990,2000,2010,2020].map(y=><option key={y} value={y}>{y}s</option>)}<option value="unknown">年代未定</option></select></div>
        <div className="style-results-heading"><div><h2>{onlyFavorites?'我的收藏':category==='all'?'探索音乐风格':categories.find(c=>c.id===category)?.name}</h2><span>{filtered.length} 个风格</span></div><div className="style-view-switch" aria-label="风格显示方式"><button aria-pressed={mode==='browse'} onClick={()=>setMode('browse')}>浏览</button><button aria-pressed={mode==='map'} onClick={()=>setMode('map')}>关系地图</button></div></div>
        {error?<div className="style-empty" role="alert"><h3>风格库暂时未打开</h3><p>{error}</p><button onClick={()=>setRetry(x=>x+1)}>重新打开</button></div>:!index?<div className="style-skeleton" aria-label="正在打开风格库">{[1,2,3,4,5,6].map(i=><div key={i}/>)}</div>:mode==='map'?<MapView items={filtered} categories={categories} graph={graph} selected={selected} onSelect={choose}/>:<div className="style-results-scroll" ref={listScroll}>
          {!!filtered.length&&<div className="style-card-grid">{filtered.slice(0,count).map(s=>{const ci=Math.max(0,categories.findIndex(c=>c.id===s.family)),color=colors[ci%colors.length];return <button className="style-card" key={s.id} data-style={s.id} aria-pressed={selected===s.id} style={{'--card-accent':color} as CSSProperties} onClick={()=>choose(s.id)}><Art color={color} seed={ci}/><div className="style-card-top"><span>{categories[ci]?.name}</span><span>{favorites.includes(s.id)?'★':s.yearDisplay??'年代未定'}</span></div><h3>{s.name}</h3><p className="style-card-english">{s.english}</p><div className="style-card-tags">{s.tags.slice(0,3).map(t=><span key={t}>{t}</span>)}</div><span className="style-card-open">查看风格 <Icon name="arrow" size={12}/></span></button>;})}</div>}
          {!filtered.length&&<div className="style-empty"><Icon name="music" size={25}/><h3>{onlyFavorites?'还没有符合条件的收藏':'没有找到匹配的风格'}</h3><p>{onlyFavorites?'打开喜欢的风格，点星标即可收藏。':'试试英文名、别名，或清除筛选条件。'}</p><button onClick={()=>{setQuery('');setCategory('all');setEra('all');setOnlyFavorites(false);}}>查看全部风格</button></div>}
          {filtered.length>count&&<button className="style-load-more" onClick={()=>setCount(x=>x+48)}>继续浏览 <span>{Math.min(count,filtered.length)} / {filtered.length}</span></button>}
        </div>}
      </div>
      <aside className="style-dossier" ref={detailScroll} aria-label="风格档案"><button className="style-mobile-back" onClick={()=>setMobileDetail(false)}><Icon name="back" size={14}/>返回风格列表</button>{detailError?<div className="style-empty"><p>{detailError}</p><button onClick={()=>choose('asi03')}>打开城市流行</button></div>:!detail?<div className="style-detail-loading">正在打开风格档案…</div>:<>
        <div className="style-dossier-cover"><Art color={accent} seed={categoryIndex+50}/><span>{categories.find(c=>c.id===detail.family)?.name}</span><button className="style-favorite" aria-label={favorites.includes(detail.id)?'取消收藏风格':'收藏风格'} aria-pressed={favorites.includes(detail.id)} onClick={()=>toggleFavorite(detail.id)}>{favorites.includes(detail.id)?'★':'☆'}</button><h2>{detail.name}</h2><p>{detail.english}</p></div><div className="style-dossier-content"><div className="style-facts"><div><small>形成时期</small><span>{detail.yearDisplay??'年代未定'}</span></div><div><small>速度倾向</small><span>{detail.tempo||'随作品变化'}</span></div></div>
        {!!detail.aliases?.length&&<div className="style-aliases">{detail.aliases.slice(0,5).map(a=><span key={a}>{a}</span>)}</div>}
        {detail.intro&&<p className="style-intro">{detail.intro}</p>}
        {Object.entries(sectionNames).map(([key,title])=>detail.sections[key]?.length>0&&<section className="style-note-section" key={key}><h3><span>{String(Object.keys(sectionNames).indexOf(key)+1).padStart(2,'0')}</span>{title}</h3>{detail.sections[key].map(note=><div className="style-production-note" key={note.label}>{note.label!=='编曲切入'&&<strong>{note.label}</strong>}<p>{note.text}</p></div>)}</section>)}
        {!!detail.relations.length&&<section className="style-note-section"><h3><span>↗</span>来路与延伸</h3><div className="style-relations">{detail.relations.map((e,i)=>{const other=relatedById.get(e.source===detail.id?e.target:e.source);return other&&<button key={i} onClick={()=>choose(other.id)}><span>{e.target===detail.id?'←':'→'} {relationNames[e.kind]??e.kind}</span><strong>{other.name}</strong><small>{other.english}</small></button>;})}</div></section>}
        {!!detail.tracks.length&&<section className="style-note-section"><h3><span>♫</span>代表曲目</h3><div className="style-reference-tracks">{detail.tracks.map((t,i)=><a key={i} target="_blank" rel="noreferrer" href={'https://music.163.com/#/search/m/?s='+encodeURIComponent((t.artist?t.artist+' – ':'')+t.title)}><small>{String(i+1).padStart(2,'0')}</small><span>{t.artist&&t.artist+' – '}{t.title}{t.year&&<small>{t.year}</small>}</span><Icon name="arrow" size={13}/></a>)}</div></section>}
        <details className="style-sources"><summary>资料出处</summary><a href={detail.source_url} target="_blank" rel="noreferrer">汪叔音乐风格地图 ↗</a>{detail.sources.map((s,i)=><a href={s.url} key={i} target="_blank" rel="noreferrer">{s.label} ↗</a>)}<p>分类与关联保留原始出处；编曲提示为本地整理。</p></details></div>
      </>}</aside>
    </div>
  </section>;
  return overlay?<dialog className="style-library-dialog" ref={dialog} onCancel={e=>{e.preventDefault();onClose();}} onKeyDown={e=>e.stopPropagation()}>{content}</dialog>:content;
}
