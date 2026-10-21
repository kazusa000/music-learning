import assert from 'node:assert/strict';
import {createServer} from 'vite';
import {chromium} from 'playwright';
import {fileURLToPath} from 'node:url';

// A separate local harness: no project API, plugins, model or production writes.
const server=await createServer({root:fileURLToPath(new URL('.',import.meta.url)),server:{host:'127.0.0.1',port:0},logLevel:'error'});
await server.listen();const port=server.httpServer.address().port;
const browser=await chromium.launch({channel:'msedge',headless:true,args:['--autoplay-policy=no-user-gesture-required']});
const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.route('**/v1/assets/*',async route=>{
 const rate=44100,frames=rate*4,body=Buffer.alloc(44+frames*4);body.write('RIFF');body.writeUInt32LE(body.length-8,4);body.write('WAVEfmt ',8);body.writeUInt32LE(16,16);body.writeUInt16LE(1,20);body.writeUInt16LE(2,22);body.writeUInt32LE(rate,24);body.writeUInt32LE(rate*4,28);body.writeUInt16LE(4,32);body.writeUInt16LE(16,34);body.write('data',36);body.writeUInt32LE(frames*4,40);
 for(let i=0;i<frames;i++){const value=Math.round(Math.sin(i*2*Math.PI*330/rate)*1200);body.writeInt16LE(value,44+i*4);body.writeInt16LE(value,46+i*4);}
 await route.fulfill({contentType:'audio/wav',body});
});
await page.route('**/cache-harness',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><button>test</button>'}));
try{
 await page.goto(`http://127.0.0.1:${port}/cache-harness`);
 const report=await page.evaluate(async()=>{
  const {Transport}=await import('/src/transport.ts');const t=new Transport();t.matched=false;await t.activate();
  const channels=Array.from({length:8},(_,i)=>({id:String(i),assetId:'audio'+i,bakedGain:0,bakedPan:0,values:{gain_db:-12}})),ids=channels.map(c=>'track:'+c.id),loop={enabled:false,start:0,end:4};
  t.configureChannels(channels);
  // Interleave real background preloading after decoding and before voice start.
  t.primeLive=async()=>{await t.warm(['background-source']);return [];};
  const wait=()=>new Promise(resolve=>setTimeout(resolve,180));
  await t.play(ids,.2,loop);await wait();
  const sounding=Object.values(t.meterLevels()).filter(db=>db>-60).length;
  if(sounding!==8)throw new Error('not all eight decoded stems are audible: '+sounding);
  const voices=[...t.voices.values()].map(v=>v.source);
  t.configureChannels(channels.map((c,i)=>({...c,values:{gain_db:-6,mute:i===1,solo:i===0}})));
  await t.play(ids,t.position,loop,true);await wait();
  const reuse=[...t.voices.values()].every((v,i)=>v.source===voices[i]);
  const solo=Object.values(t.meterLevels()).filter(db=>db>-60).length;
  t.configureChannels(channels);await wait();
  t.pause();const paused=t.position;await t.play(ids,paused,loop);await wait();
  const resumed=t.playing&&t.position>paused;
  // Original audio and reconstruction retain one clock even with an empty cache.
  const at=t.position;await t.play(['original'],at,loop,true);await wait();const original=t.playing&&t.position>=at;
  const back=t.position;await t.play(ids,back,loop,true);await wait();
  const rebuilt=t.voices.size>=8&&Object.values(t.meterLevels()).filter(db=>db>-60).length===8;
  t.pause();await t.play(ids,0,{enabled:true,start:0,end:.3});await new Promise(resolve=>setTimeout(resolve,500));const looped=t.playing&&t.position<.3;
  await t.close();return {sounding,reuse,solo,resumed,original,rebuilt,looped};
 });
 assert.equal(report.sounding,8);assert.equal(report.solo,1);for(const key of ['reuse','resumed','original','rebuilt','looped'])assert.equal(report[key],true,key);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({status:'succeeded',...report,jsErrors:errors.length}));
}finally{await browser.close();await server.close();}
