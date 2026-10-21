import {call} from './api';
import type {InstrumentPreview} from '../../../packages/contracts/types';

export type PreviewState={presetId:string;phase:'loading'|'playing'|'failed';error?:string;message?:string;name?:string};
export class InstrumentPreviewPlayer {
  generation=0;state:PreviewState|null=null;volume=.7;disposed=false;
  private client=crypto.randomUUID().replaceAll('-','');
  private sequence=0;
  private timer:ReturnType<typeof setTimeout>|null=null;
  constructor(private update:(state:PreviewState|null)=>void){}
  setVolume(value:number){this.volume=value;if(this.state?.phase==='playing')void call('engine.preview',{client_id:this.client,client_sequence:++this.sequence,volume:value}).catch(()=>{});}
  stop(){this.generation++;if(this.timer)clearTimeout(this.timer);this.timer=null;void call('engine.preview',{client_id:this.client,client_sequence:++this.sequence,stop:true}).catch(()=>{});this.state=null;this.update(null);}
  async toggle(presetId:string,retry=false){
    if(this.state?.presetId===presetId&&this.state.phase!=='failed'){this.stop();return;}
    this.stop();if(this.disposed)return;
    const generation=this.generation;
    const active=()=>generation===this.generation&&!this.disposed;
    const deadline=Date.now()+390000;
    this.state={presetId,phase:'loading'};this.update(this.state);
    try{
      let preview=await call<InstrumentPreview>('instruments.preview.prepare',{preset_id:presetId,retry},undefined,true);
      while(active()&&(preview.status==='queued'||preview.status==='loading')){
        if(Date.now()>deadline)throw Error('试听准备超时，可以重试');
        this.state={presetId,phase:'loading',name:preview.display_name,message:preview.status==='queued'?'等待试听准备…':'正在加载音源并制作试听…'};this.update(this.state);
        await new Promise(resolve=>setTimeout(resolve,250));if(!active())return;
        preview=await call<InstrumentPreview>('instruments.preview.get',{preview_id:preview.id});
      }
      if(!active())return;
      if(preview.status!=='ready')throw Error(preview.error??'试听未准备好，请重试');
      await call('engine.preview',{client_id:this.client,client_sequence:++this.sequence,preview_id:preview.id,volume:this.volume});
      if(!active())return;
      this.state={presetId,phase:'playing',name:preview.display_name};this.update(this.state);
      const poll=async()=>{
        if(!active())return;
        try{const value=await call<{preview?:{playing:boolean;client_id:string}}>('engine.status');if(!active())return;
          if(!value.preview?.playing||value.preview.client_id!==this.client){this.state=null;this.update(null);return;}
          this.timer=setTimeout(poll,150);
        }catch(error){if(active()){this.state={presetId,phase:'failed',error:(error as Error).message};this.update(this.state);}}
      };this.timer=setTimeout(poll,150);
    }catch(error){if(active()){this.state={presetId,phase:'failed',error:error instanceof Error?error.message:'试听失败，请重试'};this.update(this.state);}}
  }
  dispose(){this.disposed=true;this.stop();}
}
