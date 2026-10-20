export type MixValues={gain_db?:number;pan?:number;mute?:boolean;solo?:boolean;name?:string};
export type MixChannel={id:string;assetId:string;bakedGain:number;bakedPan:number;values:MixValues};
// The native host uses stereo balance, not equal-power mono panning.
export function channelGains(channel:MixChannel,soloed:boolean):[number,number]{
  const v=channel.values,pan=v.pan??0,base=channel.bakedPan;
  if(v.mute||(soloed&&!v.solo))return [0,0];
  const gain=10**(((v.gain_db??0)-channel.bakedGain)/20);
  return [gain*(pan>0?1-pan:1)/(base>0?1-base:1),gain*(pan<0?1+pan:1)/(base<0?1+base:1)];
}
