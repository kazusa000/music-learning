// Real SoundPanel/InstrumentBrowser against an isolated HTTP service; application
// callbacks are recorded, so browsing/confirmation cannot edit a music project.
import {createServer} from 'vite';
import {chromium} from 'playwright';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const [configFile,output]=process.argv.slice(2);
const config=JSON.parse(await readFile(configFile,'utf8'));
assert(config.port!==8765&&config.data_root.includes('instrument-models-20261020'));
const token=(await readFile(path.join(config.data_root,'session-token.local'),'ascii')).trim();
const currentResult=await fetch(`http://127.0.0.1:${config.port}/v1/commands`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify({operation:'instruments.list',arguments:{search:'LA Custom C7 - Natural'}})}).then(r=>r.json());
const current=currentResult.result.items.find(p=>p.name==='LA Custom C7 - Natural');assert(current);
const fixture=`
import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
import {SoundPanel} from '/src/SoundPanel.tsx';
import '/src/style.css';import '/src/workbench.css';
window.confirmed=[];
const tracks=[{id:'piano',name:'钢琴轨',kind:'instrument'},{id:'second',name:'第二轨',kind:'instrument'}];
const revision={id:'fixture',snapshot:{tracks,plugins:[{id:'slot',track_id:'piano',order:0,descriptor_asset_id:'descriptor',preset_id:${JSON.stringify(current.id)},preset_name:${JSON.stringify(current.name)}}]}};
function App(){const [selected,setSelected]=useState('piano');return React.createElement(SoundPanel,{revision,selected,onSelect:setSelected,enabled:true,pluginInfo:{slot:{name:'Keyscape',is_instrument:true,metadata_asset_id:'descriptor',parameters_readback:[]}},change:null,onRetry:()=>{},metadataError:'',onReloadMetadata:()=>{},onChoose:(choice,target)=>window.confirmed.push({choice,target,track:selected})});}
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
  server:{host:'127.0.0.1',port:0,proxy:{'/v1':{
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
  const model=page.locator('.model-option').filter({hasText:'LA Custom C7 Grand Piano'});
  await model.waitFor();check('current model is marked',await model.getAttribute('class').then(s=>s.includes('is-current')));await model.click();
  const first=page.locator('.variant-option:not(.is-current)').first();await first.waitFor();
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
  const mki=page.locator('.model-option').filter({hasText:'Vintage Tines MKI'}).filter({hasNotText:'MKII'});await mki.click();
  await page.getByRole('radio').first().waitFor();check('pianoteq variants share a model',await page.getByRole('radio').count()===2);
  await page.getByRole('radio').first().focus();await page.keyboard.press('ArrowDown');check('keyboard selects a variant',await page.getByRole('radio',{checked:true}).count()===1);
  await page.getByRole('radio').filter({hasText:'MKI Amped'}).click();await apply().click();
  check('new track receives the explicit choice',(await confirmed())[1].track==='second'&&(await confirmed())[1].choice.presetName==='MKI Amped');
  await page.setViewportSize({width:1000,height:800});await page.screenshot({path:path.join(output,'desktop.png')});
  check('only read APIs used',commands.every(c=>['instruments.models','instruments.list','instruments.categories'].includes(c.operation)));
  check('no browser errors',errors.length===0);
  const report={status:'succeeded',checks,commands:commands.map(c=>({operation:c.operation,arguments:c.arguments})),confirmed:await confirmed(),errors};
  await writeFile(path.join(output,'browser.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,checks:checks.length}));
}catch(error){
  await writeFile(path.join(output,'browser-failure.json'),JSON.stringify({error:String(error),errors,commands,body:(await page.locator('body').innerText()).slice(0,3000)},null,2));
  console.error(JSON.stringify({errors,commands:commands.length,body:(await page.locator('body').innerText()).slice(0,1000)}));throw error;
}finally{await browser.close();await server.close();}
