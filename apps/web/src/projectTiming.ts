import type {Snapshot} from '../../../packages/contracts/types';
const value=(beat:{n:number;d?:number})=>beat.n/(beat.d??1);
export const commonMeters=['2/4','3/4','4/4','5/4','6/4','7/4','3/8','6/8','7/8','9/8','12/8'];
export function projectTiming(snapshot:Snapshot,sample=0){
  const map=snapshot.timing_map;
  if(!map)return null;
  const anchors=map.anchors;
  const index=anchors.findIndex((a,i)=>i>0&&a.sample>sample);
  const i=index<0?anchors.length-2:Math.max(0,index-1),a=anchors[i],b=anchors[i+1];
  const beat=value(a.beat)+(sample-a.sample)*(value(b.beat)-value(a.beat))/(b.sample-a.sample);
  const meter=map.meters.filter(m=>value(m.beat)<=beat).at(-1)??map.meters[0];
  const bpm=(value(b.beat)-value(a.beat))*(snapshot.sample_rate??44100)*60/(b.sample-a.sample);
  return {bpm,numerator:meter.numerator,denominator:meter.denominator,variable:anchors.length>2};
}
