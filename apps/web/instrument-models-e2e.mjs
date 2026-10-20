// Real SoundPanel/InstrumentBrowser against an isolated HTTP service; application
// callbacks are recorded, so browsing/confirmation cannot edit a music project.
import {createServer} from 'vite';
import {chromium} from 'playwright';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const [configFile,output]=process.argv.slice(2);
const config=JSON.parse(await readFile(configFile,'utf8'));
assert(config.port!==8765&&/instrument-(models-20261020|previews-20261021)/.test(config.data_root));
const token=(await readFile(path.join(config.data_root,'session-token.local'),'ascii')).trim();
const currentResult=await fetch(`http://127.0.0.1:${config.port}/v1/commands`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify({operation:'instruments.list',arguments:{search:'LA Custom C7 - Natural'}})}).then(r=>r.json());
const current=currentResult.result.items.find(p=>p.name==='LA Custom C7 - Natural');assert(current);
const audition=process.env.INSTRUMENT_PREVIEW_ACCEPTANCE==='1';
const fixture=`
import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
import {SoundPanel} from '/src/SoundPanel.tsx';
import {Transport} from '/src/transport.ts';
import '/src/style.css';import '/src/workbench.css';
window.confirmed=[];
window.__audioStarts=[];
const NativeContext=window.AudioContext;
window.AudioContext=class extends NativeContext{createBufferSource(){const source=super.createBufferSource(),start=source.start.bind(source);source.start=(...args)=>{window.__audioStarts.push({peak:source.buffer?source.buffer.getChannelData(0).reduce((a,b)=>Math.max(a,Math.abs(b)),0):0,at:this.currentTime});return start(...args);};return source;}};
window.startMonitoring=async()=>{const t=new Transport();t.context=new AudioContext();await t.context.resume();t.attachMonitoring();t.output=t.context.createGain();t.output.gain.value=1;t.output.connect(t.context.destination);t.playing=true;t.channels.set('monitor',{gainDb:-6,pan:.2,mute:false,solo:false});const source=t.context.createBufferSource();source.buffer=t.context.createBuffer(1,44100,44100);const samples=source.buffer.getChannelData(0);for(let i=0;i<samples.length;i++)samples[i]=Math.sin(i*.1)*.02;source.loop=true;source.connect(t.output);source.start();window.__mainTransport=t;};
const tracks=[{id:'piano',name:'钢琴轨',kind:'instrument'},{id:'second',name:'第二轨',kind:'instrument'}];
const revision={id:'fixture',snapshot:{tracks,plugins:[{id:'slot',track_id:'piano',order:0,descriptor_asset_id:'descriptor',preset_id:${JSON.stringify(current.id)},preset_name:${JSON.stringify(current.name)}}]}};
function App(){const [selected,setSelected]=useState('piano');return React.createElement(React.Fragment,null,${audition?"React.createElement('button',{id:'test-monitor',style:{position:'fixed',top:0,right:0},onClick:()=>window.startMonitoring()},'测试监听'),":""}React.createElement(SoundPanel,{revision,selected,onSelect:setSelected,enabled:true,pluginInfo:{slot:{name:'Keyscape',is_instrument:true,metadata_asset_id:'descriptor',parameters_readback:[]}},change:null,onRetry:()=>{},metadataError:'',onReloadMetadata:()=>{},onChoose:(choice,target)=>window.confirmed.push({choice,target,track:selected})}));}
createRoot(document.getElementById('root')).render(React.createElement(App));
`;
const server=await createServer({root:process.cwd(),configFile:false,logLevel:'error',
  plugins:[{name:'instrument-models-fixture',
    resolveId(id){if(id==='virtual:models-fixture')return '\0virtual:models-fixture';},
    load(id){if(id==='\0virtual:models-fixture')return fixture;},
    configureServer(vite){vite.middlewares.use('/__models',async(req,res)=>{
      res.setHeader('Content-Type','text/html');res.end(await vite.transformIndexHtml('/__models',`<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;padding:12px}#root{width:min(420px,100%);height:720px}#root>.sound-panel{height:100%;display:flex;flex-direction:column}.instrument-browser{flex:1;min-height:0}</style></head><body><div id="root"></div><script type="module" src="/@id/__x00__virtual:models-fixture"></script></body></html>`));
    });}
  }],
  server:{host:'127.0.0.1',port:0,hmr:false,watch:null,proxy:{'/v1':{
    target:`http://127.0.0.1:${config.port}`,changeOrigin:true,
    configure(proxy){proxy.on('proxyReq',req=>{req.setHeader('Authorization','Bearer '+token);req.removeHeader('Origin');});}
  }}}
});
await server.listen();await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const page=await browser.newPage({viewport:{width:1000,height:800}}),errors=[],commands=[],checks=[];
page.on('pageerror',e=>{errors.push(String(e));console.error('Browser:',String(e));});
page.on('request',r=>{if(r.url().endsWith('/v1/commands'))commands.push(r.postDataJSON());});
const check=(name,value)=>{assert(value,name);checks.push(name);};
const confirmed=()=>page.evaluate(()=>window.confirmed);
const apply=()=>page.getByRole('button',{name:'确认应用',exact:true});
page.setDefaultTimeout(10000);
try{
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/__models`);
  const model=page.locator('.model-option[title="LA Custom C7 Grand Piano"]');
  await model.waitFor();check('current model is marked',await model.getAttribute('class').then(s=>s.includes('is-current')));
  check('model shows Chinese and parenthesized original',(await model.locator('strong').innerText()).includes('（LA Custom C7 Grand Piano）'));
  check('product retains its factory name',(await model.locator('small').innerText()).includes('Keyscape')&&!(await model.locator('small').innerText()).includes('珍藏'));
  await model.click();
  const first=page.locator('.variant-option:not(.is-current)').first();await first.waitFor();
  check('variant shows Chinese and exact parenthesized original',(await first.innerText()).includes('（'+await first.getAttribute('title')+'）'));
  check('model navigation performs no application',(await confirmed()).length===0);
  check('confirmation requires a variant',await apply().isDisabled());
  await page.locator('.variant-option.is-current').click();check('current preset cannot be applied again',await apply().isDisabled());
  await first.click();const chosenId=await first.getAttribute('data-preset-id');
  check('variant selection performs no application',(await confirmed()).length===0);
  check('selected variant is marked',await first.getAttribute('aria-checked')==='true');
  check('explicit target visible',(await page.locator('.preset-confirm').innerText()).includes('钢琴轨'));
  await apply().click();
  check('confirmation sends exact preset once',(await confirmed()).length===1&&(await confirmed())[0].choice.presetId===chosenId);
  check('existing instrument slot is passed to application',(await confirmed())[0].target==='slot');
  check('confirmation clears pending application',await apply().isDisabled());
  await page.getByRole('radio').first().click();
  await page.getByLabel('音色与效果的音轨',{exact:true}).selectOption('second');
  await page.locator('.model-option').first().waitFor();
  check('changing track clears the pending variant',await page.getByRole('radiogroup').count()===0);
  await model.click();await page.getByRole('radio').first().waitFor();
  const next=page.getByRole('button',{name:'下一页',exact:true});await next.click();
  await page.getByRole('radio').first().waitFor();check('variants are paginated',await page.locator('.instrument-pages').innerText().then(t=>t.includes('2 /')));
  await page.getByRole('button',{name:'返回型号列表'}).click();await model.waitFor();
  check('back cancels without applying',(await confirmed()).length===1);
  // A failed read stays on the selected model and can be retried in place.
  let fail=true;await page.route('**/v1/commands',async route=>{const body=route.request().postDataJSON();
    if(body.operation==='instruments.list'&&fail){fail=false;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({ok:false,error:{message:'fixture read failure'}})});}else await route.continue();});
  await model.click();await page.getByRole('alert').filter({hasText:'fixture read failure'}).waitFor();
  await page.getByRole('button',{name:'重试读取',exact:true}).click();await page.getByRole('radio').first().waitFor();
  check('failed variant read retries without applying',(await confirmed()).length===1);
  // Confirm remains on screen at narrow widths, and category change cancels selection.
  await page.setViewportSize({width:360,height:800});await page.getByRole('radio').first().click();
  const box=await apply().boundingBox();check('narrow confirmation stays visible',box&&box.y>=0&&box.y+box.height<=800);
  check('no horizontal page overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(output,'narrow.png')});
  await page.getByLabel('选择乐器',{exact:true}).selectOption('electric-piano');await page.locator('.model-option').first().waitFor();
  check('changing category clears selection',await page.getByRole('radiogroup').count()===0&&(await confirmed()).length===1);
  const mki=page.locator('.model-option[title="Vintage Tines MKI"]');await mki.click();
  await page.getByRole('radio').first().waitFor();check('pianoteq variants share a model',await page.getByRole('radio').count()===2);
  await page.getByRole('radio').first().focus();await page.keyboard.press('ArrowDown');check('keyboard selects a variant',await page.getByRole('radio',{checked:true}).count()===1);
  await page.getByRole('radio').filter({hasText:'音箱效果'}).click();await apply().click();
  check('new track receives the explicit choice',(await confirmed())[1].track==='second'&&(await confirmed())[1].choice.presetName.includes('音箱效果'));
  check('Pianoteq model and variant visibly retain original names',(await page.locator('.instrument-model-heading').innerText()).includes('一代簧片电钢琴（Vintage Tines MKI）')&&(await page.locator('[title="MKI Amped"]').innerText()).includes('音箱效果（MKI Amped）'));
  if(audition){
    await page.getByRole('button',{name:'测试监听',exact:true}).click();
    await page.waitForFunction(()=>window.__mainTransport?.playing);
    const monitorBefore=await page.evaluate(()=>({time:window.__mainTransport.context.currentTime,channels:JSON.stringify([...window.__mainTransport.channels])}));
    const baseline=(await confirmed()).length;
    const basic=page.locator('.preset-row').filter({has:page.getByRole('radio').filter({hasText:'基础'})}).locator('.preset-listen');
    await basic.click();await page.locator('.preset-listening-status.is-playing').waitFor();
    check('preview starts actual nonzero decoded PCM',await page.evaluate(()=>window.__audioStarts.length>=2&&window.__audioStarts.at(-1).peak>.001));
    check('preview ducks only the monitor output',await page.evaluate(()=>Math.abs(window.__mainTransport.output.gain.value-.16)<1e-6));
    check('preview never selects or applies a variant',(await confirmed()).length===baseline&&await page.getByRole('radio',{checked:true}).count()===0);
    await page.getByLabel('音色试听音量',{exact:true}).press('Home');
    await page.getByLabel('音色试听音量',{exact:true}).press('ArrowRight');
    check('preview volume has its own control',await page.getByLabel('音色试听音量',{exact:true}).inputValue()==='0.01');
    await page.getByRole('button',{name:'停止试听',exact:true}).click();
    check('stopping restores monitor gain',await page.evaluate(()=>window.__mainTransport.output.gain.value===1));
    check('music clock and channel values preserved',await page.evaluate(before=>window.__mainTransport.context.currentTime>before.time&&JSON.stringify([...window.__mainTransport.channels])===before.channels&&window.__mainTransport.playing,monitorBefore));
    // Fail only the first preview request; retry uses the genuine cached audio.
    let failPreview=true;await page.route('**/v1/commands',async route=>{if(route.request().postDataJSON().operation==='instruments.preview.prepare'&&failPreview){failPreview=false;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({ok:false,error:{message:'试听测试失败'}})});}else await route.continue();});
    await basic.click();await page.locator('.preset-listening-status.is-failed').waitFor();
    await page.getByRole('button',{name:'重试试听',exact:true}).click();await page.locator('.preset-listening-status.is-playing').waitFor();
    check('failed preview retries in place',(await confirmed()).length===baseline);
    await page.waitForFunction(()=>!document.querySelector('.preset-listening-status'),{},{timeout:12000});
    check('natural end restores monitor',await page.evaluate(()=>window.__mainTransport.output.gain.value===1));
    await page.getByRole('button',{name:'返回型号列表'}).click();await mki.waitFor();
    const wurl=page.locator('.preset-row').filter({has:page.locator('.model-option[title="Wurlitzer 200A"]')}).locator('.preset-listen');
    const mkiListen=page.locator('.preset-row').filter({has:mki}).locator('.preset-listen');
    let delayNext=true;await page.route('**/v1/commands',async route=>{if(route.request().postDataJSON().operation==='instruments.preview.prepare'&&delayNext){delayNext=false;await new Promise(r=>setTimeout(r,500));}await route.continue();});
    const startsBefore=await page.evaluate(()=>window.__audioStarts.length);
    await wurl.click();await mkiListen.click();await page.locator('.preset-listening-status.is-playing').waitFor();
    await page.waitForTimeout(700);
    check('model speaker does not navigate',await page.getByRole('radiogroup').count()===0);
    check('rapid model preview switches keep one active sound',await page.locator('.preset-listen.is-active').count()===1);
    check('late preview response never starts an obsolete sound',await page.evaluate(n=>window.__audioStarts.length===n+1,startsBefore));
    await page.getByRole('button',{name:'停止试听',exact:true}).click();
    delayNext=true;const stoppedAt=await page.evaluate(()=>window.__audioStarts.length);await mkiListen.click();
    await page.getByRole('button',{name:'停止试听',exact:true}).click();await page.waitForTimeout(700);
    check('stopping a pending preview prevents late playback',await page.evaluate(n=>window.__audioStarts.length===n&&window.__mainTransport.output.gain.value===1,stoppedAt));
    check('all previews preserve confirmation count',(await confirmed()).length===baseline);
    await page.evaluate(()=>window.__mainTransport.close());
  }
  await page.setViewportSize({width:1000,height:800});await page.screenshot({path:path.join(output,'desktop.png')});
  check('only library APIs used',commands.every(c=>['instruments.models','instruments.list','instruments.categories','instruments.labels',...(audition?['instruments.preview.prepare','instruments.preview.get']:[])].includes(c.operation)));
  check('no browser errors',errors.length===0);
  const report={status:'succeeded',checks,commands:commands.map(c=>({operation:c.operation,arguments:c.arguments})),confirmed:await confirmed(),errors};
  await writeFile(path.join(output,'browser.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,checks:checks.length}));
}catch(error){
  await writeFile(path.join(output,'browser-failure-'+Date.now()+'.json'),JSON.stringify({error:String(error),errors,commands,body:(await page.locator('body').innerText()).slice(0,3000)},null,2));
  console.error(JSON.stringify({errors,commands:commands.length,body:(await page.locator('body').innerText()).slice(0,1000)}));throw error;
}finally{await browser.close();await server.close();}
