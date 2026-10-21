// Runs only in the invisible, non-activating WebView2 verification window.
(async()=>{
 const report={checks:[],errors:[]};
 const until=async(check,label)=>{const end=Date.now()+15000;while(Date.now()<end){const value=await check();if(value)return value;await new Promise(r=>setTimeout(r,50));}throw Error(label);};
 const project=new URL(location.href).searchParams.get('project');
 const call=async(operation,args={})=>{const response=await fetch('/v1/commands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation,arguments:args,idempotency_key:crypto.randomUUID().replaceAll('-','')})});const value=await response.json();if(!value.ok)throw Error(JSON.stringify(value.error));return value.result;};
 window.addEventListener('error',event=>report.errors.push(event.message));
 try{
  if(!window.mlDesktop)throw Error('Native desktop bridge missing');
  await until(()=>document.querySelector('.studio'),'Workspace did not load');
  const state=await until(async()=>{const s=await call('engine.status');return s.status==='ready'&&s.project_id===project?s:null;},'Native engine not ready');
  report.checks.push('Real WebView2 and native engine ready');
  const button=label=>[...document.querySelectorAll('button')].find(el=>el.getAttribute('aria-label')===label||el.title===label||el.textContent.trim()===label);
  const play=await until(()=>{const el=button('播放');return el&&!el.disabled?el:null;},'Play button unavailable');play.click();
  await until(async()=>{const s=await call('engine.status');return s.playing&&s.peak>0;},'Native window did not start actual device output');
  const id=Object.keys(state.monitor)[0];
  const solo=document.querySelector('[aria-label="独奏 1"]');if(!solo)throw Error('Track solo missing');solo.click();
  await until(async()=>{const s=await call('engine.status');return s.monitor[id].solo&&Object.entries(s.meters).every(([key,value])=>key===id||value===0);},'Solo did not silence other device tracks');
  solo.click();
  report.checks.push('Play and live solo from native window controls');
  const original=[...document.querySelectorAll('.mode-switch button')].find(el=>el.textContent.includes('原曲'));original.click();
  await until(async()=>{const s=await call('engine.status');return s.view==='original'&&s.playing;},'Native original switch failed');
  const arrangement=[...document.querySelectorAll('.mode-switch button')].find(el=>el.textContent.includes('重建'));arrangement.click();
  await until(async()=>{const s=await call('engine.status');return s.view==='arrangement'&&s.playing;},'Native reconstruction switch failed');
  report.checks.push('Original and reconstruction maintain transport');
  const pause=await until(()=>{const el=button('暂停');return el&&!el.disabled?el:null;},'Pause button unavailable');pause.click();
  await until(async()=>!(await call('engine.status')).playing,'Native pause failed');
  report.final=await call('engine.status');
  if((await call('jobs.list',{project_id:project})).length)throw Error('Playback created a render job');
  if(report.errors.length)throw Error(report.errors.join(';'));
  report.checks.push('Pause and no project render jobs');report.status='succeeded';
 }catch(error){report.status='failed';report.error=String(error.stack||error);}
 finally{window.__nativeQAResult=report;}
})();
