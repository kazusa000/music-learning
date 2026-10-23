import {createContext,useContext,useEffect,useRef,useState} from 'react';
import type {ReactNode} from 'react';
import {call} from './api';
export type ReasoningEffort=string;
export type Preferences={default_model:string|null;login_mode:'isolated'|'existing';level_match:boolean;reasoning_effort:ReasoningEffort;fast_mode:boolean};
export type SettingsView={version:number;configured:boolean;preferences:Preferences;runtime:{data_root:string;instrument_library:string|null;plugin_roots:string[]};analysis_models:Array<{id:string;name:string;purpose:string}>;background_verification:{status:string;total:number;counts:Record<string,number>}|null};
const initial:Preferences={default_model:null,login_mode:'isolated',level_match:true,reasoning_effort:'low',fast_mode:false};
const Context=createContext<{view:SettingsView|null;prefs:Preferences;error:string;update:(patch:Partial<Preferences>)=>Promise<void>;refresh:()=>Promise<void>}>({view:null,prefs:initial,error:'',update:async()=>{},refresh:async()=>{}});
export const usePreferences=()=>useContext(Context);
export function PreferencesProvider({ready,children}:{ready:Promise<unknown>;children:ReactNode}){
  const [view,setView]=useState<SettingsView|null>(null),[error,setError]=useState('');
  const current=useRef(view);current.current=view;const writes=useRef(Promise.resolve());
  async function refresh(){const value=await call<SettingsView>('application.settings.get');current.current=value;setView(value);}
  async function update(patch:Partial<Preferences>){
    const run=writes.current.catch(()=>{}).then(async()=>{if(!current.current)await refresh();
      const value=await call<SettingsView>('application.settings.set',{expected_version:current.current!.version,preferences:patch},undefined,true);current.current=value;setView(value);setError('');});
    writes.current=run;try{await run;}catch(e){setError((e as Error).message);await refresh();throw e;}
  }
  useEffect(()=>{let active=true;void ready.then(async()=>{
    const value=await call<SettingsView>('application.settings.get');if(!active)return;current.current=value;setView(value);
    if(!value.configured){const model=localStorage.getItem('discussion.model'),login=localStorage.getItem('music-learning-login-mode');
      if(model||login==='existing')await update({default_model:model||null,login_mode:login==='existing'?'existing':'isolated'});}
  }).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[ready]);
  return <Context.Provider value={{view,prefs:view?.preferences??initial,error,update,refresh}}>{children}</Context.Provider>;
}
