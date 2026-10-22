export const TRACK_DRAG_TYPE='application/x-music-learning-track';
export const MIN_TRACK_HEIGHT=108,MAX_TRACK_HEIGHT=240;
export function trackHeight(value:number){return Math.round(Math.max(MIN_TRACK_HEIGHT,Math.min(MAX_TRACK_HEIGHT,value)));}
export function reorderTracks(ids:string[],from:string,to:string,after=false){
  if(from===to||!ids.includes(from)||!ids.includes(to))return ids;
  const next=ids.filter(id=>id!==from),index=next.indexOf(to)+(after?1:0);
  next.splice(index,0,from);return next;
}
export function zoomTrackScroll(scroll:number,anchor:number,oldHeight:number,newHeight:number){return Math.max(0,(scroll+anchor)/oldHeight*newHeight-anchor);}
