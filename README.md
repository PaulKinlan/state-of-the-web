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
python3 scripts/validate_atomic_publication.py
python3 -m unittest scripts/test_reconcile_atomic_run.py
```

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
- exact 705 / 257 / 38 dispositions and zero queue/retry/invalid counts;
- exactly 58,000 database test rows, 17,000 principle rows, and zero scores.

## Methodology and limitations

- Audits use the [web-uplift](https://github.com/PaulKinlan/web-uplift) atomic-check methodology with representative routes, states, and active interactions where reachable.
- A coverage-complete report means every check has a judged outcome; it does **not** mean the site passed every check.
- Completion is correlated with whether an origin permits meaningful headless inspection. The 705-report complete subset is therefore selection-biased.
- Results are point-in-time observations under the recorded routes and conditions.
- Authenticated, destructive, sensitive, or unavailable flows remain limited as documented in each report.

The older homepage-oriented CDP and principle-level files remain in `results/cdp/` and `results/gpt/` for history, but they are not merged into, scored with, or presented as the final atomic dataset.

## License

MIT
