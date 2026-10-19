/** One audio clock schedules every stem, including loops and A/B switches. */
export class Transport {
  context: AudioContext | null = null;
  buffers = new Map<string, AudioBuffer>();
  pending = new Map<string, Promise<AudioBuffer>>();
  sources: AudioBufferSourceNode[] = [];
  playing = false; offset = 0; started = 0; generation = 0; duration = 0;
  loop = false; loopStart = 0; loopEnd = 0; disposed = false;
  schedule: Array<{when:number; offset:number; ids:string[]}> = [];
  timelines:Array<{when:number;offset:number;loop:boolean;start:number;end:number;duration:number}>=[];

  async activate() {
    this.context ??= new AudioContext({latencyHint:'interactive'});
    if(this.context.state!=='running'&&!navigator.userActivation.hasBeenActive)throw new Error('请点击播放以启用浏览器音频。');
    await this.context.resume();
    if (this.context.state !== 'running') throw new Error('请点击播放以启用浏览器音频。');
  }
  at(clock:number) {
    const timeline=[...this.timelines].reverse().find(t=>t.when<=clock)??this.timelines[0];
    if(!timeline)return this.offset;
    let position=timeline.offset+Math.max(0,clock-timeline.when);
    if(timeline.loop&&position>=timeline.end)position=timeline.start+(position-timeline.start)%(timeline.end-timeline.start);
    return Math.min(timeline.duration||Infinity,position);
  }
  get renderPosition(){return this.playing&&this.context?this.at(this.context.currentTime):this.offset;}
  get position() {
    if(!this.playing||!this.context)return this.offset;
    const stamp=this.context.getOutputTimestamp();
    const clock=stamp.contextTime&&stamp.performanceTime
      ? stamp.contextTime+Math.max(0,performance.now()-stamp.performanceTime)/1000
      : Math.max(0,this.context.currentTime-this.context.baseLatency-(this.context.outputLatency??0));
    return this.at(clock);
  }
  async buffer(id:string) {
    if (this.buffers.has(id)) return this.buffers.get(id)!;
    if (!this.pending.has(id)) this.pending.set(id,(async () => {
      this.context ??= new AudioContext({latencyHint:'interactive'});
      const response = await fetch('/v1/assets/'+id);
      if (!response.ok) throw new Error('音频读取失败');
      const buffer = await this.context.decodeAudioData(await response.arrayBuffer());
      if (!this.disposed) this.buffers.set(id,buffer);
      return buffer;
    })().finally(() => this.pending.delete(id)));
    return this.pending.get(id)!;
  }
  async play(ids:string[], offset:number, loop:{enabled:boolean;start:number;end:number}, preserve=false) {
    const generation = ++this.generation;
    if (!ids.length) {this.pause(); return;}
    await this.activate();
    const buffers = await Promise.all(ids.map(id => this.buffer(id)));
    if (generation !== this.generation || this.disposed) return;
    const context = this.context!, when = context.currentTime+.025;
    const position = preserve && this.playing ? this.renderPosition+.025 : offset;
    this.duration = Math.max(...buffers.map(b => b.duration));
    this.loop = loop.enabled;this.loopStart = loop.start;this.loopEnd = loop.end;
    this.offset = this.loop && position >= this.loopEnd ? this.loopStart : Math.min(position,Math.max(0,this.duration-.001));
    for (const source of this.sources) {try {source.stop(when);} catch { /* already ended */ }}
    this.sources = buffers.map(buffer => {
      const source = context.createBufferSource();source.buffer = buffer;
      source.loop = this.loop;source.loopStart = this.loopStart;source.loopEnd = this.loopEnd;
      source.connect(context.destination);
      source.start(when,Math.min(this.offset,Math.max(0,buffer.duration-.001)));
      source.onended = () => source.disconnect();return source;
    });
    this.started = when;this.playing = true;
    this.timelines.push({when,offset:this.offset,loop:this.loop,start:this.loopStart,end:this.loopEnd,duration:this.duration});
    if(this.timelines.length>12)this.timelines.shift();
    this.schedule.push({when,offset:this.offset,ids:[...ids]});
    if (this.schedule.length>100) this.schedule.shift();
  }
  pause() {
    this.offset = this.renderPosition;this.playing = false;++this.generation;this.timelines=[];
    for (const source of this.sources) {try {source.stop();} catch { /* already ended */ }}
    this.sources = [];
  }
  async close() {
    this.pause();this.disposed = true;this.buffers.clear();
    if (this.context) await this.context.close();
  }
}
