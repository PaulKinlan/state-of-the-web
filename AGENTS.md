# Agents — State of the Web audit

This file instructs AI agents (Claude Code, Codex, Gemini, etc.) on how to run the State of the Web audit. It is read automatically by agents that look for AGENTS.md in the project root.

## Overview

Run web-uplift audits across the top 1,000 Tranco sites. Each site gets a structured JSON report with 17 principle outcomes, evidence metrics, and findings.

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

### Mode 2: Vision-based principle audit (requires vision-capable model)

Comprehensive — runs at ~2-5 min per site. Produces full 17-principle analysis.

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

Then review the evidence (screenshots, metrics) and judge all 17 principles.

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
  "principles": [
    {
      "id": "respect-user-preferences",
      "status": "pass|issues|not-applicable",
      "confidence": "high|medium|low",
      "summary": "One-line assessment",
      "findings": [
        {"id": "F-001", "severity": "serious", "title": "Short description", "evidence": "How we know"}
      ]
    }
  ],
  "verdict": "One-line overall assessment",
  "overallScore": 85
}
```

## The 17 principles

1. `respect-user-preferences` — color-scheme, reduced-motion, forced-colors
2. `implement-natural-interactions` — keyboard, focus, gestures, drag
3. `provide-guided-navigation` — wayfinding, breadcrumbs, progressive disclosure
4. `maximize-content-reduce-noise` — density, hierarchy, whitespace
5. `adapt-to-the-form-factor` — responsive, container queries, viewport, overflow
6. `support-core-task-success` — can the user complete the primary task?
7. `be-inclusive` — WCAG/axe, contrast, labels, landmarks
8. `be-fast-and-stable` — CWV (LCP/INP/CLS), TBT, long tasks
9. `be-discoverable` — SEO, meta, structured data, JS-shell detection
10. `be-private-and-secure` — HTTPS, HSTS, CSP, cookie flags
11. `be-resilient` — offline/PWA, no-JS fallback, service worker
12. `be-internationalised` — i18n, RTL, locale, charset
13. `be-trustworthy` — no dark patterns, honest UX
14. `be-sustainable` — transfer size, resource count, third-party weight
15. `be-agent-ready` — structured data, semantic HTML, crawlable
16. `follow-best-practices` — console errors, deprecated APIs, HTTPS images
17. `be-memory-efficient` — heap size, leak indicators

## Scoring guidance

- **pass**: No issues found for this principle. Evidence supports compliance.
- **issues**: One or more findings. Include severity (high/serious/moderate/low) and evidence.
- **not-applicable**: Principle doesn't apply to this site type (e.g., be-internationalised for a technical-spec site with no user-facing text).
- **confidence**: `high` = direct evidence (axe violation, Lighthouse score). `medium` = inferred from screenshots/metrics. `low` = couldn't verify (e.g., axe blocked by CSP).

## Important

- **Homepage only** for this study (not article/detail pages).
- **Don't fabricate evidence** — if you can't verify, mark confidence "low" or status "not-applicable".
- **Save incrementally** — write each site's JSON immediately after auditing, so partial progress survives.
- **Skip infrastructure domains** — CDN, ad-tech, DNS, analytics domains are not meaningful audit targets.
