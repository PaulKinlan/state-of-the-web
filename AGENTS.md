# Agents — State of the Web audit

This file instructs AI agents (Claude Code, Codex, Gemini, etc.) on how to run the State of the Web audit. It is read automatically by agents that look for AGENTS.md in the project root.

## Overview

Run web-uplift audits across the top 1,000 Tranco sites. Each site gets a structured JSON report covering every authoritative `(principleId, checkId)` pair from `principles.json`, plus derived principle outcomes, evidence metrics, and findings. The catalog currently contains 17 principles and 58 checks; always derive those counts from the file.

## Prerequisites

1. **web-uplift installed**: `npm install -g web-uplift` or vendored at `~/.web-uplift/`
2. **Evidence CLI**: `node ~/.web-uplift/evidence/cli.mjs <primitive> <url> [options]`
3. **Python 3.12+** with `tldextract` (`pip install tldextract`)
4. **System Chrome** at `/usr/bin/google-chrome-stable` (headless, driven via CDP)
5. **Tranco top 1000** at `/tmp/tranco-top1000.txt`

## Two audit modes

### Mode 1: CDP evidence pass (automated, fast, no vision)

Scalable — runs at ~10-20s per site. Collects objective metrics without principle judgments.

```bash
python3 scripts/audit_runner2.py <site-list> <start-index> <count>
```

Collects per site:
- Layout metrics (CLS, long tasks, scroll/client width, horizontal overflow)
- Discoverability (JS-shell detection, content coverage %, crawler vs rendered comparison)
- Screenshots (desktop + mobile)
- Viewport meta presence

**Does NOT collect**: Lighthouse, axe, heap, HAR, traces, or principle judgments.

Output: `results/cdp/results-batch-{start}.json`

### Mode 2: Agentic atomic-check audit (requires a vision-capable model)

Potentially comprehensive, but only when the atomic coverage validator passes. Produces one explicit outcome for every check in `principles.json`; a 17-row principle summary alone is incomplete.

For each site, gather evidence via the web-uplift skill:
```bash
node ~/.web-uplift/evidence/cli.mjs screenshot <url> --viewport 1280x900 --out evidence/<site>/desktop.png
node ~/.web-uplift/evidence/cli.mjs screenshot <url> --viewport 390x844 --out evidence/<site>/mobile.png
node ~/.web-uplift/evidence/cli.mjs layout <url> --viewport 1280x900
node ~/.web-uplift/evidence/cli.mjs layout <url> --viewport 390x844
node ~/.web-uplift/evidence/cli.mjs discoverability <url>
node ~/.web-uplift/evidence/cli.mjs har <url>
node ~/.web-uplift/evidence/cli.mjs trace <url>
node ~/.web-uplift/evidence/cli.mjs heap <url>
node ~/.web-uplift/evidence/cli.mjs evaluate <url> --expr "<axe injection>"
```

Then materialise the exact check manifest from `principles.json`, gather the check-specific evidence (including active interactions and representative routes where required), record every check outcome, derive the 17 principle outcomes, and run the coverage validator. Do not use a generic evidence bundle to default untested checks to pass.

## Output schema

Each site produces one JSON file at `results/gpt/{site}.json`:

```json
{
  "site": "example.com",
  "rank": 42,
  "auditedAt": "2026-07-09T14:00:00Z",
  "url": "https://www.example.com/",
  "finalUrl": "https://www.example.com/",
  "httpStatus": 200,
  "evidence": {
    "screenshot": "evidence/example.com/desktop.png",
    "lighthouse": {"performance": 95, "accessibility": 90, "bestPractices": 100, "seo": 92},
    "axeViolations": 3,
    "heapSize": 12500000,
    "cls": 0.02,
    "lcp": 1200,
    "inp": 150,
    "isJsShell": false,
    "textChars": 15000,
    "hasViewport": true,
    "hasMetaDescription": true,
    "httpsOnly": true,
    "hsts": true
  },
  "coverage": {
    "catalogVersion": "from principles.json",
    "catalogChecksum": "sha256:...",
    "expected": 58,
    "recorded": 58,
    "judged": 58,
    "blocked": 0,
    "notRun": 0,
    "missing": 0,
    "unknown": 0,
    "duplicates": 0,
    "complete": true
  },
  "checkOutcomes": [
    {
      "principleId": "respect-user-preferences",
      "checkId": "respects-color-scheme",
      "status": "pass|issues|not-applicable|opted-out|blocked|not-run",
      "confidence": "high|medium|low",
      "method": "Exact active, visual, or objective method used or attempted",
      "evidence": "Exact metric, interaction, screenshot, selector, or exception",
      "pathIds": ["homepage-dark"],
      "artifacts": ["evidence/dark.png"],
      "findingIds": []
    }
  ],
  "principleOutcomes": [
    {
      "principleId": "respect-user-preferences",
      "expectation": "default",
      "status": "pass|issues|incomplete|not-applicable|opted-out",
      "findingIds": []
    }
  ],
  "verdict": "One-line overall assessment",
  "overallScore": 85
}
```

## Authoritative principles and checks

Do not maintain a prose approximation here. Read `principles.json` at runtime and use its exact principle IDs, check IDs, applicability criteria, evidence hints, guidance references, catalog version, and checksum. Hand-written summaries drifted from the catalog and contributed to incomplete coverage.

## Scoring guidance

- **check pass**: Direct evidence supports that specific check. “No issue found” and absence of a finding are not evidence.
- **check issues**: Direct evidence shows the check failed. Include finding IDs, severity, and evidence.
- **check not-applicable**: The check genuinely does not apply, with a check-specific rationale. It never means untested.
- **check blocked/not-run**: Execution was prevented or did not happen. Both make the derived principle `incomplete` and the site audit `partial`.
- **confidence**: `high` = direct evidence (axe violation, Lighthouse score). `medium` = supported visual/model judgement. `low` describes evidence quality only; it cannot convert an unexecuted check into pass.
- **tests are mandatory**: use the exact 58 check IDs vendored in `principles.json` (the authoritative web-uplift principle catalog). Every principle must retain one result for every defined check. Do not replace those checks with a weaker generic screenshot review or invent substitute IDs.
- **missing execution is explicit**: use `blocked` or `not-run` when execution was impossible; never turn missing evidence into a pass or a vague “pending testing method”. The denominator remains every applicable defined check for every site.
- **principle status is derived from tests**: any test with `issues` makes the principle `issues`; all applicable measured tests passing makes it `pass`; `not-applicable` is only valid when the principle or test genuinely does not apply. A mix containing `blocked`/`not-run` derives `incomplete`, never pass.
- **validation is a publication gate**: run `node scripts/validate_atomic_report.mjs principles.json <report.json>` before import, scoring, aggregation, or publication. Missing, unknown, duplicate, blocked, or not-run checks keep the report partial and unscored.

## Important

- **Representative paths are required**: start from the ranked URL, then exercise the routes, states, overlays, and user flows needed by the checks. If scope or access prevents that, record `blocked`/`not-run` and keep the site partial; do not downgrade the method to homepage-only while claiming a complete audit.
- **Don't fabricate evidence** — low confidence is not permission to pass. Untested means `not-run`; inability to execute after an attempt means `blocked`; `not-applicable` is only for genuine applicability decisions.
- **Save incrementally** — write each site's JSON immediately after auditing, so partial progress survives.
- **Keep the exact 1,000-site denominator**: CDN, ad-tech, DNS, analytics, blocked, failed, and non-homepage domains remain in the manifest. Classify their applicability and audit status honestly; never silently skip them.
