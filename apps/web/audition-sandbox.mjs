// Read-only UI probes must not leave playback/citation state in the user's project.
export async function sandboxAudition(page,base,token){
  const states=new Map();let serial=0;
  await page.route('**/v1/commands',async route=>{
    const {operation,arguments:args}=route.request().postDataJSON();
    if(!['audition.get','audition.set','audition.report'].includes(operation)){await route.fallback();return;}
    if(!states.has(args.project_id)){
      const response=await fetch(base+'/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({operation:'audition.get',arguments:{project_id:args.project_id}})});
      const body=await response.json();if(!body.ok)throw new Error('Cannot read baseline audition state');
      states.set(args.project_id,structuredClone(body.result));
    }
    let result=states.get(args.project_id);
    if(operation==='audition.set'){result={sequence:'sandbox-'+(++serial),desired:args.state,observed:null};states.set(args.project_id,result);}
    if(operation==='audition.report')result={...result,observed:{position_sample:args.position_sample,status:args.status}};
    await route.fulfill({json:{schema_version:1,ok:true,result}});
  });
}
