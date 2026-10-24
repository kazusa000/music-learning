// Actual desktop/WebView2/native host workflow. No intercepted API responses.
(async()=>{
 const checks=[],pause=ms=>new Promise(r=>setTimeout(r,ms));
 async function until(test,message,timeout=45000){const end=Date.now()+timeout;while(Date.now()<end){const value=await test();if(value)return value;await pause(80);}throw Error(message);}
 async function api(operation,args={}){const response=await fetch('/v1/commands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation,arguments:args,idempotency_key:crypto.randomUUID().replaceAll('-','')})});const data=await response.json();if(!data.ok)throw Error(data.error?.message||operation);return data.result;}
 try{
  const model=new URLSearchParams(location.search).get('model');
  const gallery=await api('instruments.gallery'),item=gallery.items.find(x=>x.image_id===model);if(!item)throw Error('Missing curated model');
  await until(()=>document.querySelector('[aria-label="打开乐器 '+item.title+'"]'),'Gallery button unavailable');
  document.querySelector('[aria-label="打开乐器 '+item.title+'"]').click();
  const sid=await until(()=>document.querySelector('.instrument-room')?.dataset.sessionId,'Session did not open');
  const args={session_id:sid};
  await until(async()=>{const value=await api('instruments.studio.status',args);if(value.status==='failed')throw Error(value.error);return value.status==='ready';},'Native audio did not become ready');
  let slot;
  chrome.webview.addEventListener('message',e=>{if(e.data.type==='instrument-slot')slot=e.data;});
  [...document.querySelectorAll('.instrument-room-header nav button')].find(x=>x.textContent==='插件').click();
  const embedded=await until(async()=>{const value=await api('instruments.studio.status',args);if(value.status==='failed')throw Error(value.error);return value.editor_open&&value.editor_embedded&&slot&&value;},'Actual native editor did not embed');
  if(BigInt('0x'+embedded.editor_parent_handle)!==BigInt('0x'+slot.parent_handle))throw Error('Native editor parent does not match desktop panel: '+JSON.stringify({embedded,slot}));
  const stage=document.querySelector('.instrument-native-well').getBoundingClientRect();
  if(embedded.editor_pixels_width>stage.width*slot.scale+4||embedded.editor_pixels_height>stage.height*slot.scale+4)throw Error('Native editor overflows its reserved slot: '+JSON.stringify({embedded,stage:stage.toJSON(),slot}));
  checks.push('Actual desktop panel HWND is the native plugin parent, within physical-pixel slot bounds');
  const fitBounds={width:embedded.editor_pixels_width,height:embedded.editor_pixels_height};
  const zoom=document.querySelector('[aria-label="插件缩放"]');
  zoom.value='1';zoom.dispatchEvent(new Event('change',{bubbles:true}));
  const original=await until(async()=>{const value=await api('instruments.studio.status',args);return !value.editor_legacy_dpi&&value.editor_pixels_width&&value;},'Original-size editor unavailable');
  zoom.value='0';zoom.dispatchEvent(new Event('change',{bubbles:true}));
  await until(async()=>{const value=await api('instruments.studio.status',args);return value.editor_pixels_width===fitBounds.width&&value.editor_pixels_height===fitBounds.height;},'Fit did not restore editor size');
  if(model==='la-custom-c7'&&fitBounds.width<=original.editor_pixels_width*1.5)throw Error('Legacy editor remained tiny instead of using available 4K space');
  checks.push('Fit enlarges the legacy editor; original size and native fit remain selectable');
  document.querySelector('[aria-label="显示试弹键盘"]').click();
  await pause(400);
  const withKeyboard=await until(async()=>{const value=await api('instruments.studio.status',args),box=document.querySelector('.instrument-native-well').getBoundingClientRect();return value.editor_open&&value.editor_pixels_width<=box.width*devicePixelRatio+4&&value.editor_pixels_height<=box.height*devicePixelRatio+4&&value;},'Editor does not fit with the audition keyboard expanded');
  if(model==='la-custom-c7'&&withKeyboard.editor_pixels_width<original.editor_pixels_width*1.5)throw Error('Expanded keyboard made the legacy editor tiny again');
  checks.push('Expanded audition keyboard preserves a large complete plugin view');
  const before=await api('instruments.studio.status',args);
  window.dispatchEvent(new KeyboardEvent('keydown',{code:'KeyK',bubbles:true}));await pause(300);window.dispatchEvent(new KeyboardEvent('keyup',{code:'KeyK',bubbles:true}));await pause(150);
  const played=await until(async()=>{const value=await api('instruments.studio.status',args);return value.energy>before.energy&&value.midi_on>before.midi_on&&value.midi_off>before.midi_off&&value.held_notes===0&&value;},'UI piano did not complete native MIDI/audio/release',4000);
  checks.push('Actual onscreen keyboard reached the live plugin and produced nonzero audio');
  const nextPreset=item.variants.find(x=>x.id!==item.default_preset_id);
  let switched, switchMs;
  if(nextPreset){
   const start=performance.now(),select=document.querySelector('[aria-label="插件预设"]');
   select.value=nextPreset.id;select.dispatchEvent(new Event('change',{bubbles:true}));
   switched=await until(async()=>{const value=await api('instruments.studio.status',args);if(value.error)throw Error(value.error);return value.preset_id===nextPreset.id&&value.status==='ready'&&value;},'Preset did not switch within the running instance');
   switchMs=performance.now()-start;
   if(switched.pid!==before.pid||switched.session_id!==before.session_id||!switched.timings_ms.reused_host)throw Error('Preset switch restarted the plugin host');
   const heardBefore=switched.energy;
   await until(async()=>{window.dispatchEvent(new KeyboardEvent('keydown',{code:'KeyK',bubbles:true}));await pause(200);window.dispatchEvent(new KeyboardEvent('keyup',{code:'KeyK',bubbles:true}));const value=await api('instruments.studio.status',args);return value.energy>heardBefore+1e-7&&value.midi_off>switched.midi_off;},'Switched preset did not produce actual audio',20000);
   checks.push('UI preset switch retained native PID/session and the new preset produced audio');
  }
  [...document.querySelectorAll('.instrument-room-header nav button')].find(x=>x.textContent==='乐器').click();
  await until(async()=>!(await api('instruments.studio.status',args)).editor_open,'Image tab did not hide editor');
  checks.push('Switching back to instrument image hides native editor');
  await api('instruments.studio.close',args);
  window.__nativeQAResult={status:'succeeded',model,checks,embedded,original,slot,switched,switch_ms:switchMs,energy_delta:played.energy-before.energy};
 }catch(error){window.__nativeQAResult={status:'failed',checks,error:String(error.stack??error),native:await api('instruments.studio.current').catch(()=>null),surface:document.querySelector('.instrument-native-empty')?.textContent,alert:document.querySelector('.instrument-error')?.textContent};}
})();
