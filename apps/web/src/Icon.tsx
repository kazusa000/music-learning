import type {CSSProperties} from 'react';

const paths = {
  trash: <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/></>,
  music: <><path d="M9 18V5l11-2v13M9 9l11-2"/><ellipse cx="6" cy="18" rx="3" ry="2"/><ellipse cx="17" cy="16" rx="3" ry="2"/></>,
  upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5"/></>,
  play: <path d="m8 5 11 7-11 7Z"/>,
  pause: <><path d="M8 5v14M16 5v14"/></>,
  back: <path d="m14 5-7 7 7 7"/>,
  start: <><path d="M5 5v14m13-14L8 12l10 7Z"/></>,
  loop: <><path d="m16 2 4 4-4 4M4 11V9a3 3 0 0 1 3-3h13M8 22l-4-4 4-4m12-1v2a3 3 0 0 1-3 3H4"/></>,
  plus: <path d="M12 5v14M5 12h14"/>,
  expand: <><path d="M14 4h6v6m0-6-7 7M10 20H4v-6m0 6 7-7"/></>,
  collapse: <><path d="M20 10h-6V4m0 6 6-6M4 14h6v6m0-6-6 6"/></>,
  more: <><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></>,
  arrow: <path d="M5 12h14m-6-6 6 6-6 6"/>,
  send: <><path d="M12 19V5m-6 6 6-6 6 6"/></>,
  wave: <><path d="M3 10v4m4-7v10m5-14v18m5-14v10m4-7v4"/></>,
  piano: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 4v16m8-16v16M6 4v8h4V4m4 0v8h4V4"/></>,
  mixer: <><path d="M5 3v18m7-18v18m7-18v18M2 8h6m1 8h6m1-10h6"/></>,
  chat: <path d="M20 15a3 3 0 0 1-3 3H9l-5 3V6a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3Z"/>,
  history: <><path d="M3 10a9 9 0 1 1 2 8M3 4v6h6m3-3v5l3 2"/></>,
  analysis: <><path d="M4 19V9m8 10V4m8 15v-7"/></>,
  timeline: <><rect x="3" y="4" width="12" height="5" rx="1"/><rect x="8" y="15" width="13" height="5" rx="1"/><path d="M3 12h18"/></>,
};
export type IconName = keyof typeof paths;
export function Icon({name,size=16,style}:{name:IconName;size?:number;style?:CSSProperties}) {
  return <svg className={'ui-icon icon-'+name} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" style={style}>{paths[name]}</svg>;
}
