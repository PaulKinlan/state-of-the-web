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
FORBIDDEN=re.compile(r"(?ix)(file://|(?<![A-Za-z0-9])/(?:home|tmp|var|etc|usr|opt|srv|private|root|mnt|run|proc|dev|sys|data|Users|Volumes)(?:/|\\)|(?:[A-Za-z]:\\|\\\\)[^\s\"']+|chrome[^\s\"']*profile|authorizationmessageid|relay[^\s\"']*id|(?:access|refresh|session)[_-]?token|api[_-]?key|bearer\s+[a-z0-9._~+/=-]+|[?&][a-z0-9._~-]+=)")
PRIVATE_NARRATIVE_FORBIDDEN=re.compile(
 r"(?ix)("
 r"(?:file|https?)://|"
 r"(?<![A-Za-z0-9])/(?:home|tmp|var|etc|usr|opt|srv|private|root|mnt|run|proc|dev|sys|data|Users|Volumes)(?:/|\\)[^\s\"']*|"
 r"(?<![A-Za-z0-9])/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~!$&'()*+,;=:@%-]*)+(?:[?#][^\s]*)?|"
 r"(?:[A-Za-z]:\\|\\\\)[^\s\"']+|"
 r"\b(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?:/[^\s]*)|"
 r"\b(?:set-cookie|cookie|authorization|proxy-authorization|content-security-policy|strict-transport-security|x-content-type-options|x-frame-options|referrer-policy|permissions-policy|headers?|request[ _-]?body|response[ _-]?body)\s*[:=]|"
 r"\b(?:basic|bearer)\s+[A-Za-z0-9+/._~=-]+|"
 r"\b(?:access|refresh|session|auth|id)?[_-]?token\s*[:=]\s*[^\s,;]+|"
 r"\b(?:api[_ -]?key|secret|password|passwd|credential)\s*[:=]\s*[^\s,;]+|"
 r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b|"
 r"\b(?:chrome|browser|user)[ _-]?profile(?:[ _-]?(?:id|name))?\s*[:=]?\s*[A-Za-z0-9._\\/-]+|"
 r"\b(?:report|artifact|flow-result|execution-permit|site-run)[A-Za-z0-9._-]*\.(?:json|har|log|html?|txt|zip)\b|"
 r"\bcookie(?:[ _-](?:name|identifier))\s*[:=]?\s*[A-Za-z0-9_-]+|"
 r"\b(?:nfvdid|optanonconsent)\b|"
 r"[?&][A-Za-z0-9._~-]+(?:=|\b)"
 r")"
)
SCORE_KEYS=re.compile(r"(?i)(score|rating|rank|passrate|pass_rate|percentage|percent)")

def canonical(v): return (json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False)+"\n").encode()
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def catalog_contract(path):
 value=json.loads(path.read_text()); principles=value.get('principles')
 if not isinstance(principles,list): raise ValueError('pinned principle catalog missing')
 pairs=[]; summaries=[]
 for principle in principles:
  pid=principle.get('id'); title=principle.get('title'); checks=principle.get('checks')
  if not isinstance(pid,str) or not isinstance(title,str) or not isinstance(checks,list): raise ValueError('pinned principle catalog shape drift')
  summaries.append({'checkCount':len(checks),'principleId':pid,'principleTitle':title})
  for check in checks:
   cid=check.get('id')
   if not isinstance(cid,str): raise ValueError('pinned check identity drift')
   pairs.append((pid,cid))
 if len(summaries)!=17 or len(pairs)!=58 or len(set(pairs))!=58: raise ValueError('pinned catalog denominator drift')
 return summaries,pairs

def private_narrative_safe(value):
 return isinstance(value,str) and not PRIVATE_NARRATIVE_FORBIDDEN.search(value)

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
 try:
  catalog_path=ROOT/'principles.json'; expected_principles,expected_pairs=catalog_contract(catalog_path)
  provenance=json.loads((data/'provenance.json').read_text())
  if provenance.get('source',{}).get('catalogSha256')!=sha(catalog_path): errors.append('pinned catalog checksum drift')
  checks_path=data/'check-outcomes.json'; checks=json.loads(checks_path.read_text()); checks_schema=json.loads((data/'check-outcomes.schema.json').read_text())
  jsonschema.Draft202012Validator(checks_schema).validate(checks)
  if checks_path.read_bytes()!=canonical(checks): errors.append('check outcomes JSON is not canonical')
  exact(checks,['catalog','datasetId','schemaVersion','sites','totals'],'check outcomes')
  exact(checks['catalog'],['checkCountPerSite','principleCountPerSite','principles','siteCount','totalSlots'],'check catalog')
  exact(checks['totals'],['blocked','issues','not-applicable','not-run','pass','unavailable'],'check totals')
  required_totals={'pass':174,'issues':147,'not-applicable':68,'blocked':73,'not-run':2,'unavailable':116}
  if checks['totals']!=required_totals: errors.append('atomic display totals drift')
  if checks['catalog']['totalSlots']!=580 or sum(p['checkCount'] for p in checks['catalog']['principles'])!=58: errors.append('atomic catalog denominator drift')
  if checks['catalog']['principles']!=expected_principles: errors.append('atomic catalog principle identity/order drift')
  sites=checks['sites']
  if [site['ordinal'] for site in sites]!=list(range(1,11)): errors.append('atomic site ordinal drift')
  display_counts=Counter(); method_invalid=[]
  for site in sites:
   exact(site,['disposition','methodInvalid','ordinal','origin','outcomes','reportState'],f"check site {site.get('ordinal')}")
   if not origin_only(site['origin']): errors.append(f"check site {site['ordinal']}: non-origin URL")
   expected_state='available' if site['ordinal']>=3 else ('missing-report' if site['ordinal']==1 else 'runner-error')
   if site['reportState']!=expected_state: errors.append(f"check site {site['ordinal']}: report-state drift")
   pairs=[]
   for outcome in site['outcomes']:
    exact(outcome,['checkId','checkSummary','confidence','detectableVia','displayStatus','evidence','findings','guides','method','methodInvalid','principleId','principleTitle','reason','sourceStatus'],'check outcome')
    exact(outcome['evidence'],['availability','summary','types'],'check evidence')
    pair=(outcome['principleId'],outcome['checkId']); pairs.append(pair); display_counts[outcome['displayStatus']]+=1
    if outcome['displayStatus']=='issues' and not outcome['findings']: errors.append(f"site {site['ordinal']} {pair}: issue lacks sanitized finding")
    if outcome['displayStatus']!='issues' and outcome['findings']: errors.append(f"site {site['ordinal']} {pair}: non-issue has finding")
    if outcome['displayStatus'] in {'blocked','not-run','not-applicable','unavailable'} and not outcome['reason']: errors.append(f"site {site['ordinal']} {pair}: incomplete status lacks reason")
    if outcome['displayStatus']=='unavailable':
     if outcome['sourceStatus']!=expected_state or outcome['confidence'] is not None or outcome['evidence']['availability']!='unavailable': errors.append(f"site {site['ordinal']} {pair}: unavailable provenance drift")
    elif outcome['sourceStatus']!=outcome['displayStatus']: errors.append(f"site {site['ordinal']} {pair}: source/display status drift")
    if outcome['methodInvalid']: method_invalid.append((site['ordinal'],outcome['principleId'],outcome['checkId']))
    narratives=[outcome['method'],outcome['evidence']['summary']]
    if outcome['reason'] is not None: narratives.append(outcome['reason'])
    for finding in outcome['findings']:
     exact(finding,['findingId','severity','summary'],'sanitized finding'); narratives.append(finding['summary'])
    if not all(private_narrative_safe(value) for value in narratives): errors.append(f"site {site['ordinal']} {pair}: private narrative pattern")
   if pairs!=expected_pairs: errors.append(f"check site {site['ordinal']}: pinned catalog order/identity drift")
  if dict(display_counts)!=required_totals: errors.append(f'atomic recomputed totals drift: {display_counts}')
  expected_invalid=[(3,'follow-best-practices','no-console-errors'),(6,'follow-best-practices','no-console-errors')]
  if method_invalid!=expected_invalid: errors.append(f'method-invalid outcome drift: {method_invalid}')
 except Exception as e: errors.append(f'atomic check validation: {e}')
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
  for tag in ['header','nav','main','footer','h1','table','caption','figure','figcaption','form','label','select','details','summary','script']:
   if not parser.tags[tag]: errors.append(f'HTML missing {tag}')
  if parser.tags['h1']!=1 or any(b>a+1 for a,b in zip(parser.headings,parser.headings[1:])): errors.append('invalid heading hierarchy')
  if not any(t=='a' and a.get('class')=='skip-link' and a.get('href')=='#content' for t,a in parser.attrs): errors.append('missing skip link')
  check_rows=[a for t,a in parser.attrs if t=='tr' and 'check-row' in a.get('class','').split()]
  if len(check_rows)!=580: errors.append(f'HTML atomic row count drift: {len(check_rows)}')
  if not all(all(key in a for key in ['data-site','data-principle','data-status']) for a in check_rows): errors.append('HTML check row filter identity missing')
  select_ids={a.get('id') for t,a in parser.attrs if t=='select'}
  if select_ids!={'site-filter','principle-filter','status-filter'}: errors.append('HTML evidence filters missing')
  if not any(t=='p' and a.get('id')=='filter-count' and a.get('role')=='status' and a.get('aria-live')=='polite' for t,a in parser.attrs): errors.append('HTML filter live status missing')
  for t,a in parser.attrs:
   if t=='th' and a.get('scope') not in {'row','col'}: errors.append('table header missing scope')
   if t=='img' and not all(k in a for k in ['alt','width','height','loading']): errors.append('image missing accessible/lazy dimensions')
   if t=='video' and not all(k in a for k in ['controls','width','height']): errors.append('video missing controls/dimensions')
  css=(root/'styles.css').read_text()
  if 'prefers-reduced-motion' not in css or 'content-visibility:auto' not in css or 'contain-intrinsic-size' not in css or 'forced-colors' not in css: errors.append('CSS preference/defer guidance missing')
  script=(root/'explorer.js').read_text()
  if any(token in script for token in ['fetch(', 'XMLHttpRequest', 'WebSocket', 'localStorage', 'sessionStorage', 'document.cookie']): errors.append('explorer script has network/storage surface')
  if 'rows.length !== 580' not in script: errors.append('explorer script lacks denominator gate')
 except Exception as e: errors.append(f'HTML validation: {e}')
 forbidden_suffix={'.har','.log','.heapsnapshot'}
 if any(p.suffix in forbidden_suffix or p.name in {'flow-result.json','report.json','execution-permit.json','site-run.json'} for p in root.rglob('*') if p.is_file()): errors.append('raw private artifact published')
 if errors: raise ValueError('\n'.join(errors))
 return {'atomicSlots':580,'files':sum(p.is_file() for p in root.rglob('*')),'mediaReceipts':13,'rows':10}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,default=ROOT/'journey-pilot'); args=ap.parse_args()
 try: result=validate(args.root.resolve())
 except Exception as e: print(f'fixed10 validation failed:\n{e}'); return 1
 print(f"fixed10 validation passed: {result['rows']} rows, {result['atomicSlots']} site-check slots, {result['mediaReceipts']} media receipts, {result['files']} public files"); return 0
if __name__=='__main__': raise SystemExit(main())
