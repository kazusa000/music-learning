import type {AuditionState} from '../../../packages/contracts/types';

export function sourceSample(sample:number,duration:number){return Math.min(duration,Math.max(0,Math.round(Number.isFinite(sample)?sample:0)));}
// Playback buffers may include a release tail. Persistent transport positions
// and loop selections use the arrangement's source clock, never buffer length.
export function boundAudition(state:AuditionState,duration:number):AuditionState&{position_sample:number;loop_start_sample:number;loop_end_sample:number;loop:boolean}{
  if(duration<=0)throw new Error('工程还没有可播放的内容。');
  const position_sample=sourceSample(state.position_sample??0,duration);
  const end=sourceSample(state.loop_end_sample??duration,duration),start=sourceSample(state.loop_start_sample??0,duration);
  const valid=start<end;
  return {...state,position_sample,loop_start_sample:valid?start:0,loop_end_sample:valid?end:duration,loop:valid?(state.loop??false):false};
}
