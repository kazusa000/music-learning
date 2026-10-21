import {chromium} from 'playwright';
import {readFile,writeFile,access} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=path.resolve(process.argv[2]),config=JSON.parse(await readFile(path.join(root,'app.local.json'),'utf8'));
assert.equal(config.port,8791);assert(config.data_root.startsWith(root));
const fixture=JSON.parse(await readFile(path.join(root,'fixture.json'),'utf8'));
const token=(await readFile(path.join(config.data_root,'session-token.local'),'ascii')).trim(),base='http://127.0.0.1:'+config.port;
async function call(operation,args={}){const response=await fetch(base+'/v1/commands',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({operation,arguments:args})});const data=await response.json();assert(data.ok,JSON.stringify(data.error));return data.result;}
async function exists(file){try{await access(file);return true;}catch{return false;}}
const object=id=>path.join(config.data_root,'objects',id.slice(0,2),id);
const browser=await chromium.launch({channel:'msedge',headless:true});
const context=await browser.newContext({viewport:{width:1536,height:864},deviceScaleFactor:2.5}),page=await context.newPage();
const errors=[];page.on('pageerror',error=>errors.push(error.message));
const [first,second,last]=fixture.projects;
const preserved=await call('revisions.get',{project_id:second.id});
try{
 await page.goto(base+'/#token='+token);
 await page.getByLabel('显示归档工程').check();
 await page.getByRole('button',{name:'删除工程 删除演示',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'彻底删除工程？'});await dialog.waitFor();
 await page.screenshot({path:path.join(root,'confirm-4k.png')});
 await dialog.getByRole('button',{name:'取消',exact:true}).click();
 assert(await exists(object(fixture.private)));
 assert((await call('projects.list',{include_archived:true})).some(p=>p.id===first.id));
 await page.getByRole('button',{name:'删除工程 删除演示',exact:true}).click();
 await dialog.getByRole('button',{name:'彻底删除',exact:true}).click();
 await page.getByRole('status').filter({hasText:'已彻底删除「删除演示」'}).waitFor({timeout:20000});
 assert(!(await exists(object(fixture.private))));assert(await exists(object(fixture.shared)));
 assert.deepEqual(await call('revisions.get',{project_id:second.id}),preserved);
 await page.reload();await page.getByRole('button',{name:'删除工程 保留工程',exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:'删除工程 删除演示',exact:true}).count(),0);
 await page.getByRole('button',{name:'保留工程',exact:false}).filter({has:page.locator('strong')}).click();
 await page.locator('.library-actions').getByRole('button',{name:'删除工程',exact:true}).click();
 await dialog.getByRole('button',{name:'彻底删除',exact:true}).click();
 await page.getByRole('status').filter({hasText:'已彻底删除「保留工程」'}).waitFor({timeout:20000});
 assert(!(await exists(object(fixture.shared))));
 await page.getByRole('button',{name:'删除工程 最后工程',exact:true}).click();
 await dialog.getByRole('button',{name:'彻底删除',exact:true}).click();
 await page.getByRole('status').filter({hasText:'已彻底删除「最后工程」'}).waitFor({timeout:20000});
 assert.deepEqual(await call('projects.list',{include_archived:true}),[]);
 await page.reload();await page.getByText('还没有工程。选一首完整音乐，保存在本机。',{exact:true}).waitFor();
 assert.equal(await page.locator('.project-row').count(),0);
 assert.equal(await page.evaluate(()=>localStorage.getItem('selected-project')),null);
 assert(!(await exists(path.join(config.data_root,'backups'))));
 const deletions=await call('projects.deletions.list');assert.equal(deletions.length,3);assert(deletions.every(d=>d.status==='completed'));
 assert.equal(errors.length,0,errors.join('\n'));
 await page.screenshot({path:path.join(root,'empty-4k.png')});
 const result={status:'passed',archiveDelete:true,cancelPreserves:true,sharedAssetRetainedUntilLastOwner:true,
   retainedProjectUnchanged:true,refreshKeepsDeleted:true,lastSelectionCleared:true,noBackup:true,errors,deletions};
 await writeFile(path.join(root,'browser.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser.close();}
