# Agents — State of the Web audit

This file instructs AI agents (Claude Code, Codex, Gemini, etc.) on how to run the State of the Web audit. It is read automatically by agents that look for AGENTS.md in the project root.

## Overview

Run web-uplift audits across an immutable 1,000-origin manifest. Each site gets a structured JSON report covering every authoritative `(principleId, checkId)` pair from `principles.json`, plus derived principle outcomes, evidence metrics, and findings. The published bounded run uses all 1,000 unique origins in the CrUX global `rank=1000` bucket, preserving source-file order (not an exact within-bucket popularity rank). The catalog currently contains 17 principles and 58 checks; always derive those counts from the file.

## Prerequisites

1. **web-uplift installed**: `npm install -g web-uplift` or vendored at `~/.web-uplift/`
2. **Evidence CLI**: `node ~/.web-uplift/evidence/cli.mjs <primitive> <url> [options]`
3. **Python 3.12+** with `tldextract` (`pip install tldextract`)
4. **System Chrome** at `/usr/bin/google-chrome-stable` (headless, driven via CDP)
5. **Immutable source manifest** for the run (the final published copy is `results/atomic/manifest.csv`)

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
- Modern-web feature signals for the Chrome 134+ checks — view transitions,
  scroll-driven animations, anchored positioning, scroll-state-aware chrome and
  platform gestures — from `scripts/probes/modern-web-features.js`, retained per
  site at `evidence/<domain>/modern-web-features.json`. A probe that cannot
  measure the page records `ok: false` with the reason instead of implying the
  features are absent.

**Does NOT collect**: Lighthouse, axe, heap, HAR, traces, or principle judgments.

The probe runs inside the Mode 1 pass (bead state-of-the-web-if6), not as a
separate step someone has to remember: `scripts/modern_web_probe.py` remains
available for targeted or manifest-wide collection, and the same probe expression
is the per-route evidence command in Mode 2 below.

Output: `results-batch-{start}.json` in the working directory, plus per-site
probe evidence at `evidence/<domain>/modern-web-features.json`. The tracked
`results/cdp/results-batch-*.json` files are the retained v1 batch output; the
runner does not write there, so a fresh pass cannot overwrite published records.

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
node ~/.web-uplift/evidence/cli.mjs evaluate <url> --expr-file scripts/probes/modern-web-features.js
```

The last command is the [modern-web feature probe](scripts/probes/modern-web-features.js):
it measures declarative view transitions, scroll-driven animations, anchor
positioning, scroll-state container queries and platform gestures — the evidence
`view-transitions`, `scroll-driven-animations`, `anchored-positioning`,
`scroll-state-aware-chrome` and `physical-gestures` are judged from. Record its
output for every representative route, and treat `css.sheets.inaccessible` (a
cross-origin stylesheet whose text the page cannot read) as partial CSS evidence,
not as absence of the feature.

Detection is **value-aware** (`matching: "value-aware"` in the report): a
property counts only when its value enables the feature. A list value is judged
per comma-separated part, `@view-transition` is judged by its `navigation`
descriptor rather than its existence, and a function name inside a string
literal is text, not a call.

Each family reports three buckets:

- `used` / `usedCount` — values that actually enable the feature. This is the
  adoption signal.
- `optedOut` — inert values the author appears to have **written**
  (`view-transition-name: none`, `position-anchor: unset`,
  `@view-transition { navigation: none }`).
- `inertDefaults` — inert values the CSSOM **synthesised** from a shorthand
  (`animation:` produces `animation-timeline: auto`; `container:` produces
  `container-type: inline-size`). The author never wrote these.

When judging, read `used`/`usedCount` for adoption. Treat `optedOut` as a hint
worth reading, **not as proof of intent**: it is derived from CSSOM
serialisation, and a rule mixing a shorthand with longhand overrides expands
every longhand into its text, so untouched longhands there look authored. Never
turn the `optedOut` bucket alone into a verdict, and never read a family's
absence as proof when `css.sheets.inaccessible` is non-zero — that is partial CSS
evidence, and the honest outcome is bounded to what was readable.

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

## Final publication

The finished bounded run is reconciled into `results/atomic/`, 1,000 static pages in `sites/`, and 17 principle pages. Raw browser evidence remains in the local run tree and is not committed. Rebuild and validate with:

```bash
python3 scripts/reconcile_atomic_run.py <run-dir> --catalog <exact-run-catalog.json>
python3 scripts/build_atomic_db.py
python3 scripts/validate_atomic_publication.py --check-local-evidence
```

The final inventory is unscored and must preserve complete, exhausted-blocked, and exhausted-partial dispositions exactly.

## Important

- **Representative paths are required**: start from the ranked URL, then exercise the routes, states, overlays, and user flows needed by the checks. If scope or access prevents that, record `blocked`/`not-run` and keep the site partial; do not downgrade the method to homepage-only while claiming a complete audit.
- **Don't fabricate evidence** — low confidence is not permission to pass. Untested means `not-run`; inability to execute after an attempt means `blocked`; `not-applicable` is only for genuine applicability decisions.
- **Save incrementally** — write each site's JSON immediately after auditing, so partial progress survives.
- **Keep the exact 1,000-site denominator**: CDN, ad-tech, DNS, analytics, blocked, failed, and non-homepage domains remain in the manifest. Classify their applicability and audit status honestly; never silently skip them.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:970c3bf2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   bd dolt push
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

<!-- BEGIN BEADS CODEX SETUP: generated by bd setup codex -->
## Beads Issue Tracker

Use Beads (`bd`) for durable task tracking in repositories that include it. Use the `beads` skill at `.agents/skills/beads/SKILL.md` (project install) or `~/.agents/skills/beads/SKILL.md` (global install) for Beads workflow guidance, then use the `bd` CLI for issue operations.

### Quick Reference

```bash
bd ready                # Find available work
bd show <id>            # View issue details
bd update <id> --claim  # Claim work
bd close <id>           # Complete work
bd prime                # Refresh Beads context
```

### Rules

- Use `bd` for all task tracking; do not create markdown TODO lists.
- Run `bd prime` when Beads context is missing or stale. Codex 0.129.0+ can load Beads context automatically through native hooks; use `/hooks` to inspect or toggle them.
- Keep persistent project memory in Beads via `bd remember`; do not create ad hoc memory files.

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.
<!-- END BEADS CODEX SETUP -->
