// Record real app footage with headless Chrome (CDP screencast) and a visible cursor.
// Usage: node scripts/demo_video/capture.mjs <plan.json> <out-dir>
// Plan steps: {goto:url} {click:{text|selector}} {move:{text|selector}} {wait:ms} {scroll:{selector?,by,ms}}
//             {eval:js} {record:true|false} {waitFor:js-expression, timeout}
// Frames are written as <out-dir>/frames/NNNNNN.jpg with <out-dir>/frames.json [{file,t}] (seconds).
import { spawn } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [planPath, outDir] = process.argv.slice(2);
const plan = JSON.parse(readFileSync(planPath, 'utf8'));
const W = plan.width ?? 1920, H = plan.height ?? 1080;
mkdirSync(join(outDir, 'frames'), { recursive: true });
const port = 9350 + Math.floor(Math.random() * 100);
const profile = mkdtempSync(join(tmpdir(), 'demo-cdp-'));
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, '--hide-scrollbars',
   '--autoplay-policy=no-user-gesture-required', `--window-size=${W},${H}`, 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let target;
for (let i = 0; i < 60 && !target; i++) {
  await sleep(200);
  try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find(t => t.type === 'page'); } catch {}
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r));
let id = 0; const pending = new Map(); const handlers = [];
ws.addEventListener('message', e => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  else if (m.method) handlers.forEach(h => h(m));
});
const send = (method, params = {}) => new Promise(r => { const n = ++id; pending.set(n, r); ws.send(JSON.stringify({ id: n, method, params })); });
const evaluate = async expr => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });

// Screencast bookkeeping.
const frames = []; let recording = false; let t0 = null; let frameNo = 0;
handlers.push(m => {
  if (m.method !== 'Page.screencastFrame') return;
  const { data, metadata, sessionId } = m.params;
  send('Page.screencastFrameAck', { sessionId });
  if (!recording) return;
  if (t0 === null) t0 = metadata.timestamp;
  const file = String(++frameNo).padStart(6, '0') + '.jpg';
  writeFileSync(join(outDir, 'frames', file), Buffer.from(data, 'base64'));
  frames.push({ file, t: +(metadata.timestamp - t0).toFixed(3) });
});
await send('Page.startScreencast', { format: 'jpeg', quality: 88, maxWidth: W, maxHeight: H, everyNthFrame: 1 });

const CURSOR = `(()=>{if(document.getElementById('demo-cursor'))return;const s=document.createElement('style');
s.textContent='#demo-cursor{position:fixed;left:0;top:0;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;background:rgba(17,17,17,.82);border:3px solid #f2c500;z-index:2147483647;pointer-events:none;transition:transform .55s cubic-bezier(.3,.1,.2,1);box-shadow:0 2px 10px #0006}'
+'.demo-ripple{position:fixed;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;border:4px solid #f2c500;z-index:2147483646;pointer-events:none;animation:demo-r .6s ease-out forwards}@keyframes demo-r{to{transform:scale(3.2);opacity:0}}';
document.head.appendChild(s);const c=document.createElement('div');c.id='demo-cursor';c.style.transform='translate(${W / 2}px,${H / 2}px)';document.body.appendChild(c);})()`;
let cursor = { x: W / 2, y: H / 2 };
async function ensureCursor() { await evaluate(CURSOR); await evaluate(`document.getElementById('demo-cursor').style.transform='translate(${cursor.x}px,${cursor.y}px)'`); }
function locator(q) {
  if (q.js) return `(${q.js})`;
  if (q.selector) return `document.querySelector(${JSON.stringify(q.selector)})`;
  return `[...document.querySelectorAll('button,a,[role=button],[role=tab],h1,h2,h3,div,span,p,img,video')].filter(e=>e.offsetParent!==null&&(e.innerText||e.alt||'').trim()&&new RegExp(${JSON.stringify(q.text)},'i').test((e.innerText||e.alt||'').trim())).sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length)[${q.nth ?? 0}]`;
}
async function center(q) {
  const v = await evaluate(`(()=>{const e=${locator(q)};if(!e)return null;e.scrollIntoView({block:'nearest'});const b=e.getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2}})()`);
  if (!v) throw new Error('Element not found: ' + JSON.stringify(q));
  return v;
}
async function moveTo(p) {
  await ensureCursor(); cursor = p;
  await evaluate(`document.getElementById('demo-cursor').style.transform='translate(${p.x}px,${p.y}px)'`);
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: p.x, y: p.y });
  await sleep(650);
}
async function click(q) {
  if (!plan.paid) {  // Unpaid plans must never click a library video (that starts a paid review in Live/Review).
    const risky = await evaluate(`(()=>{const e=${locator(q)};if(!e)return false;const v=e.closest('button,a,[role=button]');return !!(/YOUR VIDEOS/i.test(document.body.innerText)&&v&&v.querySelector('img')&&!v.closest('[role=dialog],dialog,[aria-modal=true]')&&!/AI analysis/.test(v.innerText))})()`);
    if (risky) throw new Error('Refusing to click a library video in an unpaid plan: ' + JSON.stringify(q));
  }
  const p = await center(q); await moveTo(p);
  await evaluate(`(()=>{const r=document.createElement('div');r.className='demo-ripple';r.style.left='${p.x}px';r.style.top='${p.y}px';document.body.appendChild(r);setTimeout(()=>r.remove(),700)})()`);
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: p.x, y: p.y, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: p.x, y: p.y, button: 'left', clickCount: 1 });
  await sleep(300);
}
async function scroll(s) {
  const steps = Math.max(1, Math.round((s.ms ?? 2000) / 40));
  const el = s.selector ? `document.querySelector(${JSON.stringify(s.selector)})` : null;
  for (let i = 0; i < steps; i++) {
    if (el) await evaluate(`(${el}||document.scrollingElement).scrollBy(0,${s.by / steps})`);
    else await send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: cursor.x, y: cursor.y, deltaX: 0, deltaY: s.by / steps });
    await sleep(40);
  }
}
const log = [];
for (const step of plan.steps) {
  if (step.label) log.push({ label: step.label, wall: Date.now() / 1000 });
  if (step.goto) { await send('Page.navigate', { url: step.goto }); await sleep(step.after ?? 3000); await ensureCursor(); }
  if (step.record !== undefined) recording = step.record;
  if (step.move) await moveTo(await center(step.move));
  if (step.moveXY) await moveTo({ x: step.moveXY[0], y: step.moveXY[1] });
  if (step.click) await click(step.click);
  if (step.scroll) await scroll(step.scroll);
  if (step.eval) console.log(JSON.stringify(await evaluate(step.eval)).slice(0, 2000));
  if (step.waitFor) {
    const end = Date.now() + (step.timeout ?? 60000);
    while (Date.now() < end && !(await evaluate(step.waitFor))) await sleep(500);
    console.log('waitFor', step.label ?? '', Date.now() < end ? 'met' : 'timed out');
  }
  if (step.wait) await sleep(step.wait);
  if (step.shot) { const r = await send('Page.captureScreenshot', { format: 'png' }); writeFileSync(join(outDir, step.shot), Buffer.from(r.result.data, 'base64')); }
}
writeFileSync(join(outDir, 'frames.json'), JSON.stringify(frames));
writeFileSync(join(outDir, 'labels.json'), JSON.stringify({ t0, labels: log }));
console.log('frames', frames.length, 'duration', frames.at(-1)?.t ?? 0);
await send('Page.stopScreencast'); ws.close(); chrome.kill();
