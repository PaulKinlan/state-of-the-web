#!/usr/bin/env python3
import json, os, re, subprocess, sys, time, datetime, urllib.parse
from pathlib import Path

ROOT = Path('/home/paulkinlan/journal')
RESULTS = Path('/home/paulkinlan/state-of-the-web/results/gpt')
BATCH = sys.argv[1] if len(sys.argv) > 1 else 'batch-001'
SITES = Path(sys.argv[2]) if len(sys.argv) > 2 else RESULTS / f'{BATCH}-sites.tsv'
RUN_DIR = RESULTS / f'{BATCH}-evidence'
RUN_DIR.mkdir(parents=True, exist_ok=True)

PRINCIPLES = ['respect-user-preferences','implement-natural-interactions','provide-guided-navigation','maximize-content-reduce-noise','adapt-to-the-form-factor','support-core-task-success','be-fast-and-stable','be-inclusive','follow-best-practices','be-discoverable','be-private-and-secure','be-resilient','be-internationalised','be-trustworthy','be-sustainable','be-agent-ready','be-memory-efficient']

def sh(args, timeout=45):
    try:
        return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(args, 124, e.stdout or '', (e.stderr or '') + '\nTIMEOUT')

def load(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default if default is not None else {}

def write(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2))

def safe_site(domain):
    return re.sub(r'[^a-z0-9.-]+','-',domain.lower()).strip('-')

def headers(url):
    r=sh(['curl','-sSIL','--max-time','12',url], timeout=15)
    text=(r.stdout or '')+(r.stderr or '')
    blocks=[b for b in re.split(r'\r?\n\r?\n', text) if b.strip().startswith('HTTP/')]
    last=blocks[-1] if blocks else text
    m=re.search(r'HTTP/\S+\s+(\d+)', last)
    return (int(m.group(1)) if m else None), bool(re.search(r'(?im)^strict-transport-security:', text))

def evidence_cmd(kind, url, out, *opts, timeout=45):
    cmd=['node','.web-uplift/evidence/cli.mjs',kind,url,'--out',str(out),*opts,'--quiet']
    r=sh(cmd, timeout=timeout)
    if r.returncode!=0:
        Path(str(out)+'.error.txt').write_text((r.stdout or '')+'\n'+(r.stderr or ''))
    return r.returncode==0

AXE = Path('/tmp/axe-probe.js')
if not AXE.exists():
    AXE.write_text("""(async()=>{try{const r=await fetch('https://unpkg.com/axe-core@4.10.3/axe.min.js',{cache:'no-store'});const s=await r.text();(0,eval)(s);const result=await globalThis.axe.run(document,{resultTypes:['violations','incomplete'],runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa','best-practice']}});return {ok:true,violations:result.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.length,help:v.help})),incomplete:result.incomplete.length};}catch(e){return {ok:false,error:String(e&&e.message||e)}}})()""")

PROBE_EXPR = """(() => ({title: document.title, lang: document.documentElement.lang || null, dir: document.documentElement.dir || null, h1s: [...document.querySelectorAll('h1')].slice(0,5).map(h=>h.innerText.trim()), metaDescription: document.querySelector('meta[name=description]')?.content || null, viewport: document.querySelector('meta[name=viewport]')?.content || null, forms: document.forms.length, inputsWithoutNames: [...document.querySelectorAll('input,select,textarea')].filter(el=>!el.getAttribute('aria-label')&&!el.labels?.length&&!el.getAttribute('aria-labelledby')).length, buttonsWithoutNames: [...document.querySelectorAll('button')].filter(b=>!b.innerText.trim()&&!b.getAttribute('aria-label')&&!b.getAttribute('aria-labelledby')).length, linksWithoutNames: [...document.querySelectorAll('a[href]')].filter(a=>!a.innerText.trim()&&!a.getAttribute('aria-label')&&!a.getAttribute('aria-labelledby')).length, hasManifest: !!document.querySelector('link[rel~=manifest]'), serviceWorkerControlled: !!navigator.serviceWorker?.controller, colorScheme: getComputedStyle(document.documentElement).colorScheme, animationCount: document.getAnimations({subtree:true}).length, textChars: document.body?.innerText?.length || 0, externalScriptHosts: [...new Set([...document.scripts].map(s=>s.src&&new URL(s.src).host).filter(Boolean))].slice(0,25)}))()"""

def score(findings, outcomes):
    weights={'critical':18,'high':12,'medium':6,'low':2}
    penalty=sum(weights.get(f.get('severity'),4) for f in findings)
    issue_pr=sum(1 for p in outcomes if p['status']=='issues')
    return max(0,min(100,round(100-penalty-issue_pr*2)))

def audit(rank, domain, url):
    site=safe_site(domain)
    evdir=RUN_DIR/site/'evidence'
    evdir.mkdir(parents=True, exist_ok=True)
    print(f'[{datetime.datetime.utcnow().isoformat()}Z] {rank} {domain} {url}', flush=True)

    # Evidence pass. Keep waits short; coordinator has deeper CDP metrics.
    evidence_cmd('screenshot', url, evdir/'screenshot.png', '--viewport','1365x900','--wait','2500', timeout=35)
    evidence_cmd('screenshot', url, evdir/'mobile.png', '--viewport','390x844','--wait','2500', timeout=35)
    evidence_cmd('layout', url, evdir/'layout-desktop.json', '--viewport','1365x900','--wait','2500', timeout=35)
    evidence_cmd('layout', url, evdir/'layout-mobile.json', '--viewport','390x844','--wait','2500', timeout=35)
    evidence_cmd('discoverability', url, evdir/'discoverability.json', '--viewport','1365x900','--wait','2500', timeout=45)
    evidence_cmd('har', url, evdir/'page.har', '--viewport','1365x900','--wait','3000', timeout=55)
    evidence_cmd('heap', url, evdir/'heap.json', '--viewport','1365x900','--wait','2500', timeout=55)
    evidence_cmd('evaluate', url, evdir/'axe.json', '--viewport','1365x900','--wait','3500','--expr-file',str(AXE), timeout=45)
    evidence_cmd('evaluate', url, evdir/'probes.json', '--viewport','1365x900','--wait','2500','--expr',PROBE_EXPR, timeout=35)

    disc=load(evdir/'discoverability.json',{})
    probes=load(evdir/'probes.json',{})
    layout=load(evdir/'layout-desktop.json',{})
    mobile=load(evdir/'layout-mobile.json',{})
    har=load(evdir/'page-summary.json',{})
    heap=load(evdir/'heap.json',{})
    axe=load(evdir/'axe.json',{})
    totals=har.get('totals',har)
    third=har.get('thirdParty',{})
    req=totals.get('requestCount')
    bytes_=totals.get('totalTransferredBytes')
    heap_size=(heap.get('totals') or heap).get('totalSelfSizeBytes')
    cls=max(layout.get('observed',{}).get('cls',0) or 0, mobile.get('observed',{}).get('cls',0) or 0)
    overflow=mobile.get('observed',{}).get('horizontalOverflowPx',0) or 0
    axe_viol=axe.get('violations') if axe.get('ok') else None
    final_url=disc.get('finalUrl') or url
    http_status=disc.get('fetchedStatus')
    head_status,hsts=headers(final_url)
    if http_status is None: http_status=head_status

    findings=[]
    def add(principle,severity,title,evidence,check=None):
        fid=f'{site}-{len(findings)+1:02d}'
        findings.append({'id':fid,'principleId':principle,'checkId':check,'severity':severity,'title':title,'evidence':evidence})
    if cls and cls>0.1: add('be-fast-and-stable','high',f'CLS above good threshold ({cls:.2f})',f'layout primitive reported CLS {cls:.3f}.','cumulative-layout-shift')
    if overflow>0: add('adapt-to-the-form-factor','high','Mobile viewport has horizontal overflow',f'mobile layout reported horizontalOverflowPx={overflow}.','responsive-no-horizontal-scroll')
    if disc.get('coveragePct') is not None and disc.get('coveragePct')<50: add('be-discoverable','high' if disc.get('isJsShell') else 'medium',f"Low non-JS discoverability ({disc.get('coveragePct')}%)",f"discoverability found coveragePct={disc.get('coveragePct')}%, isJsShell={disc.get('isJsShell')}.",'content-visible-without-js')
    axe_blocked = False
    axe_block_reason = None
    if axe_viol:
        add('be-inclusive','high' if any(v.get('impact') in ('critical','serious') for v in axe_viol) else 'medium',f'axe found {len(axe_viol)} violation groups','axe violations: '+', '.join(f"{v.get('id')}({v.get('impact')},{v.get('nodes')} nodes)" for v in axe_viol[:8]),'automated-a11y')
    elif axe.get('ok') is False:
        axe_blocked = True
        axe_block_reason = str(axe.get('error',''))[:220]
    if bytes_ and bytes_>3_000_000: add('be-sustainable','medium',f'Heavy transfer on initial load ({bytes_/1_000_000:.1f} MB)',f'HAR recorded {req} requests and {bytes_} transferred bytes.','resource-efficiency')
    if req and (req>100 or (third.get('thirdPartyRequestCount') or 0)>50): add('be-private-and-secure','medium','Large third-party/network surface',f"HAR requests={req}; thirdPartyRequests={third.get('thirdPartyRequestCount')}; thirdPartyBytes={third.get('thirdPartyTransferredBytes')}.",'third-party-minimisation')
    if probes.get('colorScheme') in (None,'normal'): add('respect-user-preferences','medium','No explicit color-scheme/dark-mode signal observed',f"computed root colorScheme={probes.get('colorScheme')!r}; dark condition not separately captured in this batch.",'respects-color-scheme')
    if (probes.get('animationCount') or 0)>10: add('respect-user-preferences','low','Animations present; reduced-motion not verified',f"probe counted {probes.get('animationCount')} active animations.",'respects-reduced-motion')
    if not probes.get('lang'): add('be-internationalised','medium','Missing document language','html lang was not detected.','document-language')
    if (probes.get('inputsWithoutNames') or 0)>0 or (probes.get('buttonsWithoutNames') or 0)>0 or (probes.get('linksWithoutNames') or 0)>0:
        add('be-inclusive','medium','Unnamed controls or links detected',f"inputsWithoutNames={probes.get('inputsWithoutNames')}, buttonsWithoutNames={probes.get('buttonsWithoutNames')}, linksWithoutNames={probes.get('linksWithoutNames')}.",'accessible-names')
    if not (probes.get('metaDescription') or disc.get('metaDescriptionPresentInRaw')): add('follow-best-practices','medium','Missing meta description signal','No rendered/raw meta description was detected.','metadata')
    if req and req>140 and domain not in {'wikipedia.org'}: add('maximize-content-reduce-noise','medium','Substantial page chrome/network noise',f'HAR recorded {req} requests; script hosts include {", ".join((probes.get("externalScriptHosts") or [])[:8])}.','reduce-noise')

    by_pr={p:[f for f in findings if f['principleId']==p] for p in PRINCIPLES}
    principles=[]
    for p in PRINCIPLES:
        st='pass'; conf='medium'; summary='No issue found in this batch evidence.'; fs=[]
        if by_pr[p]:
            st='issues'; fs=[{'id':f['id'],'severity':f['severity'],'title':f['title'],'evidence':f['evidence']} for f in by_pr[p]]; summary='; '.join(f['title'] for f in by_pr[p][:3])
        if p=='be-resilient':
            if probes.get('hasManifest') or probes.get('serviceWorkerControlled'):
                summary='Manifest/service-worker signal present; offline fallback not exercised.'; conf='low'
            else:
                st='not-applicable'; summary='No app-like offline/installable intent established in homepage batch evidence.'
        elif p=='be-agent-ready':
            if disc.get('coveragePct') is not None and disc.get('coveragePct')>=67 and not disc.get('isJsShell'):
                st='pass'; summary=f"Non-JS content coverage is {disc.get('coveragePct')}%."; conf='high'
            elif disc.get('coveragePct') is not None:
                st='issues'; summary=f"Non-JS/agent-visible content coverage is {disc.get('coveragePct')}%, isJsShell={disc.get('isJsShell')}."; fs += [{'id':f['id'],'severity':f['severity'],'title':f['title'],'evidence':f['evidence']} for f in by_pr.get('be-discoverable',[])]
        elif p=='be-memory-efficient':
            st='pass'; summary=f"Single-load heap snapshot captured ({heap_size} bytes self size); no repeated-interaction leak test."; conf='low'
        elif p=='be-fast-and-stable' and st=='pass':
            summary=f"Batch layout CLS {cls:.3f}; deeper LCP/INP merged from CDP coordinator data."; conf='medium'
        elif p=='adapt-to-the-form-factor' and st=='pass':
            summary='Mobile layout showed no horizontal overflow.'; conf='high'
        elif p=='be-inclusive' and st=='pass':
            if axe_blocked:
                summary='Axe was blocked or failed, so no automated accessibility violation is asserted; judgement is based on DOM probes only.'
                conf='low'
            else:
                summary='axe/probes found no obvious homepage accessibility issue.' if axe.get('ok') else 'Axe unavailable; judgement based on DOM probes only.'
                conf='high' if axe.get('ok') else 'low'
        elif p=='follow-best-practices' and st=='pass':
            summary='Basic metadata/viewport probes passed; Lighthouse not run in this scale batch.'; conf='medium'
        elif p=='be-discoverable' and st=='pass':
            summary=f"discoverability coverage {disc.get('coveragePct')}%, JS shell={disc.get('isJsShell')}."; conf='high'
        elif p=='be-sustainable' and st=='pass':
            summary=f"Initial transfer {bytes_} bytes across {req} requests."; conf='high' if bytes_ is not None else 'low'
        elif p=='be-private-and-secure' and st=='pass':
            summary=f"Network surface looked bounded in batch evidence ({req} requests)."; conf='medium'
        elif p=='be-internationalised' and st=='pass':
            summary=f"html lang={probes.get('lang')!r}; deeper locale flows not tested."; conf='medium'
        elif p=='provide-guided-navigation' and st=='pass':
            summary='Homepage navigation/search entry points were not flagged by DOM/screenshot evidence; flow not exercised.'; conf='low'
        elif p=='implement-natural-interactions' and st=='pass':
            summary='No interaction-specific issue found; keyboard/focus journeys not exercised.'; conf='low'
        elif p=='support-core-task-success' and st=='pass':
            summary='Primary homepage entry point rendered; task completion flow not exercised.'; conf='low'
        elif p=='be-trustworthy' and st=='pass':
            summary='No obvious trust/deceptive-design issue found in homepage batch evidence; consent/account flows not exercised.'; conf='low'
        principles.append({'id':p,'status':st,'confidence':conf,'summary':summary,'findings':fs})

    counts={}
    for p in principles: counts[p['status']]=counts.get(p['status'],0)+1
    if findings:
        top='; '.join(f"{f['severity']} {f['principleId']}: {f['title']}" for f in findings[:3])
        verdict=f"Homepage batch: {counts.get('issues',0)} principles with issues; top concerns: {top}."
    else:
        verdict=f"Homepage batch: no findings in this evidence pass; {counts.get('pass',0)} principles passed."
    out={
        'site':domain,
        'rank':rank,
        'auditedAt':datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z'),
        'url':url,
        'finalUrl':final_url,
        'httpStatus':http_status,
        'evidence':{
            'screenshot':str(evdir/'screenshot.png'),
            'lighthouse':{'performance':None,'accessibility':None,'bestPractices':None,'seo':None},
            'axeViolations':None if axe_viol is None else len(axe_viol),
            'heapSize':heap_size,
            'cls':cls,
            'lcp':None,
            'inp':None,
            'isJsShell':disc.get('isJsShell'),
            'textChars':(disc.get('rendered') or {}).get('textChars') or probes.get('textChars'),
            'hasViewport':bool(probes.get('viewport') or layout.get('observed',{}).get('hasViewportMeta')),
            'hasMetaDescription':bool(probes.get('metaDescription') or disc.get('metaDescriptionPresentInRaw')),
            'httpsOnly':url.startswith('https://') and str(final_url).startswith('https://'),
            'hsts':hsts,
        },
        'principles':principles,
        'verdict':verdict,
        'overallScore':score(findings, principles),
        '_batch':BATCH,
        '_artifactsDir':str(evdir),
        '_caveats':['Scale batch: no Lighthouse, LCP or INP in this pass; coordinator CDP data should fill objective metrics.','Memory is a single heap snapshot, not a repeated-interaction leak proof.','Dark-mode/reduced-motion are inferred from probes unless separately captured.'] + ([f'Axe blocked or failed: {axe_block_reason}'] if axe_blocked else [])
    }
    write(RESULTS/f'{site}.json', out)
    return out

rows=[]
for line in SITES.read_text().splitlines():
    if not line.strip() or line.startswith('#'): continue
    parts=line.split('\t')
    if len(parts)==3: rank,domain,url=parts; rank=int(rank) if rank!='' else None
    elif len(parts)==2: rank=None; domain,url=parts
    else: continue
    rows.append((rank,domain,url))

batch=[]
for row in rows:
    domain=row[1]
    if (RESULTS/f'{safe_site(domain)}.json').exists():
        try:
            existing=load(RESULTS/f'{safe_site(domain)}.json')
            if existing.get('_batch')==BATCH:
                print(f'skip existing {domain}', flush=True); batch.append(existing); continue
        except Exception: pass
    try:
        batch.append(audit(*row))
    except Exception as e:
        print(f'ERROR {domain}: {e}', flush=True)
        write(RESULTS/f'{safe_site(domain)}.error.json', {'site':domain,'rank':row[0],'url':row[2],'error':str(e),'_batch':BATCH})
    write(RESULTS/f'{BATCH}.partial.json', batch)
write(RESULTS/f'{BATCH}.json', batch)
print(f'DONE {BATCH}: {len(batch)} results -> {RESULTS}/{BATCH}.json', flush=True)
