#!/usr/bin/env python3
"""Legacy evidence batch. This is not a complete web-uplift audit.

It hard-codes broad principle heuristics rather than executing the authoritative
check manifest. It is disabled by default to prevent incomplete output from
being published as an audit.
"""
import json, os, re, subprocess, sys, time, datetime, urllib.parse

if os.environ.get("ALLOW_INCOMPLETE_LEGACY_EVIDENCE_PASS") != "1":
    raise SystemExit(
        "run_gpt_batch.py is a legacy partial evidence collector, not an audit: "
        "it does not execute every check in principles.json. Use an atomic-check "
        "runner and validate exact coverage before publishing. Set "
        "ALLOW_INCOMPLETE_LEGACY_EVIDENCE_PASS=1 only to collect explicitly "
        "labelled recon evidence."
    )
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

def lighthouse_cmd(url, out, timeout=170):
    cmd=['npx','-y','lighthouse',url,'--output=json',f'--output-path={out}','--quiet','--chrome-flags=--headless=new --no-sandbox --disable-gpu','--only-categories=performance,accessibility,best-practices,seo']
    r=sh(cmd, timeout=timeout)
    if r.returncode!=0:
        Path(str(out)+'.error.txt').write_text((r.stdout or '')+'\n'+(r.stderr or ''))
    return r.returncode==0

AXE = Path('/tmp/axe-probe.js')
if not AXE.exists():
    AXE.write_text("""(async()=>{try{const r=await fetch('https://unpkg.com/axe-core@4.10.3/axe.min.js',{cache:'no-store'});const s=await r.text();(0,eval)(s);const result=await globalThis.axe.run(document,{resultTypes:['violations','incomplete'],runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa','best-practice']}});return {ok:true,violations:result.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.length,help:v.help})),incomplete:result.incomplete.length};}catch(e){return {ok:false,error:String(e&&e.message||e)}}})()""")

PROBE_EXPR = """(() => ({title: document.title, lang: document.documentElement.lang || null, dir: document.documentElement.dir || null, h1s: [...document.querySelectorAll('h1')].slice(0,5).map(h=>h.innerText.trim()), metaDescription: document.querySelector('meta[name=description]')?.content || null, viewport: document.querySelector('meta[name=viewport]')?.content || null, forms: document.forms.length, inputsWithoutNames: [...document.querySelectorAll('input,select,textarea')].filter(el=>!el.getAttribute('aria-label')&&!el.labels?.length&&!el.getAttribute('aria-labelledby')).length, buttonsWithoutNames: [...document.querySelectorAll('button')].filter(b=>!b.innerText.trim()&&!b.getAttribute('aria-label')&&!b.getAttribute('aria-labelledby')).length, linksWithoutNames: [...document.querySelectorAll('a[href]')].filter(a=>!a.innerText.trim()&&!a.getAttribute('aria-label')&&!a.getAttribute('aria-labelledby')).length, hasManifest: !!document.querySelector('link[rel~=manifest]'), serviceWorkerControlled: !!navigator.serviceWorker?.controller, colorScheme: getComputedStyle(document.documentElement).colorScheme, animationCount: document.getAnimations({subtree:true}).length, textChars: document.body?.innerText?.length || 0, externalScriptHosts: [...new Set([...document.scripts].map(s=>s.src&&new URL(s.src).host).filter(Boolean))].slice(0,25)}))()"""

INTERACT_EXPR = """(() => { window.scrollTo({top: Math.max(600, document.body.scrollHeight * 0.45), behavior: 'instant'}); setTimeout(() => { const candidates = [...document.querySelectorAll('nav a[href], header a[href], main a[href]')].filter(a => { const href = a.href || ''; return href && !href.startsWith('javascript:') && !href.startsWith('mailto:') && !href.includes('#') && a.offsetParent !== null; }); const a = candidates.find(a => new URL(a.href, location.href).origin === location.origin) || candidates[0]; if (a) a.click(); }, 800); return true; })()"""

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
    lighthouse_cmd(url, evdir/'lighthouse.json', timeout=180)
    evidence_cmd('trace', url, evdir/'trace.json', '--viewport','1365x900','--wait','3500', timeout=80)
    evidence_cmd('video', url, evdir/'reduced-motion.mp4', '--viewport','1365x900','--wait','1500','--duration','2500','--emulate-media','prefers-reduced-motion=reduce', timeout=70)
    evidence_cmd('evaluate', url, evdir/'reduced-motion-probe.json', '--viewport','1365x900','--wait','2500','--emulate-media','prefers-reduced-motion=reduce','--expr',"(() => ({animationCount: document.getAnimations({subtree:true}).length, animations: document.getAnimations({subtree:true}).slice(0,10).map(a=>({playState:a.playState,currentTime:a.currentTime,effect:!!a.effect}))}))()", timeout=45)
    evidence_cmd('heap', url, evdir/'heap.json', '--viewport','1365x900','--wait','2500', timeout=55)
    evidence_cmd('heap', url, evdir/'heap-after.json', '--viewport','1365x900','--wait','10000','--interact',INTERACT_EXPR, timeout=80)
    evidence_cmd('evaluate', url, evdir/'axe.json', '--viewport','1365x900','--wait','3500','--expr-file',str(AXE), timeout=45)
    evidence_cmd('evaluate', url, evdir/'probes.json', '--viewport','1365x900','--wait','2500','--expr',PROBE_EXPR, timeout=35)

    disc=load(evdir/'discoverability.json',{})
    probes=load(evdir/'probes.json',{})
    reduced_motion=load(evdir/'reduced-motion-probe.json',{})
    layout=load(evdir/'layout-desktop.json',{})
    mobile=load(evdir/'layout-mobile.json',{})
    har=load(evdir/'page-summary.json',{})
    lighthouse=load(evdir/'lighthouse.json',{})
    trace=load(evdir/'trace-summary.json',{})
    heap=load(evdir/'heap.json',{})
    heap_after=load(evdir/'heap-after.json',{})
    axe=load(evdir/'axe.json',{})
    totals=har.get('totals',har)
    third=har.get('thirdParty',{})
    req=totals.get('requestCount')
    bytes_=totals.get('totalTransferredBytes')
    heap_size=(heap.get('totals') or heap).get('totalSelfSizeBytes')
    heap_after_size=(heap_after.get('totals') or heap_after).get('totalSelfSizeBytes')
    heap_delta=(heap_after_size-heap_size) if isinstance(heap_size,(int,float)) and isinstance(heap_after_size,(int,float)) else None
    cls=max(layout.get('observed',{}).get('cls',0) or 0, mobile.get('observed',{}).get('cls',0) or 0)
    overflow=mobile.get('observed',{}).get('horizontalOverflowPx',0) or 0
    axe_viol=axe.get('violations') if axe.get('ok') else None
    final_url=disc.get('finalUrl') or url
    http_status=disc.get('fetchedStatus')
    head_status,hsts=headers(final_url)
    if http_status is None: http_status=head_status
    lh_cats={k:v.get('score') for k,v in (lighthouse.get('categories') or {}).items()}
    lh_audits=lighthouse.get('audits') or {}
    lcp=(lh_audits.get('largest-contentful-paint') or {}).get('numericValue') or (trace.get('timings') or {}).get('largestContentfulPaintMs')
    tbt=(lh_audits.get('total-blocking-time') or {}).get('numericValue') or (trace.get('mainThread') or {}).get('totalBlockingTimeMs')
    lh_cls=(lh_audits.get('cumulative-layout-shift') or {}).get('numericValue')
    if isinstance(lh_cls,(int,float)) and lh_cls > cls: cls=lh_cls

    findings=[]
    def suggested_fix(principle, check, title):
        if check == 'largest-contentful-paint': return 'Improve LCP by prioritizing the hero/content resource, reducing render-blocking work, and optimizing server/critical CSS delivery.'
        if check == 'total-blocking-time': return 'Reduce main-thread JavaScript by splitting, deferring, and removing non-critical third-party work.'
        if check == 'lighthouse-best-practices': return 'Review Lighthouse best-practices failures and fix console errors, deprecated APIs, unsafe patterns, and browser compatibility issues.'
        if check == 'lighthouse-seo': return 'Fix Lighthouse SEO failures such as crawlable links, metadata, status codes, and indexable content.'
        if check == 'cumulative-layout-shift': return 'Reserve space for late-loading media/ads and avoid inserting content above existing content after render.'
        if check == 'responsive-no-horizontal-scroll': return 'Add/repair viewport-aware responsive CSS so all content fits within the mobile visual viewport without horizontal scrolling.'
        if check == 'content-visible-without-js': return 'Server-render the primary content and metadata so crawlers and no-JavaScript users can access the page.'
        if check == 'automated-a11y': return 'Fix the reported axe violations with semantic HTML, valid ARIA, accessible names, labels, and sufficient contrast.'
        if check == 'accessible-names': return 'Give every interactive control/link a visible label or accurate accessible name.'
        if check == 'resource-efficiency': return 'Reduce initial bytes by compressing assets, lazy-loading non-critical media, and removing unused scripts.'
        if check == 'third-party-minimisation': return 'Audit third-party scripts/requests and remove, defer, or sandbox anything not essential to the core task.'
        if check == 'respects-color-scheme': return 'Declare color-scheme support and provide a usable dark theme via prefers-color-scheme or light-dark().'
        if check == 'respects-reduced-motion': return 'Wrap non-essential animations in prefers-reduced-motion and reduce/disable motion when requested.'
        if check == 'document-language': return 'Set the correct html lang attribute and ensure locale-specific content uses appropriate language metadata.'
        if check == 'metadata': return 'Add a concise, page-specific meta description and keep essential metadata server-rendered.'
        if check == 'reduce-noise': return 'Prioritise core content and defer or remove distracting ads, trackers, popups, and non-essential chrome.'
        if check == 'heap-growth-after-interaction': return 'Profile the interaction, remove retained detached DOM/listeners/timers, and ensure route/component cleanup releases references.'
        return 'Review this principle against the evidence and apply the relevant modern web guidance.'
    def add(principle,severity,title,evidence,check=None):
        fid=f'{site}-{len(findings)+1:02d}'
        findings.append({'id':fid,'principleId':principle,'checkId':check,'severity':severity,'title':title,'evidence':evidence,'suggestedFix':suggested_fix(principle, check, title)})
    if lcp and lcp>2500: add('be-fast-and-stable','high' if lcp>4000 else 'medium',f'LCP above good threshold ({lcp/1000:.1f}s)',f'Lighthouse/trace recorded LCP {lcp:.0f}ms and TBT {tbt or 0:.0f}ms.','largest-contentful-paint')
    if tbt and tbt>300: add('be-fast-and-stable','medium',f'Total blocking time is high ({tbt:.0f}ms)',f'Lighthouse/trace recorded TBT {tbt:.0f}ms.','total-blocking-time')
    if cls and cls>0.1: add('be-fast-and-stable','high',f'CLS above good threshold ({cls:.2f})',f'layout/Lighthouse recorded CLS {cls:.3f}.','cumulative-layout-shift')
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
    if isinstance(reduced_motion.get('animationCount'), int) and reduced_motion.get('animationCount')>10: add('respect-user-preferences','low','Animations still present under reduced-motion',f"reduced-motion probe counted {reduced_motion.get('animationCount')} active animations; video artifact retained.",'respects-reduced-motion')
    if not probes.get('lang'): add('be-internationalised','medium','Missing document language','html lang was not detected.','document-language')
    if (probes.get('inputsWithoutNames') or 0)>0 or (probes.get('buttonsWithoutNames') or 0)>0 or (probes.get('linksWithoutNames') or 0)>0:
        add('be-inclusive','medium','Unnamed controls or links detected',f"inputsWithoutNames={probes.get('inputsWithoutNames')}, buttonsWithoutNames={probes.get('buttonsWithoutNames')}, linksWithoutNames={probes.get('linksWithoutNames')}.",'accessible-names')
    if not (probes.get('metaDescription') or disc.get('metaDescriptionPresentInRaw')): add('follow-best-practices','medium','Missing meta description signal','No rendered/raw meta description was detected.','metadata')
    if lh_cats.get('best-practices') is not None and lh_cats.get('best-practices') < 0.9: add('follow-best-practices','medium',f'Lighthouse best-practices score {lh_cats.get("best-practices")*100:.0f}', 'Lighthouse best-practices category is below 90.', 'lighthouse-best-practices')
    if lh_cats.get('seo') is not None and lh_cats.get('seo') < 0.9: add('be-discoverable','medium',f'Lighthouse SEO score {lh_cats.get("seo")*100:.0f}', 'Lighthouse SEO category is below 90.', 'lighthouse-seo')
    if req and req>140 and domain not in {'wikipedia.org'}: add('maximize-content-reduce-noise','medium','Substantial page chrome/network noise',f'HAR recorded {req} requests; script hosts include {", ".join((probes.get("externalScriptHosts") or [])[:8])}.','reduce-noise')
    # Coarse heap-after is retained as evidence only. be-memory-efficient is updated by rigorous_memory_pass.mjs.

    by_pr={p:[f for f in findings if f['principleId']==p] for p in PRINCIPLES}
    principles=[]
    for p in PRINCIPLES:
        st='pass'; conf='medium'; summary='No issue found in this batch evidence.'; fs=[]
        if by_pr[p]:
            st='issues'; fs=[{'id':f['id'],'severity':f['severity'],'title':f['title'],'evidence':f['evidence'],'suggestedFix':f.get('suggestedFix')} for f in by_pr[p]]; summary='; '.join(f['title'] for f in by_pr[p][:3])
        if p=='be-resilient':
            st='not-applicable'; summary='Pending active no-JS/offline/reload resilience test; manifest/service-worker presence alone is not treated as a pass.'; conf='low'
        elif p=='be-agent-ready':
            if disc.get('coveragePct') is not None and disc.get('coveragePct')>=67 and not disc.get('isJsShell'):
                st='pass'; summary=f"Non-JS content coverage is {disc.get('coveragePct')}%."; conf='high'
            elif disc.get('coveragePct') is not None:
                st='issues'; summary=f"Non-JS/agent-visible content coverage is {disc.get('coveragePct')}%, isJsShell={disc.get('isJsShell')}."; fs += [{'id':f['id'],'severity':f['severity'],'title':f['title'],'evidence':f['evidence'],'suggestedFix':f.get('suggestedFix')} for f in by_pr.get('be-discoverable',[])]
        elif p=='be-memory-efficient':
            st='not-applicable'; summary='Pending rigorous same-session memory audit; coarse heap snapshots are retained as evidence but not treated as pass/fail.'; conf='low'; fs=[]
        elif p=='be-fast-and-stable' and st=='pass':
            summary=f"Lighthouse/trace/layout evidence: LCP {lcp}, CLS {cls:.3f}, TBT {tbt}."; conf='high' if lcp is not None else 'medium'
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
            summary='Basic metadata/viewport probes and Lighthouse best-practices/SEO checks did not raise a finding.'; conf='high' if lh_cats else 'medium'
        elif p=='be-discoverable' and st=='pass':
            summary=f"discoverability coverage {disc.get('coveragePct')}%, JS shell={disc.get('isJsShell')}."; conf='high'
        elif p=='be-sustainable' and st=='pass':
            summary=f"Initial transfer {bytes_} bytes across {req} requests."; conf='high' if bytes_ is not None else 'low'
        elif p=='be-private-and-secure' and st=='pass':
            summary=f"Network surface looked bounded in batch evidence ({req} requests)."; conf='medium'
        elif p=='be-internationalised' and st=='pass':
            summary=f"html lang={probes.get('lang')!r}; deeper locale flows not tested."; conf='medium'
        elif p=='provide-guided-navigation':
            if st == 'pass':
                st='not-applicable'; summary='Pending active wayfinding/search/menu-flow test; passive homepage evidence is not treated as a pass.'; conf='low'
        elif p=='implement-natural-interactions':
            if st == 'pass':
                st='not-applicable'; summary='Pending active keyboard/focus/input-modality test; screenshot/DOM evidence is not treated as a pass.'; conf='low'
        elif p=='support-core-task-success' and st=='pass':
            summary='Primary homepage entry point rendered; task completion flow not exercised.'; conf='low'
        elif p=='be-trustworthy':
            if st == 'pass':
                st='not-applicable'; summary='Pending active consent/account/commerce-flow review for dark patterns; passive homepage evidence is not treated as a pass.'; conf='low'
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
            'lighthouse':{
                'performance': None if lh_cats.get('performance') is None else round(lh_cats.get('performance')*100),
                'accessibility': None if lh_cats.get('accessibility') is None else round(lh_cats.get('accessibility')*100),
                'bestPractices': None if lh_cats.get('best-practices') is None else round(lh_cats.get('best-practices')*100),
                'seo': None if lh_cats.get('seo') is None else round(lh_cats.get('seo')*100),
            },
            'axeViolations':None if axe_viol is None else len(axe_viol),
            'heapSize':heap_size,
            'heapSizeAfterInteraction':heap_after_size,
            'heapDeltaAfterInteraction':heap_delta,
            'cls':cls,
            'lcp':lcp,
            'inp':None,
            'tbt':tbt,
            'reducedMotionVideo': str(evdir/'reduced-motion.mp4') if (evdir/'reduced-motion.mp4').exists() else None,
            'reducedMotionProbe': reduced_motion,
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
        '_caveats':['Scale batch includes Lighthouse, trace, HAR, layout, screenshots, discoverability, axe/probes, and reduced-motion video; INP still requires a dedicated interaction flow.','Memory pass/fail requires rigorous_memory_pass.mjs; coarse heap snapshots are retained but not treated as pass/fail.','Dark-mode/reduced-motion are inferred from probes unless separately captured.'] + ([f'Axe blocked or failed: {axe_block_reason}'] if axe_blocked else [])
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
