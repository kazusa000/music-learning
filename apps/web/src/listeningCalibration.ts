export type ListeningCalibration={mix_gain_db:number;solo_gain_db:Record<string,number>;target_lufs:number};
export function listeningCalibration(value:unknown):ListeningCalibration|null{
  const v=value as Partial<ListeningCalibration>|null;
  return v&&Number.isFinite(v.mix_gain_db)&&Number.isFinite(v.target_lufs)&&v.solo_gain_db? v as ListeningCalibration:null;
}
export function peakLimiter(context:BaseAudioContext){
  const node=context.createDynamicsCompressor();node.threshold.value=-1;node.knee.value=0;
  node.ratio.value=20;node.attack.value=.001;node.release.value=.06;return node;
}
