// One SSE connection owned by App; panels subscribe without replaying history.
export type DomainEvent={type:string;project_id:string|null;revision_id:string|null;job_id:string|null};
const events=new EventTarget();
export function publishDomain(event:DomainEvent){events.dispatchEvent(new CustomEvent('domain',{detail:event}));}
export function subscribeDomain(listener:(event:DomainEvent)=>void){
  const receive=(event:Event)=>listener((event as CustomEvent<DomainEvent>).detail);
  events.addEventListener('domain',receive);
  return()=>events.removeEventListener('domain',receive);
}
