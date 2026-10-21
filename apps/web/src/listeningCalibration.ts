export type ListeningCalibration={mix_gain_db:number;solo_gain_db:Record<string,number>;target_lufs:number};
export function listeningCalibration(value:unknown):ListeningCalibration|null{
  const v=value as Partial<ListeningCalibration>|null;
  return v&&Number.isFinite(v.mix_gain_db)&&Number.isFinite(v.target_lufs)&&v.solo_gain_db? v as ListeningCalibration:null;
}
export function peakLimiter(context:BaseAudioContext){
  const node=context.createDynamicsCompressor();node.threshold.value=-1;node.knee.value=0;
  node.ratio.value=20;node.attack.value=.001;node.release.value=.06;
  // The compressor has attack overshoot. A continuous soft ceiling catches
  // those transients while leaving samples below -0.92 dB unchanged.
  const ceiling=context.createWaveShaper(),curve=new Float32Array(8193);
  for(let i=0;i<curve.length;i++){
    const x=2*i/(curve.length-1)-1,a=Math.abs(x);
    curve[i]=Math.sign(x)*(a<=.9?a:.9+.07*(1-Math.exp(-(a-.9)/.07)));
  }
  ceiling.curve=curve;ceiling.oversample='4x';node.connect(ceiling);
  // Reserve 1 dB for reconstructed peaks from the oversampling filter.
  const headroom=context.createGain();headroom.gain.value=10**(-1/20);
  ceiling.connect(headroom);return {input:node,output:headroom};
}
