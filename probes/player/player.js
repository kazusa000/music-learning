// The audio clock owns position. UI timers never advance the playhead.
export function startBuffer(context,buffer,when,offset,loop=null){
  const source=context.createBufferSource();source.buffer=buffer;source.connect(context.destination);
  if(loop){source.loop=true;source.loopStart=loop.start;source.loopEnd=loop.end;}
  source.start(when,offset);return source;
}
export class ProbePlayer {
  constructor() { this.context=new AudioContext({sampleRate:48000});this.buffers=new Map();this.offset=0;this.started=0;this.source=null;this.loop=null;this.name=null;this.loading=0; }
  async load(name) {
    const generation=++this.loading;
    if(!this.buffers.has(name)) {
      const response=await fetch(`audio/${encodeURIComponent(name)}`);
      if(!response.ok) throw new Error(`Audio HTTP ${response.status}`);
      const buffer=await this.context.decodeAudioData(await response.arrayBuffer());
      if(generation!==this.loading) return;
      this.buffers.set(name,buffer);
    }
    this.pause();this.name=name;this.offset=0;this.loop=null;
    return this.state();
  }
  position() {
    const raw=this.source?this.offset+Math.max(0,this.context.currentTime-this.started):this.offset;
    if(this.loop && raw>=this.loop.end) return this.loop.start+(raw-this.loop.start)%(this.loop.end-this.loop.start);
    return Math.min(raw,this.buffer?.duration??0);
  }
  get buffer(){return this.buffers.get(this.name)}
  async play() {
    if(!this.buffer) throw new Error('Load an asset first');
    if(this.source)return;
    await this.context.resume();
    if(this.offset>=this.buffer.duration)this.offset=0;
    this.started=this.context.currentTime+.03;
    const source=startBuffer(this.context,this.buffer,this.started,this.offset,this.loop);this.source=source;
    source.onended=()=>{if(this.source===source){this.offset=this.buffer.duration;this.source=null;}};
  }
  pause() {
    const offset=this.position();const source=this.source;this.source=null;
    if(source){source.onended=null;source.stop();source.disconnect();}
    this.offset=offset;
  }
  seek(seconds) {
    if(!this.buffer || !Number.isFinite(seconds) || seconds<0 || seconds>this.buffer.duration)throw new Error('Invalid seek');
    const playing=Boolean(this.source);this.pause();this.offset=seconds;
    if(this.loop && (seconds<this.loop.start||seconds>=this.loop.end))this.offset=this.loop.start;
    return playing?this.play():Promise.resolve();
  }
  setLoop(start,end) {
    if(start===null){const playing=Boolean(this.source);this.pause();this.loop=null;return playing?this.play():Promise.resolve();}
    if(!this.buffer||!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start||end>this.buffer.duration)throw new Error('Invalid loop');
    const playing=Boolean(this.source);this.pause();this.loop={start,end};
    if(this.offset<start||this.offset>=end)this.offset=start;
    return playing?this.play():Promise.resolve();
  }
  state(){return {asset:this.name,playing:Boolean(this.source),context_state:this.context.state,
    seconds:this.position(),duration:this.buffer?.duration??0,loop:this.loop,
    decoded_pcm_bytes:[...this.buffers.values()].reduce((total,b)=>total+b.length*b.numberOfChannels*4,0)}}
  async close(){this.pause();this.buffers.clear();await this.context.close();}
}
