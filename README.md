# State of the Web

An automated audit of the top 1,000 websites using the [web-uplift](https://github.com/PaulKinlan/web-uplift) methodology. Measures the web's health across 17 modern quality principles — from performance and accessibility to privacy, resilience, and UX.

## What this is

This project is auditing the homepages of the top 1,000 Tranco sites (2026) against 17 principles and 58 authoritative checks from the vendored `principles.json`. The current dataset is incomplete: 499 sites have broad principle-level judgements, but the earlier run did not retain an outcome for every check. Those judgements are useful evidence, not proof of complete 58-check coverage. Evidence was collected in two modes:

1. **CDP evidence pass** (automated, scalable): CLS, horizontal overflow, JS-shell detection, discoverability, layout metrics. Covers 870/1000 sites.
2. **Vision-based principle analysis** (AI agent with screenshot review): broad pass/issues/not-applicable judgements and findings for 499 sites. This predates the atomic-test schema and therefore does not establish that all checks passed.

## Results

| Metric | Finding |
|---|---|
| JS shells (invisible to crawlers) | 6.8% of sites |
| High CLS (>0.1, fails Core Web Vitals) | 4.9% |
| Horizontal overflow on desktop | 2.3% |
| Sites blocking headless audit | 2.9% |

Notable: Wikipedia is the gold standard (score 100, CLS 0, 6 requests). Amazon is one of the worst (score 8, discoverability 1%, JS shell, 10 principle issues). Most top sites are clean.

## Directory structure

```
state-of-the-web/
├── README.md                 — this file
├── AGENTS.md                 — instructions for AI agents running audits
├── principles.json           — authoritative 17-principle / 58-check catalog
├── principles/               — generated per-principle result pages
├── schemas/
│   ├── schema.sql            — SQLite schema, including atomic test results
│   └── schema.example.json   — example per-site JSON output
├── scripts/
│   ├── audit_runner2.py      — CDP evidence batch runner (automated metrics)
│   ├── run_gpt_batch.py      — Vision-based principle audit runner (gpt-5.5)
│   └── batch-001-sites.tsv   — site list for batch 001
├── results/
│   ├── cdp/                  — CDP evidence results (870 sites, JSON)
│   └── gpt/                  — Vision-based principle results (per-site JSON)
├── evidence/                 — Screenshots and artifacts (not in git — too large)
└── state-of-the-web.db       — Merged SQLite database (regenerated from JSON)
```

## How to run

### CDP evidence pass (fast, scalable, no vision needed)
```bash
# Audit 50 sites starting at rank 0
python3 scripts/audit_runner2.py /tmp/tranco-top1000.txt 0 50

# Results saved to results/cdp/results-batch-{start}.json
```

### Vision-based principle audit (requires a vision-capable AI agent)
See `AGENTS.md` and `.web-uplift/skill/SKILL.md` for the full methodology.

### Merge results into SQLite
```bash
python3 scripts/merge_results.py
# Produces state-of-the-web.db
```

## Methodology

- **17 principles / 58 checks**: the exact IDs, applicability criteria, evidence hints, and guidance references are vendored in `principles.json`. New reports must retain pass/issues/N/A/blocked/not-run for every defined check; broad principle summaries are derived, not substitutes.
- **CDP evidence**: Raw Chrome DevTools Protocol via [web-uplift evidence primitives](https://github.com/PaulKinlan/web-uplift) — screenshots, Lighthouse, axe-core, heap snapshots, layout/CLS, discoverability, HAR, performance traces.
- **Domain classification**: Public Suffix List (tldextract) for proper registrable-domain handling.
- **SPA detection**: Pages with <300 chars of visible text in raw HTML flagged as JavaScript-rendered shells.

## Limitations

- Homepage-only (not article/detail pages)
- Single point-in-time snapshot per site
- Headless Chrome — some sites block automated access (2.9%)
- Lighthouse/INP not available for all sites (CSP may block injection)
- The legacy principle-level run does not contain a complete per-site 58-check matrix. Principle pages expose this as `not run`; missing evidence is not counted as a test pass.

## License

MIT

## Links

- [Live data explorer](https://paulkinlan.github.io/are-links-dying/) (external link study using the same methodology)
- [web-uplift skill](https://github.com/PaulKinlan/web-uplift)
- [Tranco list](https://tranco-list.eu)
