// Uses a running local API/worker; files and results remain local.
import {chromium} from 'playwright';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';

const [tokenFile, audioFile, output] = process.argv.slice(2);
if (!output) throw new Error('Arguments: local token file, local full-song audio, new local output directory');
for (const filename of [tokenFile, audioFile, output]) {
  if (filename.startsWith('\\\\') || filename.startsWith('//')) throw new Error('Local files only');
}
await mkdir(output, {recursive: false});
const token = (await readFile(tokenFile, 'ascii')).trim();
const bytes = await readFile(audioFile);
const hash = crypto.createHash('sha256').update(bytes).digest('hex');
const browser = await chromium.launch({headless: true, args: ['--autoplay-policy=no-user-gesture-required']});
const errors = [];
try {
  const page = await browser.newPage({viewport: {width: 1380, height: 1000}});
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto('http://127.0.0.1:8765/#token=' + token);
  await page.getByText('● 本机已连接', {exact: true}).waitFor();
  assert.equal(new URL(page.url()).hash, '');
  const listBefore = await page.getByRole('navigation', {name: '工程列表'}).getByRole('button').count();
  await page.getByLabel('选择音乐', {exact: true}).setInputFiles({name: "届かない恋 '13 · 主页验证.flac", mimeType: 'audio/flac', buffer: bytes});
  await page.locator('.task[data-status="succeeded"]').waitFor({timeout: 120000});
  assert.equal(await page.getByRole('navigation', {name: '工程列表'}).getByRole('button').count(), listBefore + 1);
  const duration = await page.locator('audio').evaluate(audio => new Promise(resolve => {
    if (Number.isFinite(audio.duration)) resolve(audio.duration);
    else audio.addEventListener('loadedmetadata', () => resolve(audio.duration), {once: true});
  }));
  assert.ok(Math.abs(duration - 305) < .001);
  await page.locator('audio').evaluate(async audio => {audio.currentTime = 150; await audio.play();});
  await page.waitForTimeout(350);
  const position = await page.locator('audio').evaluate(audio => {audio.pause(); return audio.currentTime;});
  assert.ok(position > 150);
  const selected = await page.evaluate(() => localStorage.getItem('selected-project'));
  await page.reload();
  await page.locator('.task[data-status="succeeded"]').waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem('selected-project')), selected);
  assert.ok((await page.locator('.original').innerText()).includes('整曲分析尚未开始'));
  await page.screenshot({path: path.join(output, 'homepage.png'), fullPage: true});
  assert.deepEqual(errors, []);
  const report = {project_id: selected, source_sha256: hash, duration_seconds: duration, seek_and_play_seconds: position,
    persisted_after_reload: true, page_errors: errors, nas_accessed: false};
  await writeFile(path.join(output, 'summary.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {await browser.close();}
