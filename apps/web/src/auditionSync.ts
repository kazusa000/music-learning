import type {Operation} from '../../../packages/contracts/types';

type Invoke=<T>(operation:Operation,args?:object,expectedRevision?:string,write?:boolean)=>Promise<T>;
type Failure=Error&{code?:string};

// Transport state does not edit music. Refresh its optimistic head on an
// explicit conflict, while preserving historical audition targets and sequence
// fencing. Never retry an unknown/network failure or a musical edit here.
export async function writeAudition<T>(invoke:Invoke,projectId:string,operation:'audition.set'|'audition.report',args:Record<string,unknown>,head:()=>string,onHead:(id:string)=>void,followHead=false):Promise<T|null>{
  let expected=head();
  for(let attempt=0;;attempt++){
    const arguments_:Record<string,unknown>={...args,project_id:projectId};
    if(operation==='audition.set'&&followHead)arguments_.state={...(args.state as object),revision_id:expected};
    try{return await invoke<T>(operation,arguments_,expected,true);}
    catch(error){
      const code=(error as Failure).code;
      if(operation==='audition.report'&&code==='transport_superseded')return null;
      if(code!=='revision_conflict'||attempt>=2)throw error;
      const project=await invoke<{current_revision:string}>('projects.get',{project_id:projectId});
      expected=project.current_revision;onHead(expected);
    }
  }
}
