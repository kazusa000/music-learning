import {chromium} from 'playwright';
import ts from 'typescript';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';

const directory=path.resolve(process.argv[2]);
const source=await readFile(new URL('../../src/listeningCalibration.ts',import.meta.url),'utf8');
const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText.replaceAll('export function','function');
const config=JSON.parse(await readFile(path.join(directory,'signals.json'),'utf8'));
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
 const page=await browser.newPage();
 for(const item of config.signals){
  const input=(await readFile(path.join(directory,item.input))).toString('base64');
  const output=await page.evaluate(async({code,input,rate})=>{
   const bytes=Uint8Array.from(atob(input),value=>value.charCodeAt(0));
   const samples=new Float32Array(bytes.buffer),frames=samples.length/2;
   const context=new OfflineAudioContext(2,frames,rate),buffer=context.createBuffer(2,frames,rate);
   for(let ch=0;ch<2;ch++)for(let i=0;i<frames;i++)buffer.getChannelData(ch)[i]=samples[i*2+ch];
   const limiter=new Function(code+';return peakLimiter;')()(context),source=context.createBufferSource();
   source.buffer=buffer;source.connect(limiter.input);limiter.output.connect(context.destination);source.start();
   const result=await context.startRendering(),values=new Float32Array(frames*2);
   for(let ch=0;ch<2;ch++)for(let i=0;i<frames;i++)values[i*2+ch]=result.getChannelData(ch)[i];
   const raw=new Uint8Array(values.buffer);let text='';
   for(let i=0;i<raw.length;i+=8192)text+=String.fromCharCode(...raw.subarray(i,i+8192));
   return btoa(text);
  },{code,input,rate:config.sample_rate});
  await writeFile(path.join(directory,item.reference),Buffer.from(output,'base64'));
 }
}finally{await browser.close();}
