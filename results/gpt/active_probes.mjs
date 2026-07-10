#!/usr/bin/env node
// Active probes for the 2 remaining "pending" principles:
//   - implement-natural-interactions (feature detection: view transitions, scroll-driven animations, reduced-motion respect)
//   - support-core-task-success (interaction: find + click primary CTA, check if flow progresses)
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import CDP from '/home/paulkinlan/.npm/_npx/5883e6c84caa01ab/node_modules/chrome-remote-interface/index.js';

const RESULTS = '/home/paulkinlan/state-of-the-web/results/gpt';
const CHROME = process.env.CHROME_BIN || '/usr/bin/google-chrome-stable';
const BATCH = process.argv[2] || 'active-probes';
const LIST = process.argv[3];
const LIMIT = Number(process.argv[4] || '0');

function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }
function safe(site){ return site.toLowerCase().replace(/[^a-z0-9.-]+/g,'-').replace(/^-|-$/g,''); }
function readJson(p){ return JSON.parse(readFileSync(p,'utf8')); }
function writeJson(p,o){ writeFileSync(p, JSON.stringify(o,null,2)+'\n'); }

async function launchChrome(){
  const userDataDir = mkdtempSync(join(tmpdir(),'sotw-active-'));
  const proc = spawn(CHROME, [
    '--headless=new','--remote-debugging-port=0','--no-sandbox',`--user-data-dir=${userDataDir}`,
    '--no-first-run','--no-default-browser-check','--disable-gpu','--disable-dev-shm-usage',
    '--js-flags=--expose-gc'
  ], {stdio:['ignore','ignore','pipe']});
  const port = await new Promise((resolve,reject)=>{
    let buf=''; const t=setTimeout(()=>reject(new Error('Chrome timeout')),20000);
    proc.stderr.on('data', chunk=>{ buf+=chunk.toString(); const m=buf.match(/DevTools listening on ws:\/\/[^:]+:(\d+)\//); if(m){clearTimeout(t);resolve(Number(m[1]));}});
    proc.on('exit', code=>{ clearTimeout(t); reject(new Error(`Chrome exited ${code}`)); });
  });
  return {proc,port,userDataDir,close(){try{proc.kill('SIGTERM')}catch{};try{rmSync(userDataDir,{recursive:true,force:true})}catch{};}};
}

async function newPage(port){
  const browser = await CDP({port});
  const {targetId} = await browser.Target.createTarget({url:'about:blank'});
  await browser.close();
  const client = await CDP({port,target:targetId});
  await Promise.all([client.Page.enable(),client.Runtime.enable(),client.Network.enable()]);
  await client.Emulation.setDeviceMetricsOverride({width:1365,height:900,deviceScaleFactor:1,mobile:false});
  return client;
}

async function navigate(client,url){
  await client.Page.navigate({url});
  await new Promise(resolve=>{ const t=setTimeout(resolve,15000); client.Page.loadEventFired(()=>{clearTimeout(t);resolve();}); });
  await sleep(3000);
}

async function evalJs(client,expression,awaitPromise=true){
  return client.Runtime.evaluate({expression,awaitPromise,returnByValue:true,userGesture:true,timeout:15000}).then(r=>r.result?.value);
}

// --- Natural interactions probe: feature detection ---
const NATURAL_INTERACTIONS_PROBE = `(() => {
  const result = {};
  // View Transitions API
  result.viewTransitionsAPI = typeof document.startViewTransition === 'function';
  // Count elements with view-transition-name (sample first 2000 elements)
  let vtCount = 0;
  const els = document.querySelectorAll('*');
  const sample = Math.min(els.length, 3000);
  let transitionCount = 0;
  for (let i=0;i<sample;i++){
    const cs = getComputedStyle(els[i]);
    if (cs.viewTransitionName && cs.viewTransitionName !== 'none') vtCount++;
    const tp = cs.transitionProperty;
    if (tp && tp !== 'none' && tp !== 'all') transitionCount++;
  }
  result.viewTransitionNames = vtCount;
  result.transitionElements = transitionCount;
  // Scroll-driven animations support + usage
  try { result.scrollTimelineSupported = CSS.supports('animation-timeline','scroll()') || CSS.supports('animation-timeline','view()'); } catch { result.scrollTimelineSupported = false; }
  let scrollTimelineUsage = 0;
  try {
    for (const ss of document.styleSheets){
      try {
        for (const rule of ss.cssRules){
          const t = rule.cssText || '';
          if (t.includes('animation-timeline') || t.includes('view-timeline') || t.includes('scroll()')) scrollTimelineUsage++;
        }
      } catch {}
    }
  } catch {}
  result.scrollTimelineUsage = scrollTimelineUsage;
  // Active animations right now
  try { result.activeAnimations = document.getAnimations().length; } catch { result.activeAnimations = 0; }
  // Smooth scroll
  result.smoothScroll = getComputedStyle(document.documentElement).scrollBehavior === 'smooth';
  // prefers-reduced-motion handling: check stylesheets for the media query
  let reducedMotionMQ = false;
  try {
    for (const ss of document.styleSheets){
      try { for (const rule of ss.cssRules){ if (rule.media && rule.media.mediaText && rule.media.mediaText.includes('prefers-reduced-motion')) { reducedMotionMQ = true; break; } } } catch {}
      if (reducedMotionMQ) break;
    }
  } catch {}
  result.reducedMotionMediaQuery = reducedMotionMQ;
  return result;
})()`;

function scoreNaturalInteractions(probe){
  const hasMotion = probe.activeAnimations > 0 || probe.transitionElements > 5;
  const usesViewTransitions = (probe.viewTransitionNames || 0) > 0;  // actual usage, not just API support
  const usesScrollAnimations = (probe.scrollTimelineUsage || 0) > 0;
  const usesModernAPIs = usesViewTransitions || usesScrollAnimations;
  const respectsReducedMotion = probe.reducedMotionMediaQuery;
  if (!hasMotion && !usesModernAPIs){
    return {status:'pass', confidence:'low', summary:'No significant motion detected — nothing to evaluate for natural interactions.'};
  }
  if (usesModernAPIs && respectsReducedMotion){
    return {status:'pass', confidence:'medium', summary:`Uses modern motion APIs (${probe.viewTransitionNames>0?'view transitions':''} ${probe.scrollTimelineUsage>0?'scroll-driven animations':''}) and respects prefers-reduced-motion.`};
  }
  if (hasMotion && !respectsReducedMotion){
    return {status:'issues', confidence:'medium', summary:`Site has motion (${probe.activeAnimations} active animations, ${probe.transitionElements} transitioned elements) but does not handle prefers-reduced-motion. Motion is imposed, not preference-aware.`};
  }
  if (usesModernAPIs && !respectsReducedMotion){
    return {status:'issues', confidence:'medium', summary:`Uses modern motion APIs but does not handle prefers-reduced-motion.`};
  }
  // has motion, respects reduced motion, but no modern APIs — acceptable (traditional but preference-aware)
  return {status:'pass', confidence:'low', summary:`Has motion and respects prefers-reduced-motion, but uses traditional transitions rather than modern declarative APIs.`};
}

// --- Task success probe: find + click primary CTA ---
const FIND_PRIMARY_ACTION = `(() => {
  const visible = el => el.offsetParent !== null && el.getBoundingClientRect().width > 0;
  const text = el => (el.textContent || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase();
  // search inputs
  const searchInputs = [...document.querySelectorAll('input[type="search"], input[role="searchbox"], input[name*="search" i], input[placeholder*="search" i], input[aria-label*="search" i]')].filter(visible);
  if (searchInputs.length) return {type:'search', selector:'input[type=search]', text:text(searchInputs[0]), x:searchInputs[0].getBoundingClientRect().x, y:searchInputs[0].getBoundingClientRect().y};
  // primary action buttons by text — prefer real buttons/role=button over plain links
  const actionWords = ['sign up','signup','sign in','login','log in','register','get started','start free','buy','shop','search','subscribe','try','download','join','create account','order','book','learn more','explore','play','watch','contact','get the app'];
  const buttons = [...document.querySelectorAll('button, [role="button"], input[type="submit"], a[href]')].filter(visible);
  const scored = buttons.map(b => {
    const t = text(b);
    let score = 0;
    for (const w of actionWords){ if (t.includes(w)) score += 10; }
    const r = b.getBoundingClientRect();
    if (r.y > 100 && r.y < 600) score += 3; // above the fold-ish
    if (r.width > 80) score += 2;
    const cs = getComputedStyle(b);
    if (cs.fontWeight >= 600 || parseInt(cs.fontSize) >= 16) score += 2;
    return {el:b, score, text:t, type:'button', x:r.x+r.width/2, y:r.y+r.height/2};
  }).filter(s => s.score > 0).sort((a,b)=>b.score-a.score);
  // prefer button/role=button/submit over plain links among top candidates
  const realButtons = scored.filter(s => s.el.tagName === 'BUTTON' || s.el.getAttribute('role')==='button' || s.el.type==='submit');
  const pool = realButtons.length ? realButtons : scored;
  if (pool.length){
    const best = pool[0];
    return {type:'button', text:best.text, score:best.score, x:best.x, y:best.y};
  }
  return null;
})()`;

async function probeTaskSuccess(client, url){
  const startUrl = await evalJs(client, 'location.href', false);
  const startHtmlLen = await evalJs(client, 'document.body.innerHTML.length', false) || 0;
  const action = await evalJs(client, FIND_PRIMARY_ACTION, true);
  if (!action){
    return {status:'issues', confidence:'medium', summary:'No discoverable primary action (search, sign-up, buy button) found on the homepage — core task path is unclear.'};
  }
  // click it
  let clicked = false;
  try {
    if (action.type === 'search'){
      // focus the search input, type something, submit
      await evalJs(client, `
        const inputs = document.querySelectorAll('input[type="search"], input[role="searchbox"], input[name*="search" i]');
        if (inputs.length){ inputs[0].focus(); inputs[0].value='test'; const form=inputs[0].form; if(form){form.requestSubmit?form.requestSubmit():form.submit();} else {const e=new Event('input',{bubbles:true});inputs[0].dispatchEvent(e);} }
        true
      `);
      clicked = true;
    } else {
      // click at coordinates
      await client.Input.dispatchMouseEvent({type:'mousePressed',x:action.x,y:action.y,button:'left',clickCount:1});
      await client.Input.dispatchMouseEvent({type:'mouseReleased',x:action.x,y:action.y,button:'left',clickCount:1});
      clicked = true;
    }
  } catch {}
  await sleep(3000);
  const endUrl = await evalJs(client, 'location.href', false);
  const endHtmlLen = await evalJs(client, 'document.body.innerHTML.length', false) || 0;
  const urlChanged = startUrl !== endUrl;
  const domChanged = Math.abs(endHtmlLen - startHtmlLen) > 500;
  if (clicked && (urlChanged || domChanged)){
    return {status:'pass', confidence:'medium', summary:`Primary action ("${(action.text||'').slice(0,30)}") found and clicking it progressed the flow${urlChanged?' (navigated to new page)':' (content updated)'}.`};
  }
  if (clicked){
    return {status:'issues', confidence:'low', summary:`Primary action ("${(action.text||'').slice(0,30)}") was found but clicking it did not visibly progress the flow.`};
  }
  return {status:'issues', confidence:'medium', summary:'Primary action found but could not be interacted with.'};
}

async function auditSite(row){
  const [rank, site, url] = row;
  const chrome = await launchChrome();
  let client;
  try {
    client = await newPage(chrome.port);
    await navigate(client, url);
    // natural interactions probe
    let niProbe = null, niScore = null;
    try {
      niProbe = await evalJs(client, NATURAL_INTERACTIONS_PROBE, true);
      niScore = scoreNaturalInteractions(niProbe);
    } catch(e){ niScore = {status:'not-applicable', confidence:'low', summary:'Probe failed: '+e.message}; }
    // task success probe
    let tsScore = null;
    try { tsScore = await probeTaskSuccess(client, url); }
    catch(e){ tsScore = {status:'not-applicable', confidence:'low', summary:'Probe failed: '+e.message}; }
    return {site, ok:true, naturalInteractions:{probe:niProbe, score:niScore}, taskSuccess:tsScore};
  } finally {
    try { if(client) await client.close(); } catch {}
    chrome.close();
  }
}

let rows=[];
if(LIST){ rows=readFileSync(LIST,'utf8').split(/\n/).filter(Boolean).map(l=>l.split('\t')).map(p=>[p[0],p[1],p[2]]); }
else { rows=process.argv.slice(3).map(site=>[null,site,`https://${site}/`]); }
if(LIMIT>0) rows=rows.slice(0,LIMIT);
const results=[];
for(const row of rows){
  const site=row[1];
  // skip if already done
  const existing = results.find(r=>r.site===site);
  if(existing){ continue; }
  console.log(`[active] ${site}`);
  try {
    const r = await Promise.race([
      auditSite(row),
      new Promise((_,reject)=>setTimeout(()=>reject(new Error('timeout 90s')),90000))
    ]);
    results.push(r);
  } catch(e){ console.error(`[active] ERROR ${site}:`,e.message); results.push({site,ok:false,error:e.message}); }
  try { execSync('pkill -9 -f "sotw-active"',{stdio:'ignore'}); } catch {}
  writeJson(`${RESULTS}/${BATCH}.partial.json`, results);
}
writeJson(`${RESULTS}/${BATCH}.json`, results);
console.log(`DONE ${BATCH}: ${results.length}`);
