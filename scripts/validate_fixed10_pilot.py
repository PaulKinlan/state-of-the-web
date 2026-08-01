#!/usr/bin/env python3
"""Fail-closed validation for the public fixed-10 pilot derivative."""
from __future__ import annotations
import argparse, hashlib, json, re, subprocess
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
import jsonschema
ROOT=Path(__file__).resolve().parents[1]
FORBIDDEN=re.compile(r"(?i)(/home/|/tmp/|file://|chrome[^\s\"']*profile|authorizationmessageid|relay[^\s\"']*id|(?:access|refresh|session)[_-]?token|api[_-]?key|bearer\s+[a-z0-9._-]+|[?&][a-z0-9._-]+=)")
SCORE_KEYS=re.compile(r"(?i)(score|rating|rank|passrate|pass_rate|percentage|percent)")

def canonical(v): return (json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False)+"\n").encode()
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def exact(obj,keys,label):
 if not isinstance(obj,dict) or set(obj)!=set(keys): raise ValueError(f"{label}: unknown or missing fields: {set(obj) ^ set(keys) if isinstance(obj,dict) else 'not-object'}")
def walk_keys(v):
 if isinstance(v,dict):
  for k,x in v.items(): yield k; yield from walk_keys(x)
 elif isinstance(v,list):
  for x in v: yield from walk_keys(x)
def origin_only(v):
 p=urlsplit(v)
 return p.scheme in {'http','https'} and bool(p.hostname) and not p.username and not p.password and p.path in {'','/'} and not p.query and not p.fragment and v.rstrip('/')==f"{p.scheme}://{p.netloc}"
def run(cmd): return subprocess.run(cmd,check=True,text=True,capture_output=True)
class StaticHTML(HTMLParser):
 def __init__(self): super().__init__(); self.tags=Counter(); self.headings=[]; self.attrs=[]
 def handle_starttag(self,tag,attrs):
  self.tags[tag]+=1; a=dict(attrs); self.attrs.append((tag,a))
  if re.fullmatch(r'h[1-6]',tag): self.headings.append(int(tag[1]))

def validate(root:Path)->dict:
 errors=[]; data=root/'data'; pilot_path=data/'pilot.json'
 try:
  pilot=json.loads(pilot_path.read_text()); schema=json.loads((data/'schema.json').read_text()); jsonschema.Draft202012Validator(schema).validate(pilot)
  if pilot_path.read_bytes()!=canonical(pilot): errors.append('pilot JSON is not canonical')
  if any(SCORE_KEYS.search(k) for k in walk_keys(pilot)): errors.append('score/rank/pass-rate field present')
  rows=pilot['rows']; exact(pilot['cohort'],['denominator','dispositions','noAutomaticRetry','selectionType'],'cohort')
  if pilot['cohort']['denominator']!=10 or len(rows)!=10: errors.append('denominator drift')
  if [r['ordinal'] for r in rows]!=list(range(1,11)) or len({r['origin'] for r in rows})!=10: errors.append('row identity drift')
  if Counter(r['disposition'] for r in rows)!=Counter({'runner-completed':2,'partial':6,'error':2}): errors.append('disposition drift')
  for r in rows:
   exact(r,['aggregates','archetype','coverage','disposition','journey','methodInvalid','ordinal','origin','reasonCode','reasonSummary'],f"row {r.get('ordinal')}")
   if not origin_only(r['origin']): errors.append(f"row {r['ordinal']}: non-origin URL")
   c=r['coverage']; exact(c,['blocked','duplicates','expected','judged','missing','notRun','recorded','unknown'],'coverage')
   if c['expected']!=58 or c['recorded']!=c['judged']+c['blocked']+c['notRun']+c['unknown'] or c['missing']!=58-(c['recorded']-c['duplicates']): errors.append(f"row {r['ordinal']}: invalid coverage arithmetic")
   exact(r['journey'],['actions','status'],'journey')
   for a in r['journey']['actions']: exact(a,['ordinal','outcome','reasonCode','type'],'action')
   if r['methodInvalid'] != (r['ordinal'] in {3,6}): errors.append('method-invalid marker drift')
   a=r['aggregates']
   if a is not None:
    exact(a,['cookieAttributeCounts','firstPartyOriginCount','httpStatusClassCounts','performance','requestCount','resourceTypes','securityHeaderPresence','thirdPartyOriginCount','transferBytes'],'aggregates')
    exact(a['cookieAttributeCounts'],['httpOnly','sameSiteLax','sameSiteNone','sameSiteStrict','sameSiteUnspecified','secure','thirdParty','total'],'cookie counts')
    exact(a['performance'],['firstContentfulPaintMs','largestContentfulPaintMs','loadEventEndMs','longTaskCount','longestTaskMs','totalBlockingTimeMs','traceDurationMs'],'performance')
    exact(a['securityHeaderPresence'],['content-security-policy','permissions-policy','referrer-policy','strict-transport-security','x-content-type-options','x-frame-options'],'header presence')
    for x in a['resourceTypes'].values(): exact(x,['count','transferBytes'],'resource type')
 except Exception as e: errors.append(f'schema/data validation: {e}')
 for path in sorted(root.rglob('*.json')):
  try:
   obj=json.loads(path.read_text())
   if path.read_bytes()!=canonical(obj): errors.append(f'{path.name}: non-canonical JSON')
  except Exception as e: errors.append(f'{path.name}: invalid JSON: {e}')
  text=path.read_text(errors='replace')
  if FORBIDDEN.search(text): errors.append(f'{path.name}: forbidden secret/path/query pattern')
 media_path=data/'media-manifest.json'
 try:
  media=json.loads(media_path.read_text()); exact(media,['omissions','receipts','reviewMethod','reviewScope','schemaVersion','transforms'],'media manifest')
  if len(media['receipts'])!=13: errors.append('media receipt count drift')
  for receipt in media['receipts']:
   common=['height','kind','mediaId','ocrReview','ordinal','outputBytes','outputFile','outputSha256','sourceBytes','sourceSha256','transformId','visualReview','width']
   video=['codec','decodeVerified','durationMs','frameCount','frameReview','metadataVerified','pixelFormat','playbackVerified','streamCount']
   exact(receipt,common+(video if receipt['kind']=='video' else []),'media receipt')
   f=root/receipt['outputFile']
   if not f.is_file() or sha(f)!=receipt['outputSha256'] or f.stat().st_size!=receipt['outputBytes']: errors.append(f"{receipt['mediaId']}: hash/size mismatch")
   if receipt['ocrReview']!='passed' or receipt['visualReview']!='passed': errors.append(f"{receipt['mediaId']}: review not passed")
   if receipt['kind']=='video':
    if receipt['outputBytes']>=receipt['sourceBytes'] or receipt['streamCount']!=1 or not all(receipt[x] for x in ['decodeVerified','metadataVerified','playbackVerified']): errors.append(f"{receipt['mediaId']}: invalid video receipt")
    try:
     probe=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-show_chapters','-of','json',str(f)]).stdout); streams=probe.get('streams',[])
     if len(streams)!=1 or streams[0].get('codec_type')!='video' or streams[0].get('codec_name')!='h264' or streams[0].get('pix_fmt')!='yuv420p' or probe.get('chapters'): errors.append(f"{receipt['mediaId']}: invalid final probe")
     run(['ffmpeg','-v','error','-i',str(f),'-f','null','-'])
    except Exception as e: errors.append(f"{receipt['mediaId']}: decode failed: {e}")
 except Exception as e: errors.append(f'media validation: {e}')
 try:
  manifest=json.loads((data/'public-manifest.json').read_text()); exact(manifest,['files','schemaVersion'],'public manifest')
  expected={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name!='public-manifest.json'}
  actual={x['path'] for x in manifest['files']}
  if expected!=actual: errors.append('public manifest inventory drift')
  for item in manifest['files']:
   exact(item,['bytes','mediaType','path','sha256'],'public file')
   p=root/item['path']
   if sha(p)!=item['sha256'] or p.stat().st_size!=item['bytes']: errors.append(f"manifest mismatch: {item['path']}")
 except Exception as e: errors.append(f'public manifest validation: {e}')
 try:
  text=(root/'index.html').read_text(); parser=StaticHTML(); parser.feed(text)
  for tag in ['header','nav','main','footer','h1','table','caption','figure','figcaption']:
   if not parser.tags[tag]: errors.append(f'HTML missing {tag}')
  if parser.tags['h1']!=1 or any(b>a+1 for a,b in zip(parser.headings,parser.headings[1:])): errors.append('invalid heading hierarchy')
  if not any(t=='a' and a.get('class')=='skip-link' and a.get('href')=='#content' for t,a in parser.attrs): errors.append('missing skip link')
  for t,a in parser.attrs:
   if t=='th' and a.get('scope') not in {'row','col'}: errors.append('table header missing scope')
   if t=='img' and not all(k in a for k in ['alt','width','height','loading']): errors.append('image missing accessible/lazy dimensions')
   if t=='video' and not all(k in a for k in ['controls','width','height']): errors.append('video missing controls/dimensions')
  css=(root/'styles.css').read_text()
  if 'prefers-reduced-motion' not in css or 'content-visibility:auto' not in css or 'contain-intrinsic-size' not in css: errors.append('CSS preference/defer guidance missing')
 except Exception as e: errors.append(f'HTML validation: {e}')
 forbidden_suffix={'.har','.log','.heapsnapshot'}
 if any(p.suffix in forbidden_suffix or p.name in {'flow-result.json','report.json','execution-permit.json','site-run.json'} for p in root.rglob('*') if p.is_file()): errors.append('raw private artifact published')
 if errors: raise ValueError('\n'.join(errors))
 return {'files':sum(p.is_file() for p in root.rglob('*')),'mediaReceipts':13,'rows':10}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,default=ROOT/'journey-pilot'); args=ap.parse_args()
 try: result=validate(args.root.resolve())
 except Exception as e: print(f'fixed10 validation failed:\n{e}'); return 1
 print(f"fixed10 validation passed: {result['rows']} rows, {result['mediaReceipts']} media receipts, {result['files']} public files"); return 0
if __name__=='__main__': raise SystemExit(main())
