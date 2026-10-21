import {call} from '../api';
import type {MixValues} from '../liveMix';
import type {Operation} from '../../../../packages/contracts/types';

export type EngineState={status:string;engine_session?:string;generation?:number;project_id?:string;revision_id?:string;
  owner_id?:string;
  control_error?:string;
  applied_revision?:string|null;view?:string;source_sample_rate?:number;sample_rate?:number;duration_samples?:number;
  position_sample?:number;playing?:boolean;loop?:boolean;peak?:number;underruns?:number;xruns?:number;
  loop_start_sample?:number;loop_end_sample?:number;
  meters?:Record<string,number>;monitor?:Record<string,MixValues>;plugins?:Record<string,{status:string;error?:string;pid?:number;load_stage?:number}>};
type Target={project:string;revision:string;view:'original'|'arrangement'};
type Invoke=<T>(operation:Operation,args?:Record<string,unknown>)=>Promise<T>;

/** Control and telemetry only. The native device owns every audio sample. */
export class NativeTransport {
  state:EngineState={status:'stopped'};
  disposed=false;
  onEnded:(()=>void)|null=null;
  onState:((state:EngineState)=>void)|null=null;
  onError:((error:Error)=>void)|null=null;
  private queue:Promise<unknown>=Promise.resolve();
  private observedAt=performance.now();
  private timer:ReturnType<typeof setTimeout>|null=null;
  private polling=false;
  private target:Target|null=null;
  private owner=crypto.randomUUID().replaceAll('-','');
  private attached=false;
  private pagehide=()=>{void this.close();};
  private attach(){if(!this.attached&&typeof window!=='undefined'){this.attached=true;window.addEventListener('pagehide',this.pagehide);}}
  private offsetOverride:number|null=null;
  private pendingMonitor:Record<string,MixValues>={};
  private monitorFlush:Promise<void>|null=null;
  constructor(private invoke:Invoke=call){}
  get playing(){return !!this.state.playing;}
  get ownsOutput(){return this.state.owner_id===this.owner;}
  get loop(){return !!this.state.loop;}
  get duration(){return (this.state.duration_samples??0)/(this.state.source_sample_rate??44100);}
  get position(){
    const base=this.offsetOverride??(this.state.position_sample??0)/(this.state.source_sample_rate??44100);
    // Display-only interpolation. Transport decisions always use native samples.
    return Math.min(this.duration||Infinity,base+(this.playing&&!this.loop?Math.min(.25,(performance.now()-this.observedAt)/1000):0));
  }
  get renderPosition(){return this.position;}
  get offset(){return this.position;}
  set offset(seconds:number){this.offsetOverride=seconds;}
  get liveErrors(){return Object.fromEntries(Object.entries(this.state.plugins??{}).filter(([,p])=>p.status==='failed').map(([id,p])=>[id,p.error??'音轨载入失败']));}
  meterLevels(){return this.state.meters??{};}
  private accept(state:EngineState){
    if(this.disposed)return;
    const ended=this.state.playing&&!state.playing&&state.project_id===this.state.project_id&&
      (state.position_sample??0)>=(state.duration_samples??Infinity);
    this.state=state;this.offsetOverride=null;this.observedAt=performance.now();this.onState?.(state);
    if(ended)this.onEnded?.();
  }
  private serialize<T>(action:()=>Promise<T>):Promise<T>{
    const next=this.queue.catch(()=>{}).then(()=>{if(this.disposed)throw new DOMException('播放窗口已关闭','AbortError');return action();});
    this.queue=next;return next;
  }
  startPolling(){
    this.attach();
    if(this.polling||this.disposed)return;this.polling=true;
    const poll=async()=>{
      if(!this.polling||this.disposed)return;
      try{await this.serialize(async()=>this.accept(await this.invoke<EngineState>('engine.status')));}catch(error){if(!this.disposed)this.onError?.(error as Error);}
      if(this.polling&&!this.disposed)this.timer=setTimeout(poll,100);
    };void poll();
  }
  private async ensure(target:Target,force=false){
    for(let attempt=0;;attempt++){
      try{return await this.ensureOnce(target,force);}catch(error){
        if(attempt||this.disposed||!this.conflict(error))throw error;
      }
    }
  }
  private conflict(error:unknown){return /会话已变更|generation changed|no longer owns output/i.test((error as Error).message??'');}
  private async controlled(action:'engine.transport'|'engine.monitor',values:Record<string,unknown>){
    for(let attempt=0;;attempt++){
      try{this.accept(await this.invoke<EngineState>(action,{engine_session:this.state.engine_session,generation:this.state.generation,owner_id:this.owner,...values}));return;}
      catch(error){
        if(attempt||this.disposed||!this.conflict(error))throw error;
        this.accept(await this.invoke<EngineState>('engine.status'));
        if(this.state.project_id!==this.target?.project||this.state.view!==this.target?.view)throw error;
        if(!this.ownsOutput)this.accept(await this.invoke<EngineState>('engine.claim',{engine_session:this.state.engine_session,generation:this.state.generation,owner_id:this.owner}));
      }
    }
  }
  private async ensureOnce(target:Target,force=false){
    let state=await this.invoke<EngineState>('engine.status');this.accept(state);
    if(state.project_id===target.project&&state.view===target.view&&state.owner_id!==this.owner){
      this.accept(await this.invoke<EngineState>('engine.claim',{engine_session:state.engine_session,generation:state.generation,owner_id:this.owner}));state=this.state;
    }
    if(force||state.project_id!==target.project||state.view!==target.view||state.status==='stopped'||state.status==='closed'){
      this.accept(await this.invoke<EngineState>('engine.open',{project_id:target.project,revision_id:target.revision,view:target.view,
        owner_id:this.owner,position_sample:state.project_id===target.project?(state.position_sample??0):0,playing:state.project_id===target.project&&state.playing}));
    }else if(state.revision_id!==target.revision){
      this.accept(await this.invoke<EngineState>('engine.update',{project_id:target.project,revision_id:target.revision,view:target.view,
        engine_session:state.engine_session,generation:state.generation,owner_id:this.owner}));
    }
    if(this.state.owner_id!==this.owner)this.accept(await this.invoke<EngineState>('engine.claim',{
      engine_session:this.state.engine_session,generation:this.state.generation,owner_id:this.owner}));
    this.target=target;
  }
  open(target:Target,force=false){this.attach();return this.serialize(()=>this.ensure(target,force));}
  async waitTrack(target:Target,track:string,retry=false){
    await this.open(target,retry);const deadline=Date.now()+60000;
    while(!this.disposed&&Date.now()<deadline){
      const state=await this.serialize(async()=>{const value=await this.invoke<EngineState>('engine.status');this.accept(value);return value;});
      if(this.target?.revision!==target.revision)throw new Error('音轨的音色已发生变化');
      const plugin=state.plugins?.[track];
      if(plugin?.status==='failed')throw new Error(plugin.error??'音轨载入失败');
      if(!plugin||plugin.status==='ready')return;
      await new Promise(resolve=>setTimeout(resolve,80));
    }
    throw new Error('音轨载入超时，可以重试');
  }
  apply(target:Target,values:{playing:boolean;sample?:number;loop?:boolean;start?:number;end?:number},monitor?:Record<string,MixValues>){
    this.attach();
    return this.serialize(async()=>{
      await this.ensure(target);
      if(monitor&&Object.keys(monitor).length)await this.monitorNow(monitor);
      const state=this.state;
      const changedLoop=values.loop!==undefined&&(values.loop!==state.loop||values.start!==state.loop_start_sample||values.end!==state.loop_end_sample);
      await this.controlled('engine.transport',{
        playing:values.playing,...(values.sample!==undefined?{position_sample:values.sample}:{}),
        ...(changedLoop?{loop:values.loop,loop_start_sample:values.start,loop_end_sample:values.end}:{})});
    });
  }
  private async monitorNow(tracks:Record<string,MixValues>){
    this.accept(await this.invoke<EngineState>('engine.status'));
    if(this.target&&(this.state.project_id!==this.target.project||this.state.view!==this.target.view))return;
    if(this.target&&!this.ownsOutput)this.accept(await this.invoke<EngineState>('engine.claim',{engine_session:this.state.engine_session,generation:this.state.generation,owner_id:this.owner}));
    const available=new Set(Object.keys(this.state.monitor??{}));
    const filtered=Object.fromEntries(Object.entries(tracks).filter(([id])=>available.has(id)).map(([id,values])=>
      [id,Object.fromEntries(Object.entries(values).filter(([key])=>['gain_db','pan','mute','solo'].includes(key)))]));
    if(!Object.keys(filtered).length)return;
    await this.controlled('engine.monitor',{tracks:filtered});
  }
  monitor(tracks:Record<string,MixValues>){
    for(const [id,values] of Object.entries(tracks))this.pendingMonitor[id]={...this.pendingMonitor[id],...values};
    if(!this.monitorFlush)this.monitorFlush=this.serialize(async()=>{
      while(Object.keys(this.pendingMonitor).length){const pending=this.pendingMonitor;this.pendingMonitor={};await this.monitorNow(pending);}
    }).finally(()=>{this.monitorFlush=null;});
    return this.monitorFlush;
  }
  pause(){if(this.disposed)return;void this.serialize(async()=>{
    this.accept(await this.invoke<EngineState>('engine.status'));
    if(!this.ownsOutput)return;
    if(!this.state.engine_session)return;
    await this.controlled('engine.transport',{playing:false});
  }).catch(error=>this.onError?.(error));}
  async close(){
    if(this.disposed)return;this.disposed=true;
    this.polling=false;if(this.timer)clearTimeout(this.timer);
    if(this.attached&&typeof window!=='undefined')window.removeEventListener('pagehide',this.pagehide);
    // Register closure before awaiting in-flight preparation. The server rejects
    // later commands from this owner, even when it never obtained a session.
    const closing=this.invoke('engine.close',{owner_id:this.owner}).catch(()=>{});
    await this.queue.catch(()=>{});await closing;
  }
}
