#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import CDP from '/home/paulkinlan/.npm/_npx/5883e6c84caa01ab/node_modules/chrome-remote-interface/index.js';

const RESULTS = '/home/paulkinlan/state-of-the-web/results/gpt';
const CHROME = process.env.CHROME_BIN || '/usr/bin/google-chrome-stable';
const BATCH = process.argv[2] || 'memory-001';
const LIST = process.argv[3];
const LIMIT = Number(process.argv[4] || '0');

function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }
function safe(site){ return site.toLowerCase().replace(/[^a-z0-9.-]+/g,'-').replace(/^-|-$/g,''); }
function readJson(p){ return JSON.parse(readFileSync(p,'utf8')); }
function writeJson(p,o){ writeFileSync(p, JSON.stringify(o,null,2)+'\n'); }

async function launchChrome(){
  const userDataDir = mkdtempSync(join(tmpdir(), 'sotw-memory-'));
  const proc = spawn(CHROME, [
    '--headless=new', '--remote-debugging-port=0', '--no-sandbox', `--user-data-dir=${userDataDir}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--disable-dev-shm-usage',
    '--hide-scrollbars=false', '--js-flags=--expose-gc', '--enable-precise-memory-info'
  ], {stdio:['ignore','ignore','pipe']});
  const port = await new Promise((resolve,reject)=>{
    let buf=''; const t=setTimeout(()=>reject(new Error('Timed out waiting for Chrome')),20000);
    proc.stderr.on('data', chunk=>{ buf += chunk.toString(); const m=buf.match(/DevTools listening on ws:\/\/[^:]+:(\d+)\//); if(m){ clearTimeout(t); resolve(Number(m[1])); }});
    proc.on('exit', code=>{ clearTimeout(t); reject(new Error(`Chrome exited early ${code}`)); });
  });
  return {proc, port, userDataDir, close(){ try{proc.kill('SIGTERM')}catch{}; try{rmSync(userDataDir,{recursive:true,force:true})}catch{}; }};
}

async function newPage(port){
  const browser = await CDP({port});
  const {targetId} = await browser.Target.createTarget({url:'about:blank'});
  await browser.close();
  const client = await CDP({port, target:targetId});
  await Promise.all([client.Page.enable(), client.Runtime.enable(), client.Network.enable(), client.HeapProfiler.enable()]);
  await client.Emulation.setDeviceMetricsOverride({width:1365,height:900,deviceScaleFactor:1,mobile:false,screenWidth:1365,screenHeight:900});
  return client;
}

async function navigate(client,url){
  await client.Page.navigate({url});
  await new Promise(resolve=>{ const t=setTimeout(resolve,12000); client.Page.loadEventFired(()=>{clearTimeout(t); resolve();}); });
  await sleep(2500);
}

async function evalJs(client, expression, awaitPromise=false){
  return client.Runtime.evaluate({expression, awaitPromise, returnByValue:true, userGesture:true, timeout:15000}).then(r=>r.result?.value);
}

async function forceGc(client){
  try { await evalJs(client, 'typeof gc === "function" ? (gc(), true) : false'); } catch {}
  try { await client.HeapProfiler.collectGarbage(); } catch {}
  await sleep(500);
}

async function heapUsage(client){
  try { return await client.Runtime.getHeapUsage(); }
  catch {
    const metrics = await client.Performance?.getMetrics?.().catch(()=>null);
    const used = metrics?.metrics?.find?.(m=>m.name==='JSHeapUsedSize')?.value;
    return {usedSize: used || null, totalSize: null};
  }
}

async function heapSnapshotSize(client, outRaw){
  const chunks=[];
  client.HeapProfiler.addHeapSnapshotChunk(({chunk})=>chunks.push(chunk));
  await client.HeapProfiler.takeHeapSnapshot({reportProgress:false});
  const raw=chunks.join('');
  if(outRaw) writeFileSync(outRaw, raw);
  const snap=JSON.parse(raw);
  const nf=snap.snapshot.meta.node_fields;
  const types=snap.snapshot.meta.node_types[nf.indexOf('type')];
  const fieldCount=nf.length;
  const selfIdx=nf.indexOf('self_size');
  const typeIdx=nf.indexOf('type');
  let totalSelfSizeBytes=0, nodeCount=0, detachedCount=0;
  for(let i=0;i<snap.nodes.length;i+=fieldCount){
    nodeCount++;
    totalSelfSizeBytes += snap.nodes[i+selfIdx] || 0;
    const type=types[snap.nodes[i+typeIdx]];
    if(String(type).toLowerCase().includes('detached')) detachedCount++;
  }
  return {nodeCount,totalSelfSizeBytes,detachedCount};
}

const interaction = `async () => {
  for (let i=0;i<3;i++) {
    window.scrollTo({top: document.body.scrollHeight, behavior: 'instant'});
    await new Promise(r=>setTimeout(r,700));
    window.scrollTo({top: 0, behavior: 'instant'});
    await new Promise(r=>setTimeout(r,400));
    const links = [...document.querySelectorAll('nav a[href], header a[href], main a[href]')]
      .filter(a => a.offsetParent !== null && a.href && !a.href.startsWith('javascript:') && !a.href.startsWith('mailto:'));
    const same = links.find(a => { try { return new URL(a.href).origin === location.origin && !a.href.includes('#'); } catch { return false; } });
    if (same && i === 0) { same.click(); await new Promise(r=>setTimeout(r,2500)); history.length && history.back(); await new Promise(r=>setTimeout(r,2500)); }
  }
  await new Promise(r=>setTimeout(r,10000));
  return true;
}`;

function memoryFinding(site, audit){
  return {
    id:`${safe(site)}-memory-01`, principleId:'be-memory-efficient', checkId:'heap-growth-after-gc', severity:audit.deltaAfterGcBytes>25000000?'high':'medium',
    title:`Heap retained after interaction and GC (+${(audit.deltaAfterGcBytes/1000000).toFixed(1)} MB)`,
    evidence:`Same-session memory pass: baseline ${(audit.baseline.totalSelfSizeBytes ?? audit.baseline.usedSize)} bytes; final after interaction cycles + forced GC ${(audit.finalAfterGc.totalSelfSizeBytes ?? audit.finalAfterGc.usedSize)} bytes; delta ${audit.deltaAfterGcBytes} bytes; monotonic=${audit.monotonicGrowth}.`,
    suggestedFix:'Profile repeated interactions, remove retained detached DOM/listeners/timers, and ensure route/component cleanup releases references.'
  };
}

function updateSiteJson(obj, audit){
  obj.evidence ||= {};
  obj.evidence.memoryAudit = audit;
  obj.evidence.heapSize = (audit.baseline.totalSelfSizeBytes ?? audit.baseline.usedSize);
  obj.evidence.heapSizeAfterInteraction = (audit.finalAfterGc.totalSelfSizeBytes ?? audit.finalAfterGc.usedSize);
  obj.evidence.heapDeltaAfterInteraction = audit.deltaAfterGcBytes;
  const p = (obj.principles||[]).find(x=>x.id==='be-memory-efficient');
  if(p){
    const issue = audit.deltaAfterGcBytes > 5_000_000 && (audit.monotonicGrowth || audit.deltaAfterGcBytes > 25_000_000);
    if(issue){
      const f=memoryFinding(obj.site, audit);
      p.status='issues'; p.confidence=audit.monotonicGrowth?'high':'medium';
      p.summary=`Same-session memory pass retained +${(audit.deltaAfterGcBytes/1000000).toFixed(1)} MB after interaction cycles and forced GC.`;
      p.findings=[f];
    } else {
      p.status='pass'; p.confidence='medium'; p.findings=[];
      p.summary=`Same-session memory pass retained ${(audit.deltaAfterGcBytes/1000000).toFixed(1)} MB after interaction cycles and forced GC; no monotonic leak signal above threshold.`;
    }
  }
  obj._caveats ||= [];
  obj._caveats = obj._caveats.filter(c=>!String(c).includes('Single-load heap') && !String(c).includes('coarse signal'));
  obj._caveats.push('Memory was rechecked with same-session baseline, 3 interaction cycles, forced GC, and final heap snapshot; still a homepage smoke test, not full memlab trace attribution.');
}

async function auditSite(row){
  const [rank, site, url] = row;
  const dir = `${RESULTS}/${BATCH}-evidence/${safe(site)}`;
  await import('node:fs').then(fs=>fs.mkdirSync(dir,{recursive:true}));
  const chrome = await launchChrome();
  let client;
  try {
    client = await newPage(chrome.port);
    await navigate(client,url);
    await forceGc(client);
    const baselineUsage = await heapUsage(client);
    const samples=[];
    for(let i=0;i<3;i++){
      await evalJs(client, `(${interaction})()`, true).catch(()=>false);
      await forceGc(client);
      samples.push(await heapUsage(client));
    }
    const finalUsage=samples[samples.length-1];
    const finalSnapshot = await heapSnapshotSize(client, null);
    const deltaAfterGcBytes=(finalUsage.usedSize ?? 0) - (baselineUsage.usedSize ?? 0);
    const sizes=[baselineUsage.usedSize, ...samples.map(s=>s.usedSize)].filter(v=>typeof v==='number');
    const monotonicGrowth=sizes.length>1 && sizes.every((v,i)=>i===0 || v>=sizes[i-1]);
    const audit={
      method:'same-session baseline heap usage -> 3 scroll/nav/wait cycles -> forced Runtime.gc/HeapProfiler.collectGarbage after each cycle -> final heap snapshot summary',
      auditedAt:new Date().toISOString(),
      url, rank:Number(rank)||null,
      baseline:{usedSize:baselineUsage.usedSize,totalSize:baselineUsage.totalSize},
      samples:samples.map(s=>({usedSize:s.usedSize,totalSize:s.totalSize})),
      finalAfterGc:{usedSize:finalUsage.usedSize,totalSize:finalUsage.totalSize,totalSelfSizeBytes:finalSnapshot.totalSelfSizeBytes,nodeCount:finalSnapshot.nodeCount,detachedCount:finalSnapshot.detachedCount},
      deltaAfterGcBytes, monotonicGrowth,
      thresholdBytes:5_000_000,
      artifactsDir:dir
    };
    writeJson(`${dir}/memory-audit.json`, audit);
    const sitePath=`${RESULTS}/${safe(site)}.json`;
    if(existsSync(sitePath)){
      const obj=readJson(sitePath); updateSiteJson(obj,audit); writeJson(sitePath,obj);
    }
    return {site, ok:true, audit};
  } finally {
    try { if(client) await client.close(); } catch {}
    chrome.close();
  }
}

let rows=[];
if(LIST){
  rows=readFileSync(LIST,'utf8').split(/\n/).filter(Boolean).map(l=>l.split('\t')).map(p=>[p[0],p[1],p[2]]);
} else {
  rows=process.argv.slice(3).map(site=>[null,site,`https://${site}/`]);
}
if(LIMIT>0) rows=rows.slice(0,LIMIT);
const results=[];
for(const row of rows){
  const site=row[1];
  const sitePath=`${RESULTS}/${safe(site)}.json`;
  if(existsSync(sitePath)){
    try {
      const existing=readJson(sitePath);
      if(existing.evidence?.memoryAudit){
        console.log(`[memory] skip existing ${site}`);
        results.push({site, ok:true, skipped:true, audit:existing.evidence.memoryAudit});
        writeJson(`${RESULTS}/${BATCH}.partial.json`, results);
        continue;
      }
    } catch {}
  }
  console.log(`[memory] ${site}`);
  try { results.push(await auditSite(row)); }
  catch(e){ console.error(`[memory] ERROR ${site}:`, e.message); results.push({site, ok:false, error:e.message}); }
  writeJson(`${RESULTS}/${BATCH}.partial.json`, results);
}
writeJson(`${RESULTS}/${BATCH}.json`, results);
console.log(`DONE ${BATCH}: ${results.length}`);
