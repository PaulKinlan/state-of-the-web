# State of the Web

A reproducible atomic audit inventory for 1,000 web origins against 17 modern-web principles and 58 authoritative checks from `principles.json`.

## Final bounded-run disposition

The run `2026-07-17T17-27-24-856Z` exhausted its retry queue on 28 July 2026. The exact fixed denominator is:

| Disposition | Origins | Meaning |
|---|---:|---|
| Coverage complete | **705** | All 58 checks have a judged outcome |
| Blocked after retries | **257** | All 58 checks remained blocked after at most three attempts |
| Partial after retries | **38** | Some checks were judged; remaining blocked/not-run rows stay explicit |
| Queued / retry eligible / invalid | **0** | The bounded run has no remaining work |
| **Total** | **1,000** | Every manifest origin appears exactly once |

Across all targets, the publication retains exactly **58,000 check rows**: 42,752 judged (16,521 pass, 20,799 issues, 5,432 not applicable), 15,209 blocked, and 39 not run. Blocked and partial outcomes are not scores, passes, or inferred not-applicable outcomes. Aggregate outcome observations use only the selection-biased 705-report complete subset.

Browse the [exact 1,000-target inventory](index.html), download the [machine-readable inventory](results/atomic/inventory.json), or read the [final run summary](checkpoint.html).

A separate [fixed-10 Web Uplift journey pilot](journey-pilot/) documents a sanitized operational convenience run and exposes all 580 site-check slots with their verdict, method, evidence summary, and failure or collection reason. It is not a ranking or replacement for this main report, and it publishes no scores or pass rates.

## Source and ordering

The source is the [Chrome UX Report global top list](https://github.com/zakird/crux-top-lists), repository commit `650c9d833e0de62ef004b827b02be3aaef1eedd3`. The manifest contains all 1,000 unique origins in the CrUX `rank=1000` bucket, preserving source-file order. CrUX does not publish exact ordering within that bucket, so the published `position` is provenance, not an exact popularity rank.

- Manifest: [`results/atomic/manifest.csv`](results/atomic/manifest.csv)
- Manifest SHA-256: `af3d02a5a1466181c5900104795e25cd8d3838375702520260cdaede5078791d`
- Catalog: `modern-web-guidance@0.0.172`
- Catalog SHA-256: `78ccfdb2d483f4c57d9dafed80fd86c6265585a56457c8dcfddc254b80fb44d7`
- Retry budget: at most three report-bearing attempts per origin

## Published artifacts

```text
results/atomic/
├── inventory.json       # exact 1,000-target disposition and provenance index
├── manifest.csv         # immutable source inventory
├── manifest.sha256
├── run.json             # finished run metadata
├── retry-status.json    # final retry counters (records live in inventory.json)
└── reports/             # 1,000 byte-identical retained report JSON files
sites/                   # 1,000 static per-target pages
principles/              # 17 complete-subset/check-total pages
atomic-checkpoint.json   # concise final run summary
checkpoint.html          # human-readable final run summary
```

Each inventory target records its canonical report SHA-256, original local report path, and local evidence root. The canonical reports are committed because they are the structured result. Raw screenshots, HARs, traces, heap snapshots, videos, and other browser evidence remain locally retained under:

```text
runs/2026-07-17T17-27-24-856Z/atomic-reports/<slug>/<attempt>/
```

Those passive artifacts are approximately 33 GB and are intentionally not committed. Artifact paths inside each report are relative to the target's recorded `evidenceRoot`.

## Reconcile and validate

The reconciler copies the retained report selected by the final retry status, verifies it against the exact catalog used by the run, generates the canonical inventory/reports, and rebuilds the static site.

```bash
python3 scripts/reconcile_atomic_run.py \
  runs/2026-07-17T17-27-24-856Z \
  --catalog /home/paulkinlan/web-uplift/knowledge/principles.json

python3 scripts/build_atomic_db.py
python3 scripts/validate_atomic_publication.py --check-local-evidence
python3 -m unittest scripts/test_reconcile_atomic_run.py
```

`--check-local-evidence` is the full local publication gate: it requires each retained source report to match its canonical SHA-256 and every declared artifact to exist at its exact path beneath the recorded `evidenceRoot`. Omit the flag only in a publication-only clone where the intentionally uncommitted run tree is unavailable.

The existing per-report validator is intentionally fail-closed for incomplete reports. Across the canonical report set, its expected result is exactly 705 exit-zero reports and 295 exit-one reports. Every exit-one report must be one of the published 257 blocked or 38 partial dispositions; an incomplete report must never pass the publication gate or carry a score.

```bash
node scripts/validate_atomic_report.mjs principles.json \
  results/atomic/reports/0001-lectormangass_net.json
```

The publication validator independently checks:

- manifest/catalog SHA-256 and exact 1,000-origin denominator;
- one unique canonical report and static page per manifest position;
- all 58 catalog pairs and 17 derived principle outcomes per report;
- evidence/path/finding references and literal coverage counters;
- with `--check-local-evidence`, exact source-report bytes and physical artifact paths beneath every retained evidence root;
- exact 705 / 257 / 38 dispositions and zero queue/retry/invalid counts;
- exactly 58,000 database test rows, 17,000 principle rows, and zero scores.

## Modern-web feature evidence (Chrome 134+)

`scripts/probes/modern-web-features.js` is the first-party probe for the catalog
checks that need declarative-platform signals: `view-transitions`,
`scroll-driven-animations`, `anchored-positioning`, `scroll-state-aware-chrome`,
and `physical-gestures`. It reports **usage and browser support only, never a
verdict**, so an auditor judges those checks from measured evidence instead of
guessing from source:

```bash
node ~/.web-uplift/evidence/cli.mjs evaluate https://example.com/ \
  --wait 3000 --expr-file scripts/probes/modern-web-features.js \
  --out evidence/example.com/modern-web-features.json
```

For a manifest or site list — resumable, and it fails closed when a target
produces no usable evidence (an exit-zero run that landed on Chrome's error page
is a failure, not evidence):

```bash
python3 scripts/modern_web_probe.py results/atomic/manifest.csv --out runs/<run>/evidence/modern-web
```

Detection is **value-aware**: a declaration counts only when its value actually
enables the feature. Matching bare property names is wrong in both directions,
and both directions were observed on live origins:

- **Opt-outs are not usage.** `view-transition-name: none` and `position-anchor:
  unset` *disable* the feature. `microsoft.com` ships an `all: unset`-style reset
  (`.sa-modern-cta-button { … position-anchor: unset; position-area: unset; … }`)
  that a name-matching probe reported as anchor-positioning adoption.
- **Ordinary shorthands are not usage.** The CSSOM expands `animation: pulse 2s
  infinite` into `animation-timeline: auto` and `container: card / inline-size`
  into `container-type: inline-size`, so name matching would have counted every
  animated page as scroll-driven.

Filtered values are still reported, under each family's `optedOut`, so an auditor
can tell *"does not use the feature"* apart from *"explicitly turned it off"* —
a distinction the catalog needs to choose between `not-applicable` and `issues`.

Crawler suite: `python3 -m unittest scripts.test_modern_web_probe`. It drives real
headless Chrome against `scripts/fixtures/modern-web-features.html` (asserting
every family is detected, including `::view-transition`, `scroll-state()` and a
live `viewTimeline`), `scripts/fixtures/plain-page.html` (asserting ordinary CSS
produces no false positive), and `scripts/fixtures/opted-out-features.html`
(asserting opt-outs and shorthand expansion are not credited as usage). The
profile-cleanup test spawns a real process and asserts it is gone, because a
mocked `subprocess.run` cannot detect a cleanup command that does not work.

Probed against real origins, the signal is complementary by design: `airbnb.com`
shows genuine adoption (88 `view-transition-name` declarations with real values,
10 anchor-positioning declarations), `scroll-driven-animations.style` reports 19
live view timelines, and `stripe.com` reports a live `ScrollTimeline` from **zero**
readable stylesheets. `microsoft.com` reports **no** modern-feature usage: its 30
tracked declarations are all inert defaults or resets, which is what value-aware
matching is for.

Known limitation: page JavaScript cannot read cross-origin stylesheets, so the
probe reports them as `css.sheets.inaccessible` with their URLs (CDN-hosted CSS on
`web.dev`, `developer.chrome.com` and `stripe.com` is unreadable from the page).
The live `animations` counts and a HAR with bodies are the cross-checks; reading
that CSS directly needs the CDP `CSS.getStyleSheetText` domain, which belongs in
the web-uplift evidence CLI rather than in this repo.

## Methodology and limitations

- Audits use the [web-uplift](https://github.com/PaulKinlan/web-uplift) atomic-check methodology with representative routes, states, and active interactions where reachable.
- A coverage-complete report means every check has a judged outcome; it does **not** mean the site passed every check.
- Completion is correlated with whether an origin permits meaningful headless inspection. The 705-report complete subset is therefore selection-biased.
- Results are point-in-time observations under the recorded routes and conditions.
- Authenticated, destructive, sensitive, or unavailable flows remain limited as documented in each report.

The older homepage-oriented CDP and principle-level files remain in `results/cdp/` and `results/gpt/` for history, but they are not merged into, scored with, or presented as the final atomic dataset.

## License

MIT
