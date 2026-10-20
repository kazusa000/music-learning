export type Point={x:number;y:number};
export function crossesRect(a:Point,b:Point,r:{x:number;y:number;width:number;height:number}){
  // Segment clipping also catches notes crossed between sparse pointer events.
  let enter=0,leave=1;const dx=b.x-a.x,dy=b.y-a.y;
  for(const [p,q] of [[-dx,a.x-r.x],[dx,r.x+r.width-a.x],[-dy,a.y-r.y],[dy,r.y+r.height-a.y]]){
    if(p===0){if(q<0)return false;continue;}const t=q/p;
    if(p<0)enter=Math.max(enter,t);else leave=Math.min(leave,t);
    if(enter>leave)return false;
  }
  return true;
}
