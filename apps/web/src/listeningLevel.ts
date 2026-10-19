export type Level={rms:number;peak:number};
// Shared family gain preserves the rendered balance between its stems.
// -28 dBFS RMS is deliberately conservative; sparse material remains limited
// by peak headroom rather than being boosted into clipping.
export function listeningGain(level:Level){
  return level.rms>1e-8&&level.peak>0?Math.min(.04/level.rms,.794/level.peak,16):1;
}

export function matchedLevels(jobs:Array<{kind:string;status:string;result?:Record<string,unknown>|null;payload?:Record<string,unknown>}>,ids:string[]){
  const measurements=new Map<string,{lufs:number;tp:number}>();
  for(const job of jobs){const r=job.result;
    if(job.kind==='audio.measure'&&job.status==='succeeded'&&r&&r.start_sample===0&&job.payload?.end_sample==null&&typeof r.asset_id==='string'&&typeof r.integrated_lufs==='number'&&typeof r.true_peak_dbtp==='number')
      measurements.set(r.asset_id,{lufs:r.integrated_lufs,tp:r.true_peak_dbtp});
  }
  if(!ids.length||ids.some(id=>!measurements.has(id)))return {} as Record<string,number>;
  const target=Math.min(-18,...ids.map(id=>{const m=measurements.get(id)!;return m.lufs-m.tp-2;}));
  return Object.fromEntries(ids.map(id=>[id,10**((target-measurements.get(id)!.lufs)/20)]));
}
