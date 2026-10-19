// Full-buffer RMS/peak analysis stays off the UI thread. These are preview
// measurements, not EBU R128 integrated loudness or changes to exported audio.
onmessage=(event:MessageEvent<{id:string;channels:Float32Array[]}>)=>{
  let power=0,peak=0,count=0;
  for(const channel of event.data.channels)for(let i=0;i<channel.length;i++){
    const value=channel[i];power+=value*value;peak=Math.max(peak,Math.abs(value));count++;
  }
  postMessage({id:event.data.id,rms:Math.sqrt(power/Math.max(1,count)),peak});
};
