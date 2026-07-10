#!/usr/bin/env python3
"""Re-audit screenshot-visible principles from existing State of the Web evidence.

No browser capture. Uses existing desktop/mobile screenshots referenced by per-site JSON files.
Writes:
  - reaudit-trustworthy.json
  - reaudit-guided-nav.json
"""
import base64
import concurrent.futures as cf
import datetime as dt
import glob
import io
import json
import os
import re
import time
from pathlib import Path

import requests
from PIL import Image, ImageOps

RESULTS = Path('/home/paulkinlan/state-of-the-web/results/gpt')
API_KEY = os.environ.get('GEMINI_API_KEY')
MODEL = os.environ.get('REAUDIT_GEMINI_MODEL', 'gemini-2.5-flash')
URL = f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={API_KEY}'
NOW = dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')

TRUST_ID = 'be-trustworthy'
NAV_ID = 'provide-guided-navigation'

SYSTEM_PROMPT = """You are re-auditing modern web principles from existing screenshots only.
Do not assume interactions or hidden page states. Judge only what is visible in the supplied desktop and mobile screenshots.
Return strict JSON only.

Principle: be-trustworthy
Judge visible trust and anti-pattern issues: deceptive or overwhelming ads, fake download/play buttons, dark-pattern cookie/consent walls, obstructive modals, manipulative urgency/scarcity claims, disguised ads, unclear sponsorship, security/privacy confidence problems visible in page chrome, or adult/gambling/commerce patterns that obscure intent. Do not penalize adult content merely for being adult; only flag deception, unsafe presentation, consent coercion, or misleading UI.

Principle: provide-guided-navigation
Judge visible wayfinding: clear primary nav, breadcrumbs or section context where appropriate, search/menu affordances, active/selected state, content hierarchy, obvious path to major sections/tasks, mobile nav clarity. Flag when navigation is absent, hidden/ambiguous, dominated by ads/overlays, visually confusing, or gives poor orientation.

Statuses:
- pass: screenshot gives enough visible evidence and no issue is apparent.
- issues: visible evidence shows a clear problem.
- not-applicable: screenshot is blocked/blank/error/consent-only or does not reveal enough to judge that principle.
Confidence should be high only for obvious visible evidence; medium for reasonable visual judgement; low for partial/obstructed screenshots.
Each finding needs severity (low/medium/high/critical), title, evidence, and suggestedFix.
"""

USER_TEMPLATE = """Site: {site}\nRank: {rank}\nURL: {url}\nFinal URL: {final_url}\n\nAudit the two principles from these existing screenshots. Respond with exactly this JSON shape:\n{{\n  \"be-trustworthy\": {{\"status\": \"pass|issues|not-applicable\", \"confidence\": \"low|medium|high\", \"summary\": \"...\", \"findings\": [{{\"severity\": \"low|medium|high|critical\", \"title\": \"...\", \"evidence\": \"...\", \"suggestedFix\": \"...\"}}]}},\n  \"provide-guided-navigation\": {{\"status\": \"pass|issues|not-applicable\", \"confidence\": \"low|medium|high\", \"summary\": \"...\", \"findings\": [{{\"severity\": \"low|medium|high|critical\", \"title\": \"...\", \"evidence\": \"...\", \"suggestedFix\": \"...\"}}]}}\n}}"""


def load_site_jsons():
    sites = {}
    for p in glob.glob(str(RESULTS / '*.json')):
        name = Path(p).name
        if name.startswith(('batch-', 'memory-', 'methodology-', 'reaudit-')):
            continue
        try:
            obj = json.loads(Path(p).read_text())
        except Exception:
            continue
        if not (isinstance(obj, dict) and obj.get('site') and obj.get('principles')):
            continue
        ss = (((obj.get('evidence') or {}).get('screenshot')) or '')
        if not ss or not Path(ss).exists():
            # Try canonical evidence dir fallback.
            art = Path(obj.get('_artifactsDir') or '')
            cand = art / 'screenshot.png'
            if cand.exists():
                ss = str(cand)
        if not ss or not Path(ss).exists():
            continue
        sites[obj['site']] = obj
    return [sites[k] for k in sorted(sites)]


def find_mobile(desktop_path):
    p = Path(desktop_path)
    cand = p.with_name('mobile.png')
    return str(cand) if cand.exists() else None


def image_part(path, label):
    im = Image.open(path).convert('RGB')
    im = ImageOps.exif_transpose(im)
    # Keep the visual information but control payload size.
    max_w = 1100 if label == 'desktop' else 600
    if im.width > max_w:
        h = int(im.height * (max_w / im.width))
        im = im.resize((max_w, h), Image.Resampling.LANCZOS)
    # Cap very tall screenshots; current evidence is viewport-sized, this is a guard.
    max_h = 1200
    if im.height > max_h:
        im = im.crop((0, 0, im.width, max_h))
    buf = io.BytesIO()
    im.save(buf, format='JPEG', quality=78, optimize=True)
    return {
        'inline_data': {
            'mime_type': 'image/jpeg',
            'data': base64.b64encode(buf.getvalue()).decode('ascii')
        }
    }


def normalise_principle(site, principle_id, raw):
    if not isinstance(raw, dict):
        raw = {}
    status = raw.get('status') if raw.get('status') in {'pass','issues','not-applicable'} else 'not-applicable'
    confidence = raw.get('confidence') if raw.get('confidence') in {'low','medium','high'} else 'low'
    summary = str(raw.get('summary') or 'No usable visual judgement returned.')[:600]
    findings = []
    for i, f in enumerate(raw.get('findings') or []):
        if not isinstance(f, dict):
            continue
        sev = f.get('severity') if f.get('severity') in {'low','medium','high','critical'} else 'medium'
        findings.append({
            'id': f'{site}-{principle_id}-{i+1:02d}',
            'severity': sev,
            'title': str(f.get('title') or 'Visible issue')[:180],
            'evidence': str(f.get('evidence') or summary)[:800],
            'suggestedFix': str(f.get('suggestedFix') or 'Review the visible UI pattern and align it with the principle.')[:800],
        })
    if status == 'issues' and not findings:
        findings.append({
            'id': f'{site}-{principle_id}-01',
            'severity': 'medium',
            'title': summary[:180],
            'evidence': summary,
            'suggestedFix': 'Review the visible UI pattern and align it with the principle.',
        })
    if status != 'issues':
        findings = []
    return {
        'id': principle_id,
        'status': status,
        'confidence': confidence,
        'summary': summary,
        'findings': findings,
    }


def parse_json_text(text):
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r'\{.*\}', text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def audit_one(obj, idx, total):
    site = obj['site']
    ev = obj.get('evidence') or {}
    desktop = ev.get('screenshot') or str(Path(obj.get('_artifactsDir','')) / 'screenshot.png')
    mobile = find_mobile(desktop)
    parts = [
        {'text': SYSTEM_PROMPT},
        {'text': USER_TEMPLATE.format(site=site, rank=obj.get('rank'), url=obj.get('url'), final_url=obj.get('finalUrl'))},
        {'text': 'Desktop screenshot follows.'},
        image_part(desktop, 'desktop'),
    ]
    if mobile:
        parts.extend([{'text': 'Mobile screenshot follows.'}, image_part(mobile, 'mobile')])

    payload = {
        'contents': [{'role': 'user', 'parts': parts}],
        'generationConfig': {
            'temperature': 0.1,
            'response_mime_type': 'application/json',
        },
    }
    last_err = None
    for attempt in range(4):
        try:
            r = requests.post(URL, json=payload, timeout=90)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt + 1)
                continue
            r.raise_for_status()
            data = r.json()
            text = data['candidates'][0]['content']['parts'][0]['text']
            raw = parse_json_text(text)
            trust = normalise_principle(site, TRUST_ID, raw.get(TRUST_ID))
            nav = normalise_principle(site, NAV_ID, raw.get(NAV_ID))
            base = {
                'site': site,
                'rank': obj.get('rank'),
                'auditedAt': NOW,
                'url': obj.get('url'),
                'finalUrl': obj.get('finalUrl'),
                'sourceEvidence': {
                    'desktopScreenshot': desktop,
                    'mobileScreenshot': mobile,
                    'existingAuditFile': str(RESULTS / f'{site}.json'),
                },
                'method': 'vision re-audit from existing desktop/mobile screenshots only; no recapture or interaction',
            }
            print(f'[{idx}/{total}] {site}: trust={trust["status"]}/{trust["confidence"]} nav={nav["status"]}/{nav["confidence"]}', flush=True)
            return {**base, 'principle': trust}, {**base, 'principle': nav}
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt + 1)
    print(f'[{idx}/{total}] {site}: ERROR {last_err}', flush=True)
    err_pr_trust = normalise_principle(site, TRUST_ID, {'status':'not-applicable','confidence':'low','summary':f'Vision re-audit failed: {last_err}','findings':[]})
    err_pr_nav = normalise_principle(site, NAV_ID, {'status':'not-applicable','confidence':'low','summary':f'Vision re-audit failed: {last_err}','findings':[]})
    base = {
        'site': site, 'rank': obj.get('rank'), 'auditedAt': NOW, 'url': obj.get('url'), 'finalUrl': obj.get('finalUrl'),
        'sourceEvidence': {'desktopScreenshot': desktop, 'mobileScreenshot': mobile, 'existingAuditFile': str(RESULTS / f'{site}.json')},
        'method': 'vision re-audit from existing desktop/mobile screenshots only; no recapture or interaction',
        'error': str(last_err),
    }
    return {**base, 'principle': err_pr_trust}, {**base, 'principle': err_pr_nav}


def summarise(rows):
    out = {}
    for r in rows:
        p = r['principle']
        k = (p['status'], p['confidence'])
        out[k] = out.get(k, 0) + 1
    return {'total': len(rows), 'byStatusConfidence': {f'{k[0]}/{k[1]}': v for k, v in sorted(out.items())}}


def main():
    if not API_KEY:
        raise SystemExit('GEMINI_API_KEY is required')
    sites = load_site_jsons()
    print(f'Loaded {len(sites)} site JSONs with screenshots', flush=True)
    trust_rows, nav_rows = [], []
    max_workers = int(os.environ.get('REAUDIT_WORKERS', '4'))
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(audit_one, obj, i+1, len(sites)) for i, obj in enumerate(sites)]
        for fut in cf.as_completed(futs):
            tr, nr = fut.result()
            trust_rows.append(tr); nav_rows.append(nr)
            # incremental checkpoints
            trust_rows.sort(key=lambda x: (x.get('rank') is None, x.get('rank') or 10**9, x['site']))
            nav_rows.sort(key=lambda x: (x.get('rank') is None, x.get('rank') or 10**9, x['site']))
            (RESULTS / 'reaudit-trustworthy.partial.json').write_text(json.dumps({'summary': summarise(trust_rows), 'results': trust_rows}, indent=2))
            (RESULTS / 'reaudit-guided-nav.partial.json').write_text(json.dumps({'summary': summarise(nav_rows), 'results': nav_rows}, indent=2))
    trust_rows.sort(key=lambda x: (x.get('rank') is None, x.get('rank') or 10**9, x['site']))
    nav_rows.sort(key=lambda x: (x.get('rank') is None, x.get('rank') or 10**9, x['site']))
    (RESULTS / 'reaudit-trustworthy.json').write_text(json.dumps({'summary': summarise(trust_rows), 'results': trust_rows}, indent=2))
    (RESULTS / 'reaudit-guided-nav.json').write_text(json.dumps({'summary': summarise(nav_rows), 'results': nav_rows}, indent=2))
    print('Wrote reaudit-trustworthy.json and reaudit-guided-nav.json', flush=True)
    print(json.dumps({'trustworthy': summarise(trust_rows), 'guidedNav': summarise(nav_rows)}, indent=2), flush=True)

if __name__ == '__main__':
    main()
