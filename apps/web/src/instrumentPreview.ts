import {call} from './api';
import type {InstrumentPreview} from '../../../packages/contracts/types';

export type PreviewState={presetId:string;phase:'loading'|'playing'|'failed';error?:string;message?:string;name?:string};
export class InstrumentPreviewPlayer {
  context:AudioContext|null=null;source:AudioBufferSourceNode|null=null;gain:GainNode|null=null;
  generation=0;state:PreviewState|null=null;volume=.7;disposed=false;
  constructor(private update:(state:PreviewState|null)=>void){}
  monitoring(value:number){
    if(typeof window==='undefined')return;
    (window as Window & {instrumentPreviewGain?:number}).instrumentPreviewGain=value;
    window.dispatchEvent(new CustomEvent('instrument-preview-monitor',{detail:value}));
  }
  setVolume(value:number){this.volume=value;if(this.gain)this.gain.gain.value=value;}
  stop(){this.generation++;if(this.source){this.source.onended=null;try{this.source.stop();}catch{}this.source.disconnect();}this.source=null;this.monitoring(1);this.state=null;this.update(null);}
  async toggle(presetId:string,retry=false){
    if(this.state?.presetId===presetId&&this.state.phase!=='failed'){this.stop();return;}
    this.stop();if(this.disposed)return;
    const generation=this.generation;
    const active=()=>generation===this.generation&&!this.disposed;
    const deadline=Date.now()+390000;
    this.state={presetId,phase:'loading'};this.update(this.state);
    try{
      // Unlock Web Audio directly in the click gesture, before network waits.
      if(!this.context){this.context=new AudioContext();this.gain=this.context.createGain();this.gain.gain.value=this.volume;this.gain.connect(this.context.destination);}
      await this.context.resume();
      let preview=await call<InstrumentPreview>('instruments.preview.prepare',{preset_id:presetId,retry},undefined,true);
      while(active()&&(preview.status==='queued'||preview.status==='loading')){
        if(Date.now()>deadline)throw Error('试听准备超时，可以重试');
        this.state={presetId,phase:'loading',name:preview.display_name,message:preview.status==='queued'?'等待试听准备…':'正在加载音源并制作试听…'};this.update(this.state);
        await new Promise(resolve=>setTimeout(resolve,250));if(!active())return;
        preview=await call<InstrumentPreview>('instruments.preview.get',{preview_id:preview.id});
      }
      if(!active())return;
      if(preview.status!=='ready'||!preview.audio_url)throw Error(preview.error??'试听未准备好，请重试');
      const response=await fetch(preview.audio_url);if(!response.ok)throw Error('试听读取失败，请重试');
      const audio=await this.context.decodeAudioData(await response.arrayBuffer());if(!active())return;
      const source=this.context.createBufferSource();source.buffer=audio;source.connect(this.gain!);this.source=source;
      source.onended=()=>{if(active())this.stop();};
      this.monitoring(.16);source.start();this.state={presetId,phase:'playing',name:preview.display_name};this.update(this.state);
    }catch(error){if(active()){this.monitoring(1);this.state={presetId,phase:'failed',error:error instanceof Error?error.message:'试听失败，请重试'};this.update(this.state);}}
  }
  dispose(){this.disposed=true;this.stop();void this.context?.close();}
}
