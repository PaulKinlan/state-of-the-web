#!/usr/bin/env node
// Secrets/API key detection — scans page HTML, inline scripts, and loaded JS resources
// for exposed credentials (AWS keys, Google API keys, Stripe, GitHub tokens, JWTs, private keys, etc.)
// Feeds the be-private-and-secure principle.
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import CDP from '/home/paulkinlan/.npm/_npx/5883e6c84caa01ab/node_modules/chrome-remote-interface/index.js';

const RESULTS = '/home/paulkinlan/state-of-the-web/results/gpt';
const CHROME = process.env.CHROME_BIN || '/usr/bin/google-chrome-stable';
const BATCH = process.argv[2] || 'secrets-scan';
const LIST = process.argv[3];
const LIMIT = Number(process.argv[4] || '0');

function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }
function safe(s){ return s.toLowerCase().replace(/[^a-z0-9.-]+/g,'-').replace(/^-|-$/g,''); }
function writeJson(p,o){ writeFileSync(p, JSON.stringify(o,null,2)+'\n'); }

async function launchChrome(){
  const udd = mkdtempSync(join(tmpdir(),'sotw-secrets-'));
  const proc = spawn(CHROME, ['--headless=new','--remote-debugging-port=0','--no-sandbox',`--user-data-dir=${udd}`,'--no-first-run','--no-default-browser-check','--disable-gpu','--disable-dev-shm-usage'], {stdio:['ignore','ignore','pipe']});
  const port = await new Promise((res,rej)=>{ let b=''; const t=setTimeout(()=>rej(new Error('Chrome timeout')),20000); proc.stderr.on('data',c=>{b+=c;const m=b.match(/DevTools listening on ws:\/\/[^:]+:(\d+)\//);if(m){clearTimeout(t);res(Number(m[1]));}}); proc.on('exit',c=>{clearTimeout(t);rej(new Error('Chrome exited '+c));}); });
  return {proc,port,udd,close(){try{proc.kill('SIGTERM')}catch{};try{rmSync(udd,{recursive:true,force:true})}catch{};}};
}

// Secret patterns — each has id, regex, severity, description
const PATTERNS = [
  {id:'aws-access-key', re:/AKIA[0-9A-Z]{16}/g, severity:'critical', desc:'AWS Access Key ID'},
  {id:'aws-secret', re:/aws_secret_access_key["\s:=]+([A-Za-z0-9/+=]{40})/g, severity:'critical', desc:'AWS Secret Access Key'},
  {id:'google-api-key', re:/AIza[0-9A-Za-z_-]{35}/g, severity:'high', desc:'Google API Key'},
  {id:'stripe-secret', re:/sk_live_[0-9a-zA-Z]{24,}/g, severity:'critical', desc:'Stripe Secret Key'},
  {id:'stripe-publishable', re:/pk_live_[0-9a-zA-Z]{24,}/g, severity:'medium', desc:'Stripe Publishable Key (live)'},
  {id:'github-token', re:/gh[pousr]_[0-9a-zA-Z]{36,}/g, severity:'critical', desc:'GitHub Token'},
  {id:'slack-token', re:/xox[baprs]-[0-9A-Za-z-]{10,}/g, severity:'critical', desc:'Slack Token'},
  {id:'jwt', re:/eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}/g, severity:'high', desc:'JWT Token'},
  {id:'private-key', re:/-----BEGIN [A-Z ]*PRIVATE KEY-----/g, severity:'critical', desc:'Private Key'},
  {id:'connection-string', re:/(mongodb|postgres|postgresql|mysql|redis):\/\/[^\s"']+:[^\s"']+@[^\s"']+/g, severity:'critical', desc:'Database Connection String with credentials'},
  {id:'generic-api-key', re:/(?:api[_-]?key|apikey|api[_-]?secret)["\s:=]+['"]([A-Za-z0-9_-]{32,})['"]/gi, severity:'high', desc:'Generic API Key/Secret (32+ chars)'},
  {id:'bearer-token', re:/(?:bearer|authorization)["\s:=]+([A-Za-z0-9_-]{20,})/gi, severity:'high', desc:'Bearer/Authorization token'},
  {id:'facebook-app-secret', re:/facebook.*?(?:app[_-]?secret|secret)["\s:=]+([a-f0-9]{32})/gi, severity:'high', desc:'Facebook App Secret'},
  {id:'twitter-token', re:/twitter.*?(?:api[_-]?key|secret|token|bearer)["\s:=]+([A-Za-z0-9_-]{20,})/gi, severity:'high', desc:'Twitter API Token/Secret'},
];

// Scan a text blob for secrets
function scanText(text, source){
  const findings = [];
  for (const p of PATTERNS){
    p.re.lastIndex = 0;
    let m;
    let count = 0;
    while ((m = p.re.exec(text)) !== null){
      count++;
      if (count <= 3){ // cap at 3 examples per pattern per source
        const matched = m[0];
        // redact the middle of the secret for safety
        const redacted = matched.length > 12 ? matched.slice(0,6) + '…' + matched.slice(-4) : matched;
        findings.push({pattern:p.id, severity:p.severity, description:p.desc, source, match:redacted});
      }
    }
    if (count > 3) findings.push({pattern:p.id, severity:p.severity, description:p.desc, source, note:`+${count-3} more matches`});
  }
  return findings;
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
  await new Promise(r=>{ const t=setTimeout(r,15000); client.Page.loadEventFired(()=>{clearTimeout(t);r();}); });
  await sleep(3000);
}

async function evalJs(client,expression,awaitPromise=true){
  return client.Runtime.evaluate({expression,awaitPromise,returnByValue:true,timeout:15000}).then(r=>r.result?.value);
}

async function scanSite(row){
  const [rank, site, url] = row;
  const chrome = await launchChrome();
  let client;
  try {
    client = await newPage(chrome.port);
    await navigate(client, url);
    const findings = [];
    // 1. Scan page HTML
    const html = await evalJs(client, 'document.documentElement.outerHTML') || '';
    findings.push(...scanText(html, 'page HTML'));
    // 2. Scan inline scripts
    const inlineScripts = await evalJs(client, `[...document.querySelectorAll('script:not[src]')].map(s=>s.textContent).join('\\n')`) || '';
    findings.push(...scanText(inlineScripts, 'inline scripts'));
    // 3. Scan external JS resources (fetch + scan)
    const scriptUrls = await evalJs(client, `[...document.querySelectorAll('script[src]')].map(s=>s.src).slice(0,20)`) || [];
    for (const su of scriptUrls){
      try {
        const jsText = await evalJs(client, `fetch('${su}').then(r=>r.text()).catch(()=>'')`, true);
        if (jsText) findings.push(...scanText(jsText, 'external JS: ' + su.split('/').pop()));
      } catch {}
    }
    // 4. Check meta tags for tokens
    const metaContent = await evalJs(client, `[...document.querySelectorAll('meta')].map(m=>m.content||'').join(' ')`) || '';
    findings.push(...scanText(metaContent, 'meta tags'));
    // Deduplicate
    const seen = new Set();
    const deduped = findings.filter(f => { const k = f.pattern+':'+f.match; if(seen.has(k)) return false; seen.add(k); return true; });
    const hasIssues = deduped.length > 0;
    return {
      site, ok: true,
      findingCount: deduped.length,
      findings: deduped.slice(0, 20), // cap stored findings
      status: hasIssues ? 'issues' : 'pass',
      confidence: hasIssues ? 'high' : 'medium',
      summary: hasIssues
        ? `${deduped.length} potential exposed secret(s) detected: ${deduped.slice(0,3).map(f=>f.description).join(', ')}`
        : 'No exposed API keys, tokens, or secrets detected in page HTML, scripts, or meta tags.'
    };
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
  console.log(`[secrets] ${site}`);
  try {
    const r = await Promise.race([
      scanSite(row),
      new Promise((_,reject)=>setTimeout(()=>reject(new Error('timeout 60s')),60000))
    ]);
    results.push(r);
  } catch(e){ console.error(`[secrets] ERROR ${site}:`,e.message); results.push({site,ok:false,error:e.message}); }
  try { require('child_process').execSync('pkill -9 -f "sotw-secrets"',{stdio:'ignore'}); } catch {}
  writeJson(`${RESULTS}/${BATCH}.partial.json`, results);
}
writeJson(`${RESULTS}/${BATCH}.json`, results);
console.log(`DONE ${BATCH}: ${results.length} sites, ${results.filter(r=>r.status==='issues').length} with secrets`);
