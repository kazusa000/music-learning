import {listeningGain,type Level} from './listeningLevel';
import {channelGains,type MixChannel,type LiveTrack} from './liveMix';
import {call} from './api';
/** One audio clock schedules every stem, including loops and A/B switches. */
export class Transport {
  context: AudioContext | null = null;
  buffers = new Map<string, AudioBuffer>();
  pending = new Map<string, Promise<AudioBuffer>>();
  sources: AudioBufferSourceNode[] = [];
  voices = new Map<string,{source:AudioBufferSourceNode;gain:GainNode;left?:GainNode;right?:GainNode;meter?:AnalyserNode;assetId?:string}>();
  channels=new Map<string,MixChannel>();
  output:GainNode|null=null;
  liveClient=crypto.randomUUID().replaceAll('-','');
  liveSequence=0;referenceSequence=0;referenceClient=crypto.randomUUID().replaceAll('-','');
  streams=new Map<string,{epoch:number;timer?:ReturnType<typeof setTimeout>;nodes:Set<AudioBufferSourceNode>;session:LiveTrack}>();
  liveErrors:Record<string,string>={};
  async prepareLive(projectId:string,revisionId:string,trackId:string,sequence:number,clientId=this.liveClient):Promise<LiveTrack>{
    let live=await call<LiveTrack>('playback.live.open',{project_id:projectId,revision_id:revisionId,track_id:trackId,client_id:clientId,client_sequence:sequence});
    while(live.status==='loading'&&!this.disposed&&(clientId!==this.liveClient||sequence===this.liveSequence)){await new Promise(resolve=>setTimeout(resolve,30));live=await call<LiveTrack>('playback.live.status',{session_id:live.session_id});}
    if(live.status==='failed')throw new Error(live.error??'音轨音色载入失败');return live;
  }
  stopStream(id:string,close=false){const stream=this.streams.get(id);if(!stream)return;this.streams.delete(id);clearTimeout(stream.timer);for(const source of stream.nodes){try{source.stop();}catch{/* ended */}}
    if(close)void call('playback.live.close',{session_id:stream.session.session_id}).catch(()=>{});
  }
  liveVoice(id:string,live:LiveTrack){
    const prior=this.streams.get(id);if(prior?.session.session_id===live.session_id&&prior.session.generation===live.generation)return;
    // Existing audio stays connected until the replacement has its first PCM.
    const context=this.context!,epoch=++this.streamEpoch,stream={epoch,nodes:new Set<AudioBufferSourceNode>(),session:live,timer:undefined as ReturnType<typeof setTimeout>|undefined};
    this.streams.set(id,stream);let nextWhen=0,nextSample=0,connected=false;
    let gain:GainNode,left:GainNode,right:GainNode,meter:AnalyserNode;
    const pump=async()=>{
      if(!this.playing||this.disposed||this.streams.get(id)!==stream)return;
      try{
        while(!connected||nextWhen<context.currentTime+.12){
          const when=connected?nextWhen:context.currentTime+.03;
          if(!connected||when<context.currentTime+.015){nextSample=Math.round(this.at(Math.max(when,context.currentTime+.03))*live.sample_rate);nextWhen=Math.max(when,context.currentTime+.03);}
          const boundary=this.loop?Math.round(this.loopEnd*live.sample_rate):live.duration_samples;
          if(nextSample>=boundary){if(this.loop)nextSample=Math.round(this.loopStart*live.sample_rate);else return;}
          const frames=Math.min(4096,boundary-nextSample);
          const part=await call<{pcm_f32le:string;frames:number;generation:number}>('playback.live.chunk',{session_id:live.session_id,generation:live.generation,sample:nextSample,frames});
          if(!this.playing||this.streams.get(id)!==stream)return;
          if(nextWhen<context.currentTime+.008){nextWhen=context.currentTime+.03;nextSample=Math.round(this.at(nextWhen)*live.sample_rate);continue;}
          const raw=atob(part.pcm_f32le),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
          const values=new Float32Array(bytes.buffer),buffer=context.createBuffer(2,part.frames,live.sample_rate);
          for(let ch=0;ch<2;ch++){const data=buffer.getChannelData(ch);for(let i=0;i<part.frames;i++)data[i]=values[i*2+ch];}
          if(!connected){
            const old=this.voices.get(id);if(old){old.gain.gain.setTargetAtTime(0,nextWhen,.003);setTimeout(()=>{old.gain.disconnect();old.meter?.disconnect();},Math.max(0,(nextWhen-context.currentTime+.03)*1000));try{old.source.stop(nextWhen+.02);}catch{/* stream placeholder */}}
            if(prior){clearTimeout(prior.timer);for(const n of prior.nodes){try{n.stop(nextWhen+.02);}catch{/* ended */}}if(prior.session.session_id!==live.session_id)void call('playback.live.close',{session_id:prior.session.session_id}).catch(()=>{});}
            gain=context.createGain();left=context.createGain();right=context.createGain();meter=context.createAnalyser();meter.fftSize=512;
            const merger=context.createChannelMerger(2);left.connect(merger,0,0);right.connect(merger,0,1);merger.connect(gain);gain.connect(meter);
            if(!this.output){this.output=context.createGain();this.output.connect(context.destination);}meter.connect(this.output);
            this.voices.set(id,{source:context.createBufferSource(),gain,left,right,meter,assetId:this.channels.get(id)?.assetId});connected=true;delete this.liveErrors[id];this.route(this.activeIds);
          }
          const source=context.createBufferSource(),split=context.createChannelSplitter(2);source.buffer=buffer;source.connect(split);split.connect(left!,0);split.connect(right!,1);
          source.start(nextWhen);stream.nodes.add(source);source.onended=()=>{stream.nodes.delete(source);source.disconnect();split.disconnect();};
          nextWhen+=part.frames/live.sample_rate;nextSample+=part.frames;
        }
      }catch(error){if(this.streams.get(id)===stream){const e=error as Error&{code?:string};if(e.code==='live_superseded'||e.code==='live_not_ready')return;this.liveErrors[id]=e.message;return;}}
      if(this.streams.get(id)===stream)stream.timer=setTimeout(()=>void pump(),12);
    };void pump();
  }
  streamEpoch=0;
  configureChannels(channels:MixChannel[]){
    const keep=new Set(channels.map(c=>'track:'+c.id));
    for(const id of this.channels.keys())if(!keep.has(id)){this.stopStream(id,true);const voice=this.voices.get(id);if(voice){try{voice.source.stop();}catch{/* placeholder */}voice.gain.disconnect();voice.meter?.disconnect();this.voices.delete(id);}}
    for(const c of channels){const id='track:'+c.id;const old=this.channels.get(id)?.live;if(old&&old.session_id!==c.live?.session_id&&(!this.playing||!this.voices.has(id)))void call('playback.live.close',{session_id:old.session_id}).catch(()=>{});if(this.channels.get(id)?.assetId!==c.assetId){this.buffers.delete(id);this.levels.delete(id);}}
    this.channels=new Map(channels.map(c=>['track:'+c.id,c]));this.route(this.activeIds);
  }
  meterLevels(){const result:Record<string,number>={};if(!this.playing)return result;for(const [id,voice] of this.voices){if(!voice.meter||!id.startsWith('track:'))continue;const values=new Float32Array(voice.meter.fftSize);voice.meter.getFloatTimeDomainData(values);const rms=Math.sqrt(values.reduce((n,v)=>n+v*v,0)/values.length);result[id.slice(6)]=Math.max(-60,20*Math.log10(Math.max(rms,1e-9)));}return result;}
  families = new Map<string,string>();
  levels = new Map<string,Level>();
  levelPending = new Map<string,{resolve:(level:Level)=>void;reject:(error:Error)=>void}>();
  analyzer:Worker|null=null;
  matched=true;
  matchedGains:Record<string,number>={};
  abort=new AbortController();
  warmGeneration=0;
  switches:Array<{at:number;ids:string[];reused:boolean}>=[];
  family(mix:string,stems:string[]){for(const id of [mix,...stems])this.families.set(id,mix);}
  async measure(id:string,buffer:AudioBuffer){
    if(this.levels.has(id))return this.levels.get(id)!;
    if(!this.analyzer){
      this.analyzer=new Worker(new URL('./levels.worker.ts',import.meta.url),{type:'module'});
      this.analyzer.onmessage=event=>{const {id,rms,peak}=event.data;const level={rms,peak};this.levels.set(id,level);this.levelPending.get(id)?.resolve(level);this.levelPending.delete(id);};
      this.analyzer.onerror=()=>{for(const request of this.levelPending.values())request.reject(new Error('试听电平分析失败'));this.levelPending.clear();};
    }
    const channels=Array.from({length:buffer.numberOfChannels},(_,i)=>new Float32Array(buffer.getChannelData(i)));
    const result=new Promise<Level>((resolve,reject)=>this.levelPending.set(id,{resolve,reject}));
    this.analyzer.postMessage({id,channels},channels.map(c=>c.buffer));return result;
  }
  gain(id:string){return this.matched?(this.matchedGains[this.families.get(id)??id]??listeningGain(this.levels.get(this.families.get(id)??id)??this.levels.get(id)??{rms:0,peak:0})):1;}
  setMatched(value:boolean){if(this.matched===value)return;this.matched=value;this.route(this.activeIds);}
  activeIds:string[]=[];
  route(ids:string[]){
    this.activeIds=[...ids];const context=this.context;if(!context)return;
    // Conservative sum-of-peaks ceiling also protects partial stem mixtures.
    const bound=ids.reduce((sum,id)=>sum+(this.levels.get(id)?.peak??0)*this.gain(id),0);
    // Mixer faders must not be cancelled by automatic renormalization.
    const ceiling=ids.some(id=>this.channels.has(id))?1:this.matched&&bound>.794?.794/bound:1;
    const soloed=Array.from(this.channels.entries()).some(([id,c])=>ids.includes(id)&&c.values.solo);
    for(const [id,voice] of this.voices){const gain=ids.includes(id)?this.gain(id)*ceiling:0;
      voice.gain.gain.cancelScheduledValues(context.currentTime);
      voice.gain.gain.setTargetAtTime(gain,context.currentTime,.003);
      const channel=this.channels.get(id);
      if(channel&&voice.left&&voice.right){const [left,right]=channelGains(channel,soloed);
        voice.left.gain.setTargetAtTime(left,context.currentTime,.003);voice.right.gain.setTargetAtTime(right,context.currentTime,.003);}
    }
  }
  async warm(ids:string[]){
    const generation=++this.warmGeneration,queue=[...new Set(ids)].filter(id=>!this.channels.get(id)?.live);
    // Two decoders; only this revision's source/mix/stems are retained.
    for(const id of this.buffers.keys())if(!queue.includes(id)&&!this.voices.has(id)){this.buffers.delete(id);this.levels.delete(id);}
    await Promise.all([0,1].map(async()=>{while(queue.length&&generation===this.warmGeneration&&!this.disposed){const id=queue.shift()!;try{await this.buffer(id);}catch(error){console.warn('Audio preload:',error);}}}));
  }
  voice(id:string,when:number,position:number){
    const live=this.channels.get(id)?.live;if(live){this.liveVoice(id,live);return;}
    this.stopStream(id);
    const context=this.context!,buffer=this.buffers.get(id)!;
    const source=context.createBufferSource(),gain=context.createGain();source.buffer=buffer;
    source.loop=this.loop;source.loopStart=this.loopStart;source.loopEnd=this.loopEnd;
    gain.gain.value=0;
    let left:GainNode|undefined,right:GainNode|undefined;
    if(this.channels.has(id)){
      const splitter=context.createChannelSplitter(2),merger=context.createChannelMerger(2);left=context.createGain();right=context.createGain();
      source.connect(splitter);splitter.connect(left,0);splitter.connect(right,1);left.connect(merger,0,0);right.connect(merger,0,1);merger.connect(gain);
    }else source.connect(gain);
    if(!this.output){this.output=context.createGain();this.output.gain.value=1;this.output.connect(context.destination);}
    const meter=context.createAnalyser();meter.fftSize=512;gain.connect(meter);meter.connect(this.output);
    source.start(when,Math.min(position,Math.max(0,buffer.duration-.001)));
    source.onended=()=>{source.disconnect();gain.disconnect();meter.disconnect();this.sources=this.sources.filter(s=>s!==source);if(this.voices.get(id)?.source===source)this.voices.delete(id);};
    this.voices.set(id,{source,gain,left,right,meter,assetId:this.channels.get(id)?.assetId??id});this.sources.push(source);
  }
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
  async buffer(id:string):Promise<AudioBuffer> {
    const channel=this.channels.get(id);

    if(channel){if(this.buffers.has(id))return this.buffers.get(id)!;const buffer=await this.buffer(channel.assetId);this.buffers.set(id,buffer);const level=this.levels.get(channel.assetId);if(level)this.levels.set(id,level);return buffer;}
    if (this.pending.has(id)) return this.pending.get(id)!;
    if (this.buffers.has(id)) return this.buffers.get(id)!;
    if (!this.pending.has(id)) this.pending.set(id,(async () => {
      this.context ??= new AudioContext({latencyHint:'interactive'});
      const response = await fetch('/v1/assets/'+id,{signal:this.abort.signal});
      if (!response.ok) throw new Error('音频读取失败');
      const buffer = await this.context.decodeAudioData(await response.arrayBuffer());
      if (!this.disposed) {
        await this.measure(id,buffer);
        if(!this.disposed){
          const resident=new Set([...this.buffers.values(),buffer]);let bytes=Array.from(resident).reduce((n,b)=>n+b.length*b.numberOfChannels*4,0);
          for(const [key,existing] of this.buffers){if(bytes<=1024**3)break;if(!this.voices.has(key)){this.buffers.delete(key);if(!Array.from(this.buffers.values()).includes(existing))bytes-=existing.length*existing.numberOfChannels*4;}}
          this.buffers.set(id,buffer);
        }
      }
      return buffer;
    })().finally(() => this.pending.delete(id)));
    return this.pending.get(id)!;
  }
  async play(ids:string[], offset:number, loop:{enabled:boolean;start:number;end:number}, preserve=false) {
    const generation = ++this.generation;
    if (!ids.length) {this.pause(); return;}
    await this.activate();
    // A live mix shares the level of its baked reference mix. Do not start
    // with per-stem normalization while the reference is still preloading:
    // otherwise the first play changes balance/loudness after cache warm-up.
    if(this.matched){
      const references=[...new Set(ids.map(id=>this.families.get(id)).filter((id):id is string=>!!id))];
      await Promise.all(references.filter(id=>this.matchedGains[id]===undefined&&!this.levels.has(id)).map(id=>this.buffer(id)));
    }
    const buffers = await Promise.all(ids.map(id => {const live=this.channels.get(id)?.live;return live?Promise.resolve({duration:live.duration_samples/live.sample_rate}):this.buffer(id);}));
    if (generation !== this.generation || this.disposed) return;
    const context = this.context!, when = context.currentTime+.01;
    const sameLoop=this.loop===loop.enabled&&this.loopStart===loop.start&&this.loopEnd===loop.end;
    if(preserve&&this.playing&&sameLoop){
      this.duration=Math.max(...buffers.map(buffer=>buffer.duration));
      for(const timeline of this.timelines)timeline.duration=this.duration;
      const position=this.at(when);
      for(const id of this.streams.keys())if(!ids.includes(id)){this.stopStream(id);const voice=this.voices.get(id);voice?.gain.disconnect();voice?.meter?.disconnect();this.voices.delete(id);}
      for(const id of ids){const old=this.voices.get(id);if(!old||old.assetId!==(this.channels.get(id)?.assetId??id)){if(!this.channels.get(id)?.live){try{old?.source.stop(when);}catch{/* live placeholder */}}this.voice(id,when,position);}}
      this.route(ids);this.switches.push({at:performance.now(),ids:[...ids],reused:true});if(this.switches.length>100)this.switches.shift();return;
    }
    const position = preserve && this.playing ? this.at(when) : offset;
    this.duration = Math.max(...buffers.map(b => b.duration));
    this.loop = loop.enabled;this.loopStart = loop.start;this.loopEnd = loop.end;
    this.offset = this.loop && position >= this.loopEnd ? this.loopStart : Math.min(position,Math.max(0,this.duration-.001));
    for(const source of this.sources){try{source.stop(when);}catch{ /* already ended */ }}
    this.sources=[];this.voices.clear();
    for(const id of this.streams.keys())this.stopStream(id);
    // Publish the common clock before a live channel requests its first chunk.
    this.started=when;this.playing=true;
    this.timelines.push({when,offset:this.offset,loop:this.loop,start:this.loopStart,end:this.loopEnd,duration:this.duration});
    for(const id of ids)this.voice(id,when,this.offset);
    this.route(ids);this.switches.push({at:performance.now(),ids:[...ids],reused:false});
    this.started = when;this.playing = true;
    if(this.timelines.length>12)this.timelines.shift();
    this.schedule.push({when,offset:this.offset,ids:[...ids]});
    if (this.schedule.length>100) this.schedule.shift();
  }
  pause() {
    this.offset = this.renderPosition;this.playing = false;++this.generation;this.timelines=[];
    for (const source of this.sources) {try {source.stop();} catch { /* already ended */ }}
    for(const id of this.streams.keys())this.stopStream(id);
    for(const voice of this.voices.values()){voice.gain.disconnect();voice.meter?.disconnect();}
    this.sources = [];this.voices.clear();this.activeIds=[];
  }
  async close() {
    for(const channel of this.channels.values())if(channel.live)void call('playback.live.close',{session_id:channel.live.session_id}).catch(()=>{});
    this.pause();this.disposed = true;++this.warmGeneration;this.abort.abort();this.buffers.clear();this.analyzer?.terminate();
    for(const request of this.levelPending.values())request.reject(new Error('试听已关闭'));this.levelPending.clear();
    if (this.context) await this.context.close();
  }
}
