---
name: state-of-the-web
description: |
  Run a web-uplift audit of the top 1000 sites and produce structured results.
  Collects CDP evidence (screenshots, Lighthouse, axe, heap, layout, discoverability)
  and judges 17 quality principles per site. Outputs per-site JSON + merged SQLite DB.
---

# State of the Web — batch audit skill

## When to use

When asked to audit, benchmark, or measure the quality of websites at scale. Produces structured per-site results suitable for aggregation and trend analysis.

## Setup

```bash
# Ensure prerequisites
node ~/.web-uplift/evidence/cli.mjs --help  # web-uplift installed
pip install tldextract                        # PSL for domain classification
curl -sS "https://tranco-list.eu/top-1m.csv.zip" -o /tmp/tranco.zip && unzip -o /tmp/tranco.zip -d /tmp/
```

## Running

### CDP evidence pass (automated, no vision)

```bash
python3 scripts/audit_runner2.py <site-list.txt> <start-index> <count>
```

- Outputs `results/cdp/results-batch-{start}.json`
- ~10-20s per site
- Collects: CLS, overflow, JS-shell detection, discoverability, screenshots

### Vision-based principle audit

See `AGENTS.md` for the full per-site methodology. Key steps per site:

1. Gather evidence (screenshot, layout, discoverability, HAR, trace, heap, axe, probes)
2. Review screenshots + metrics
3. Judge all 17 principles (pass/issues/n/a with findings)
4. Score the site (0-100)
5. Save to `results/gpt/{site}.json`

### Merge + build SQLite

```bash
python3 scripts/merge_results.py
# Merges results/cdp/ + results/gpt/ into state-of-the-web.db
```

## Output

- `results/cdp/` — CDP evidence (automated metrics)
- `results/gpt/` — Principle judgments (vision-based)
- `state-of-the-web.db` — Merged SQLite database
- `evidence/` — Screenshots and artifacts (not in git)

## Principles (17)

See `AGENTS.md` for the full list with descriptions and detection hints.

## Integration with web-uplift

This skill uses the [web-uplift](https://github.com/PaulKinlan/web-uplift) evidence primitives as its data-gathering layer. The web-uplift skill defines the 17 principles and the evidence CLI; this skill adds the batch runner, SQLite schema, and aggregation.
