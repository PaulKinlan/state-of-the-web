#!/usr/bin/env python3
import json, os, re, subprocess, sys, time
from pathlib import Path

ROOT=Path('/home/paulkinlan/journal')
RESULTS=Path('/home/paulkinlan/state-of-the-web/results/gpt')
BATCH=sys.argv[1] if len(sys.argv)>1 else 'supplemental-ltv-001'
LIMIT=int(sys.argv[2]) if len(sys.argv)>2 else 0
OUT=RESULTS/f'{BATCH}-evidence'
OUT.mkdir(parents=True, exist_ok=True)

WEIGHTS={'critical':18,'high':12,'medium':6,'low':2}

def safe(site): return re.sub(r'[^a-z0-9.-]+','-',site.lower()).strip('-')
def load(p): return json.loads(Path(p).read_text())
def write(p,o): Path(p).write_text(json.dumps(o,indent=2)+'\n')
def sh(args,timeout=120,cwd=ROOT):
    try: return subprocess.run(args,cwd=cwd,capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired as e: return subprocess.CompletedProcess(args,124,e.stdout or '',(e.stderr or '')+'\nTIMEOUT')

def evidence(kind,url,out,*opts,timeout=90):
    cmd=['node','.web-uplift/evidence/cli.mjs',kind,url,'--out',str(out),*opts,'--quiet']
    r=sh(cmd,timeout=timeout)
    if r.returncode!=0: Path(str(out)+'.error.txt').write_text((r.stdout or '')+'\n'+(r.stderr or ''))
    return r.returncode==0

def lighthouse(url,out,timeout=190):
    cmd=['npx','-y','lighthouse',url,'--output=json',f'--output-path={out}','--quiet','--chrome-flags=--headless=new --no-sandbox --disable-gpu','--only-categories=performance,accessibility,best-practices,seo']
    r=sh(cmd,timeout=timeout,cwd=RESULTS)
    if r.returncode!=0: Path(str(out)+'.error.txt').write_text((r.stdout or '')+'\n'+(r.stderr or ''))
    return r.returncode==0

def suggested_fix(check):
    return {
      'largest-contentful-paint':'Improve LCP by prioritizing the hero/content resource, reducing render-blocking work, and optimizing server/critical CSS delivery.',
      'total-blocking-time':'Reduce main-thread JavaScript by splitting, deferring, and removing non-critical third-party work.',
      'cumulative-layout-shift':'Reserve space for late-loading media/ads and avoid inserting content above existing content after render.',
      'lighthouse-best-practices':'Review Lighthouse best-practices failures and fix console errors, deprecated APIs, unsafe patterns, and browser compatibility issues.',
      'lighthouse-seo':'Fix Lighthouse SEO failures such as crawlable links, metadata, status codes, and indexable content.',
      'respects-reduced-motion':'Disable or reduce non-essential animations/transitions when prefers-reduced-motion: reduce is active.'
    }.get(check,'Review the evidence and apply the relevant modern web guidance.')

def mkfinding(site, principle, severity, title, evidence, check):
    return {'id':f'{safe(site)}-{check}','principleId':principle,'checkId':check,'severity':severity,'title':title,'evidence':evidence,'suggestedFix':suggested_fix(check)}

def replace_ltv_findings(principle, new_findings, checks):
    old=principle.get('findings') or []
    kept=[f for f in old if f.get('checkId') not in checks]
    principle['findings']=kept+new_findings
    if principle['findings']:
        principle['status']='issues'
        principle['summary']='; '.join(f['title'] for f in principle['findings'][:3])
        principle['confidence']='high'
    else:
        principle['status']='pass'
        principle['confidence']='high'

def rescore(obj):
    findings=[]
    for p in obj.get('principles',[]): findings += p.get('findings') or []
    issue_pr=sum(1 for p in obj.get('principles',[]) if p.get('status')=='issues')
    return max(0,min(100,round(100-sum(WEIGHTS.get(f.get('severity'),4) for f in findings)-issue_pr*2)))

def process(path):
    obj=load(path)
    site=obj.get('site') or Path(path).stem
    url=obj.get('finalUrl') or obj.get('url') or f'https://{site}/'
    evdir=OUT/safe(site); evdir.mkdir(parents=True,exist_ok=True)
    print(f'[ltv] {site}', flush=True)
    lh_path=evdir/'lighthouse.json'; trace_path=evdir/'trace.json'; video_path=evdir/'reduced-motion.mp4'; rm_probe_path=evdir/'reduced-motion-probe.json'
    if not lh_path.exists(): lighthouse(url,lh_path)
    if not (evdir/'trace-summary.json').exists(): evidence('trace',url,trace_path,'--viewport','1365x900','--wait','3500',timeout=90)
    if not video_path.exists(): evidence('video',url,video_path,'--viewport','1365x900','--wait','1500','--duration','2500','--emulate-media','prefers-reduced-motion=reduce',timeout=80)
    if not rm_probe_path.exists(): evidence('evaluate',url,rm_probe_path,'--viewport','1365x900','--wait','2500','--emulate-media','prefers-reduced-motion=reduce','--expr',"(() => ({animationCount: document.getAnimations({subtree:true}).length, animations: document.getAnimations({subtree:true}).slice(0,10).map(a=>({playState:a.playState, currentTime:a.currentTime, effect: !!a.effect}))}))()",timeout=45)

    lh=load(lh_path) if lh_path.exists() else {}
    trace=load(evdir/'trace-summary.json') if (evdir/'trace-summary.json').exists() else {}
    rm=load(rm_probe_path) if rm_probe_path.exists() else {}
    cats={k:v.get('score') for k,v in (lh.get('categories') or {}).items()}
    audits=lh.get('audits') or {}
    lcp=(audits.get('largest-contentful-paint') or {}).get('numericValue') or (trace.get('timings') or {}).get('largestContentfulPaintMs')
    tbt=(audits.get('total-blocking-time') or {}).get('numericValue') or (trace.get('mainThread') or {}).get('totalBlockingTimeMs')
    lh_cls=(audits.get('cumulative-layout-shift') or {}).get('numericValue')
    cls=max([x for x in [obj.get('evidence',{}).get('cls'), lh_cls] if isinstance(x,(int,float))] or [None])
    ev=obj.setdefault('evidence',{})
    ev['lighthouse']={'performance':None if cats.get('performance') is None else round(cats['performance']*100),'accessibility':None if cats.get('accessibility') is None else round(cats['accessibility']*100),'bestPractices':None if cats.get('best-practices') is None else round(cats['best-practices']*100),'seo':None if cats.get('seo') is None else round(cats['seo']*100)}
    ev['lcp']=lcp; ev['tbt']=tbt; ev['cls']=cls
    ev['traceSummary']=str(evdir/'trace-summary.json') if (evdir/'trace-summary.json').exists() else None
    ev['reducedMotionVideo']=str(video_path) if video_path.exists() else None
    ev['reducedMotionProbe']=rm

    byid={p['id']:p for p in obj.get('principles',[])}
    perf=[]
    if lcp and lcp>2500: perf.append(mkfinding(site,'be-fast-and-stable','high' if lcp>4000 else 'medium',f'LCP above good threshold ({lcp/1000:.1f}s)',f'Lighthouse/trace recorded LCP {lcp:.0f}ms and TBT {tbt or 0:.0f}ms.','largest-contentful-paint'))
    if tbt and tbt>300: perf.append(mkfinding(site,'be-fast-and-stable','medium',f'Total blocking time is high ({tbt:.0f}ms)',f'Lighthouse/trace recorded TBT {tbt:.0f}ms.','total-blocking-time'))
    if cls and cls>0.1: perf.append(mkfinding(site,'be-fast-and-stable','high',f'CLS above good threshold ({cls:.2f})',f'Layout/Lighthouse recorded CLS {cls:.3f}.','cumulative-layout-shift'))
    if 'be-fast-and-stable' in byid: replace_ltv_findings(byid['be-fast-and-stable'],perf,{'largest-contentful-paint','total-blocking-time','cumulative-layout-shift'})

    bp=[]
    if cats.get('best-practices') is not None and cats.get('best-practices')<0.9: bp.append(mkfinding(site,'follow-best-practices','medium',f'Lighthouse best-practices score {cats["best-practices"]*100:.0f}','Lighthouse best-practices category is below 90.','lighthouse-best-practices'))
    if 'follow-best-practices' in byid: replace_ltv_findings(byid['follow-best-practices'],bp,{'lighthouse-best-practices'})

    seo=[]
    if cats.get('seo') is not None and cats.get('seo')<0.9: seo.append(mkfinding(site,'be-discoverable','medium',f'Lighthouse SEO score {cats["seo"]*100:.0f}','Lighthouse SEO category is below 90.','lighthouse-seo'))
    if 'be-discoverable' in byid:
        old=[f for f in byid['be-discoverable'].get('findings',[]) if f.get('checkId')!='lighthouse-seo']
        byid['be-discoverable']['findings']=old+seo
        if byid['be-discoverable']['findings']:
            byid['be-discoverable']['status']='issues'; byid['be-discoverable']['confidence']='high'; byid['be-discoverable']['summary']='; '.join(f['title'] for f in byid['be-discoverable']['findings'][:3])
        else:
            byid['be-discoverable']['status']='pass'; byid['be-discoverable']['confidence']='high'; byid['be-discoverable']['summary']='Discoverability and Lighthouse SEO evidence did not raise a finding.'

    if 'respect-user-preferences' in byid and byid['respect-user-preferences'].get('status')=='pass':
        anim=rm.get('animationCount')
        if isinstance(anim,int) and anim>10:
            f=mkfinding(site,'respect-user-preferences','low','Animations still present under reduced-motion',f'Reduced-motion probe found {anim} active animations; video artifact retained at {video_path}.','respects-reduced-motion')
            byid['respect-user-preferences']['status']='issues'; byid['respect-user-preferences']['confidence']='medium'; byid['respect-user-preferences']['findings']=[f]; byid['respect-user-preferences']['summary']=f['title']
        else:
            byid['respect-user-preferences']['confidence']='high' if isinstance(anim,int) else 'medium'
            byid['respect-user-preferences']['summary']='User preference probes including reduced-motion did not raise a finding.'

    obj.setdefault('_caveats',[]).append('Supplemental Lighthouse/trace/reduced-motion evidence collected and merged.')
    obj['overallScore']=rescore(obj)
    write(path,obj)
    return {'site':site,'ok':True,'lighthouse':ev['lighthouse'],'lcp':lcp,'tbt':tbt,'cls':cls,'reducedMotionAnimations':rm.get('animationCount')}

sites=[]
for p in sorted(RESULTS.glob('*.json')):
    if p.name.startswith(('batch-','pilot-','memory-','supplemental-')) or p.name.endswith('.error.json'): continue
    try:
        obj=load(p)
    except Exception: continue
    # Process all existing sites; skip if already has supplemental marker and lighthouse present.
    sites.append(p)
if LIMIT: sites=sites[:LIMIT]
results=[]
for p in sites:
    try: results.append(process(p))
    except Exception as e:
        print(f'[ltv] ERROR {p.name}: {e}', flush=True); results.append({'site':p.stem,'ok':False,'error':str(e)})
    write(RESULTS/f'{BATCH}.partial.json',results)
write(RESULTS/f'{BATCH}.json',results)
print(f'DONE {BATCH}: {len(results)}')
