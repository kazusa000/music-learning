import type {Operation,AuditionState} from '../../../packages/contracts/types';
import {boundAudition} from './auditionState.ts';

type Invoke=<T>(operation:Operation,args?:object,expectedRevision?:string,write?:boolean)=>Promise<T>;
type Failure=Error&{code?:string};

// Transport state does not edit music. Refresh its optimistic head on an
// explicit conflict, while preserving historical audition targets and sequence
// fencing. Never retry an unknown/network failure or a musical edit here.
export async function writeAudition<T>(invoke:Invoke,projectId:string,operation:'audition.set'|'audition.report',args:Record<string,unknown>,head:()=>string,onHead:(id:string)=>void,followHead=false):Promise<T|null>{
  let expected=head();
  let duration:number|undefined;
  for(let attempt=0;;attempt++){
    const arguments_:Record<string,unknown>={...args,project_id:projectId};
    if(operation==='audition.set'&&followHead){const state={...(args.state as AuditionState),revision_id:expected};arguments_.state=duration===undefined?state:boundAudition(state,duration);}
    try{return await invoke<T>(operation,arguments_,expected,true);}
    catch(error){
      const code=(error as Failure).code;
      if(operation==='audition.report'&&code==='transport_superseded')return null;
      if(code!=='revision_conflict'||attempt>=2)throw error;
      const project=await invoke<{current_revision:string}>('projects.get',{project_id:projectId});
      expected=project.current_revision;onHead(expected);
      if(operation==='audition.set'&&followHead){const revision=await invoke<{snapshot:{duration_samples:number}}>('revisions.get',{project_id:projectId,revision_id:expected});duration=revision.snapshot.duration_samples;}
    }
  }
}
