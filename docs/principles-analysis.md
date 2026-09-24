# Principles catalog analysis

`principles.json` declares `appliesAnalysis: "docs/principles-analysis.md"`. This
is that document — the catalog's own provenance and coverage record. It states
what the catalog pins, how each of its checks points at Modern Web Guidance,
which guides nothing points at, and what changing the catalog costs. Every number
below is produced by a command in this file; run
`python3 scripts/analyze_guide_coverage.py --check docs/principles-analysis.md` to
verify the generated section against the catalog in the working tree.

## What is pinned

- Catalog: `modern-web-guidance@0.0.172`, checksum of the `principles.json` bytes
  (the value every published report records as its `coverage.catalogChecksum`).
- Structure: 17 principles, 58 checks, every `(principleId, checkId)` pair
  authoritative.
- Published bounded run: `2026-07-17T17-27-24-856Z`, 1,000 origins — 705 coverage
  complete, 257 blocked after retries, 38 partial after retries, 58,000 check
  rows. The run's reports are validated against the catalog bytes pinned above,
  never against a moving file; see [Changing the catalog](#changing-the-catalog-guide-only-edits-are-not-free).

```bash
python3 -c "import hashlib,pathlib;print('sha256:'+hashlib.sha256(pathlib.Path('principles.json').read_bytes()).hexdigest())"
```

## How the catalog references guidance

Each check's `guides` array holds two different things, and conflating them
produces wrong counts:

- **Guide slugs** — single tokens naming a Markdown guide shipped by the package
  (for example `state-aware-sticky-headers`). These are resolvable: they can be
  checked against a Modern Web Guidance pack.
- **Search phrases** — multi-word retrieval queries, exactly one per check (for
  example `scroll-state query scrolled sticky header`). They are instructions for
  the auditor's search step, not references to a document, and nothing can verify
  them.

The split is mechanical: a single token matching `^[a-z0-9]+(-[a-z0-9]+)*$` is a
slug, anything containing a space is a phrase. Against the 0.0.190 pack every one
of the 137 slugs resolves, and no slug is ambiguous.

The guides themselves are **not vendored in this repository**. To resolve or
diff them, extract the package:

```bash
npm pack modern-web-guidance@0.0.172 && mkdir -p /tmp/mwg/0.0.172 && tar xzf modern-web-guidance-0.0.172.tgz -C /tmp/mwg/0.0.172
npm pack modern-web-guidance@0.0.190 && mkdir -p /tmp/mwg/0.0.190 && tar xzf modern-web-guidance-0.0.190.tgz -C /tmp/mwg/0.0.190
```

<!-- BEGIN GENERATED guide-coverage -->

<!-- guide-coverage-baseline
{
  "appliesAnalysis": "docs/principles-analysis.md",
  "catalogChecksum": "sha256:78ccfdb2d483f4c57d9dafed80fd86c6265585a56457c8dcfddc254b80fb44d7",
  "catalogVersion": "modern-web-guidance@0.0.172",
  "checks": 58,
  "checksWithoutReferencedSlug": [
    "adapt-to-the-form-factor/input-modality-aware",
    "be-discoverable/canonical-and-indexing-signals",
    "be-discoverable/crawlable-and-mobile-friendly",
    "be-discoverable/structured-and-shareable-metadata",
    "be-discoverable/title-and-description",
    "be-inclusive/sufficient-contrast",
    "be-memory-efficient/bounded-footprint",
    "be-memory-efficient/no-leak-under-repeated-interaction",
    "be-resilient/offline-and-installable",
    "follow-best-practices/no-console-errors"
  ],
  "latestGuides": 147,
  "latestPackVersion": "0.0.190",
  "latestUnreferenced": [
    "contrast-color",
    "ime-safe-enter-submit",
    "out-of-order-html-streaming",
    "progress-ring",
    "prompt-api",
    "sanitize-untrusted-html",
    "scrollspy",
    "spinner",
    "state-aware-sticky-headers",
    "usage-aware-component-variations"
  ],
  "newGuides": [
    "contrast-color",
    "custom-button-actions",
    "ime-safe-enter-submit",
    "out-of-order-html-streaming",
    "progress-ring",
    "prompt-api",
    "sanitize-untrusted-html",
    "scrollspy",
    "spinner",
    "state-aware-sticky-headers",
    "usage-aware-component-variations"
  ],
  "pinnedGuides": 137,
  "pinnedPackVersion": "0.0.172",
  "pinnedUnreferenced": [
    "declarative-button-actions"
  ],
  "principles": 17,
  "referencedAbsentFromLatest": [],
  "referencedAbsentFromPinned": [
    "custom-button-actions"
  ],
  "referencedSlugs": 137,
  "removedFromPinned": [
    "declarative-button-actions"
  ],
  "searchPhrases": 58
}
-->

## Catalog-side facts

- Catalog pin: `modern-web-guidance@0.0.172`
- Catalog checksum (`sha256` of `principles.json` bytes): `sha256:78ccfdb2d483f4c57d9dafed80fd86c6265585a56457c8dcfddc254b80fb44d7`
- Principles: **17** · checks: **58**
- Distinct guide slugs referenced: **137**
- Free-text Modern Web Guidance search phrases: **58** (one per check)
- Checks that reference no guide slug: **10**

## Package-side facts

- Guides shipped in the pinned pack: **137**
- Guides shipped in the latest pack: **147**
- New guides in the latest pack: **11**
- Guides removed since the pinned pack: **1** (`declarative-button-actions`)
- New guides no check references: **10**
- Referenced slugs absent from the **pinned** pack (forward references): **1** (`custom-button-actions`)
- Referenced slugs absent from the latest pack: **0**

- New in the latest pack **and already referenced** by the pinned catalog: `custom-button-actions`

## Coverage map

| Principle | Check | Guides referenced |
| --- | --- | --- |
| `respect-user-preferences` | `respects-color-scheme` | `dark-mode`, `component-specific-light-dark-theme` |
| `respect-user-preferences` | `respects-reduced-motion` | `accessibility` |
| `respect-user-preferences` | `respects-contrast` | `adapt-scrollbar-to-contrast-preferences` |
| `implement-natural-interactions` | `view-transitions` | `same-document-transitions`, `cross-document-transitions`, `group-element-transitions`, `faster-spa-view-transitions` |
| `implement-natural-interactions` | `scroll-driven-animations` | `scrollytelling`, `parallax-scroll-effects`, `scroll-entry-exit-effects`, `carousel-slide-effects` |
| `implement-natural-interactions` | `physical-gestures` | `physics-based-easing`, `individual-transform-properties`, `animate-element-entry-exit`, `animate-to-from-top-layer`, `animate-to-intrinsic-sizes`, `dynamic-sibling-animations`, `interactive-content-reveal`, `pull-to-reveal`, `swipe-to-remove` |
| `provide-guided-navigation` | `scroll-state-aware-chrome` | `shrinking-header-on-scroll`, `scroll-progress-indicator`, `scroll-position-aware-elements`, `scroll-snap-realtime-feedback`, `scroll-snap-state-sync`, `scroll-target-on-load`, `soft-edge-content-fade`, `scrollability-affordance-hints` |
| `provide-guided-navigation` | `anchored-positioning` | `anchor-positioning-tab-underline`, `position-aware-tooltips`, `interest-triggered-tooltips`, `interest-triggered-action-previews` |
| `provide-guided-navigation` | `directs-attention` | `directional-navigation-transitions`, `carousel-snap-highlights`, `navigation-drawer`, `stack-drill-down`, `persistent-app-tours`, `persistent-toast-notifications` |
| `maximize-content-reduce-noise` | `no-intrusive-interruptions` | `light-dismiss-a-dialog`, `platform-controls-dismiss-dialog` |
| `maximize-content-reduce-noise` | `semantic-dismissible-primitives` | `declarative-dialog-popover-control`, `animated-select-picker`, `branded-select-styling`, `brand-consistent-forms`, `custom-select-picker-layouts`, `rich-media-picker` |
| `maximize-content-reduce-noise` | `reduced-chrome` | `complex-shapes`, `shaped-cutouts`, `overflow-clipping-control`, `visually-texture-content`, `apply-webgl-shaders`, `interactive-content-in-3d-scenes`, `highlight-text-ranges`, `prevent-text-wrapping`, `customize-scrollbar-color-and-thickness`, `export-html-media-from-canvas` |
| `adapt-to-the-form-factor` | `responsive-no-horizontal-scroll` | `fluid-scaling`, `calculate-with-intrinsic-sizes`, `css-layout` |
| `adapt-to-the-form-factor` | `component-level-responsiveness` | `size-aware-styling`, `content-based-styling`, `child-state-based-styling`, `design-token-reactivity`, `dynamic-sibling-styling`, `form-fields-automatically-fit-contents` |
| `adapt-to-the-form-factor` | `input-modality-aware` | — (search phrase only) |
| `support-core-task-success` | `clear-purpose-and-primary-action` | `improve-text-layout-and-legibility`, `scrollability-affordance-hints` |
| `support-core-task-success` | `primary-flow-completion` | `forms`, `custom-button-actions`, `persistent-toast-notifications` |
| `support-core-task-success` | `clear-system-state-and-recovery` | `accessible-error-announcement`, `required-field-feedback`, `validate-input-after-interaction`, `persistent-toast-notifications` |
| `be-fast-and-stable` | `good-core-web-vitals` | `identify-inp-causes`, `schedule-tasks-by-priority`, `optimize-preload-priority`, `improve-next-page-load-performance`, `interactions-in-complex-layouts`, `performance` |
| `be-fast-and-stable` | `visual-stability` | `visually-stable-font-fallbacks` |
| `be-fast-and-stable` | `efficient-main-thread` | `break-up-long-tasks`, `identify-heavy-scripts`, `optimize-script-priority`, `defer-rendering-heavy-content`, `defer-work-until-scroll-ends` |
| `be-fast-and-stable` | `efficient-resource-delivery` | `optimize-image-priority`, `optimize-preload-priority`, `optimize-script-priority`, `visually-stable-font-fallbacks` |
| `be-fast-and-stable` | `trim-unused-and-duplicate-code` | `identify-heavy-scripts`, `conditional-async-dependencies`, `defer-rendering-heavy-content` |
| `be-inclusive` | `names-roles-labels` | `accessibility`, `expose-canvas-content-to-browser-features` |
| `be-inclusive` | `sufficient-contrast` | — (search phrase only) |
| `be-inclusive` | `structure-and-focus` | `move-dom-element-without-losing-state` |
| `be-inclusive` | `legible-text` | `improve-text-layout-and-legibility`, `precise-text-alignment`, `visually-stable-mixed-fonts` |
| `be-inclusive` | `zoom-reflow-targets-and-media` | `accessibility`, `fluid-scaling`, `calculate-with-intrinsic-sizes` |
| `follow-best-practices` | `no-console-errors` | — (search phrase only) |
| `follow-best-practices` | `sound-document-and-assets` | `css`, `html`, `reduce-style-repetition` |
| `follow-best-practices` | `browser-platform-hygiene` | `html`, `security` |
| `be-discoverable` | `title-and-description` | — (search phrase only) |
| `be-discoverable` | `crawlable-and-mobile-friendly` | — (search phrase only) |
| `be-discoverable` | `canonical-and-indexing-signals` | — (search phrase only) |
| `be-discoverable` | `structured-and-shareable-metadata` | — (search phrase only) |
| `be-private-and-secure` | `secure-transport-and-headers` | `security` |
| `be-private-and-secure` | `data-minimisation-and-third-parties` | `privacy`, `batch-analytics-events`, `full-session-analytics`, `calculate-total-foreground-time` |
| `be-private-and-secure` | `in-context-permissions-and-modern-auth` | `passkeys`, `passkey-registration`, `passkey-authentication`, `passkey-reauthentication`, `passkey-conditional-create`, `passkey-management` |
| `be-private-and-secure` | `defensive-browser-policies` | `security`, `privacy` |
| `be-resilient` | `progressive-enhancement` | `flicker-free-client-side-ab-testing`, `consistent-cross-document-transitions`, `stabilize-reactive-state` |
| `be-resilient` | `resilient-runtime-behaviour` | `resilient-context-menus-and-nested-dropdowns`, `move-dom-element-without-losing-state`, `persistent-top-layer-ui`, `detect-initial-visibility-state`, `conditional-async-dependencies`, `sequence-distributed-events` |
| `be-resilient` | `offline-and-installable` | — (search phrase only) |
| `be-resilient` | `network-and-http-failure-states` | `persistent-toast-notifications`, `detect-initial-visibility-state`, `conditional-async-dependencies` |
| `be-internationalised` | `lang-dir-and-logical-properties` | `translator`, `language-detection` |
| `be-internationalised` | `locale-aware-data` | `support-global-calendar-systems`, `capture-location-agnostic-data`, `format-human-readable-durations`, `manage-recurring-intervals`, `calculate-event-differentials` |
| `be-internationalised` | `time-zone-correctness` | `coordinate-global-events`, `model-partial-time-concepts` |
| `be-trustworthy` | `no-dark-patterns` | `custom-button-actions`, `search-hidden-content`, `swipe-to-remove` |
| `be-trustworthy` | `humane-error-handling` | `forms`, `validate-input-after-interaction`, `required-field-feedback`, `select-menu-interaction`, `accessible-error-announcement`, `style-parent-with-has` |
| `be-trustworthy` | `trustworthy-input-assistance` | `autofill-address-form`, `autofill-payment-form`, `autofill-sign-in-form`, `autofill-sign-up-form`, `autofill-highlight-inputs` |
| `be-trustworthy` | `safe-commercial-and-account-flows` | `autofill-payment-form`, `autofill-sign-in-form`, `passkey-reauthentication`, `passkey-management` |
| `be-sustainable` | `optimised-assets` | `deliver-optimized-decorative-images`, `optimize-image-priority`, `resolution-optimized-pseudo-elements` |
| `be-sustainable` | `no-wasteful-work` | `deprioritize-background-fetches`, `efficient-background-processing` |
| `be-sustainable` | `third-party-and-media-budget` | `deliver-optimized-decorative-images`, `deprioritize-background-fetches`, `efficient-background-processing` |
| `be-agent-ready` | `structured-agent-capabilities` | `webmcp`, `agentic-forms`, `agentic-javascript-tools` |
| `be-agent-ready` | `on-device-inference` | `language-model`, `summarizer` |
| `be-memory-efficient` | `no-leak-under-repeated-interaction` | — (search phrase only) |
| `be-memory-efficient` | `bounded-footprint` | — (search phrase only) |
| `be-memory-efficient` | `no-detached-dom-or-unbounded-listeners` | `manage-recurring-intervals` |

Checks carrying only a search phrase (no resolvable guide slug):

- `adapt-to-the-form-factor/input-modality-aware`
- `be-discoverable/canonical-and-indexing-signals`
- `be-discoverable/crawlable-and-mobile-friendly`
- `be-discoverable/structured-and-shareable-metadata`
- `be-discoverable/title-and-description`
- `be-inclusive/sufficient-contrast`
- `be-memory-efficient/bounded-footprint`
- `be-memory-efficient/no-leak-under-repeated-interaction`
- `be-resilient/offline-and-installable`
- `follow-best-practices/no-console-errors`

Guides in the latest pack that no check references:

- `contrast-color`
- `ime-safe-enter-submit`
- `out-of-order-html-streaming`
- `progress-ring`
- `prompt-api`
- `sanitize-untrusted-html`
- `scrollspy`
- `spinner`
- `state-aware-sticky-headers`
- `usage-aware-component-variations`

<!-- END GENERATED guide-coverage -->

## What 0.0.190 changes

### The delta is 11 new guides, not 10

The bead that triggered this analysis listed ten new guides in 0.0.190. The packs
show **eleven**: the pinned pack ships 137 guides, the latest ships 147, and
`custom-button-actions` is missing from that list. That guide matters, because the
pinned catalog already references it — see below.

### The pinned catalog contains a forward reference

`custom-button-actions` is referenced by two checks:

- `support-core-task-success/primary-flow-completion`
- `be-trustworthy/no-dark-patterns`

It does **not** exist in the 0.0.172 pack; it first appears in 0.0.190. So the
statement "all referenced slugs still exist in 0.0.190, nothing is dangling" is
true, but incomplete: at the pinned version itself, those two check references
cannot resolve. The pin is behind what the catalog already assumes, which is a
reason to move the pin that is independent of adopting the ten unreferenced
guides.

This is recorded as `referencedAbsentFromPinned` in the generated block above, so
it is a checked number rather than a claim in prose: `--check` fails if it stops
being true, or if it silently becomes true of some other guide.

Verification:

```bash
find /tmp/mwg/0.0.172/package -path '*guides*' -name 'custom-button-actions.md'   # nothing
find /tmp/mwg/0.0.190/package -path '*guides*' -name 'custom-button-actions.md'   # found
```

### Ten new guides no check points at, with proposed homes

Proposals, not decisions: they connect each guide to the check whose summary it
actually serves. Nothing here should be applied before the migration question in
[Changing the catalog](#changing-the-catalog-guide-only-edits-are-not-free) is
settled.

| New guide (category) | What it covers | Proposed check |
| --- | --- | --- |
| `state-aware-sticky-headers` (ui-atoms) | Sticky UI reacting to scroll state instead of JS toggling | `provide-guided-navigation/scroll-state-aware-chrome` — near-verbatim match |
| `scrollspy` (ui-components) | Scroll-position navigation highlighting | `provide-guided-navigation/directs-attention` |
| `prompt-api` (built-in-ai) | On-device language model (Gemini Nano) | `be-agent-ready/on-device-inference`; strongest new-check candidate |
| `sanitize-untrusted-html` (security) | Sanitising untrusted HTML before insertion | `be-private-and-secure/defensive-browser-policies`; new-check candidate |
| `out-of-order-html-streaming` (performance) | OOO streaming / declarative partial updates | `be-fast-and-stable/efficient-resource-delivery` |
| `contrast-color` (visual-design) | `contrast-color()` for unpredictable backgrounds | `be-inclusive/sufficient-contrast`, which currently has no slug at all |
| `ime-safe-enter-submit` (forms) | Enter-to-submit that does not fire mid-IME-composition | `be-trustworthy/humane-error-handling`, and arguably `be-internationalised` |
| `progress-ring` (ui-components) | Determinate progress indicator | `support-core-task-success/clear-system-state-and-recovery` |
| `spinner` (ui-components) | Indeterminate loading indicator | `support-core-task-success/clear-system-state-and-recovery` |
| `usage-aware-component-variations` (css) | `@container style()` semantic component adaptation | `adapt-to-the-form-factor/component-level-responsiveness` |

Two of these are also candidates for **new checks** rather than new references, and
the published run supplies the evidence for that argument: `prompt-api` maps to
`be-agent-ready/on-device-inference`, whose 1,000 published outcomes are **0 pass,
4 issues, 737 not-applicable and 259 blocked** — either the check is not measuring
what the platform now offers, or the guide arrived after the run.
`sanitize-untrusted-html` has no equivalent check at all today. Both are blocked on
the same migration decision as every other catalog change.

Reproduce that breakdown from the published reports:

```bash
python3 - <<'PY'
import json, glob, collections
c = collections.Counter()
for path in glob.glob('results/atomic/reports/*.json'):
    for row in json.load(open(path)).get('checkOutcomes', []):
        if row.get('principleId') == 'be-agent-ready' and row.get('checkId') == 'on-device-inference':
            c[row['status']] += 1
print(dict(c), sum(c.values()))
PY
# {'not-applicable': 737, 'blocked': 259, 'issues': 4} 1000
```

### Ten checks reference no guide slug

These checks carry only a search phrase, so an auditor following the catalog's
slug references has nothing to read for them: `adapt-to-the-form-factor/input-modality-aware`,
`be-inclusive/sufficient-contrast`, `follow-best-practices/no-console-errors`,
`be-discoverable/title-and-description`, `be-discoverable/crawlable-and-mobile-friendly`,
`be-discoverable/canonical-and-indexing-signals`,
`be-discoverable/structured-and-shareable-metadata`,
`be-resilient/offline-and-installable`,
`be-memory-efficient/no-leak-under-repeated-interaction`,
`be-memory-efficient/bounded-footprint`. Four of the ten sit in `be-discoverable`
and two in `be-memory-efficient`; `sufficient-contrast` is where `contrast-color`
belongs. This is a coverage gap in the catalog's intent layer, separate from any
measurement gap.

## Changing the catalog: guide-only edits are not free

The natural assumption is that re-pointing a check at a new guide is metadata and
therefore cheaper than adding a check. It is not. The per-report publication gate
keys on the SHA-256 of the catalog file **bytes**, so any edit — including one that
only adds a guide slug to one check's `guides` array — changes the checksum that
every published report records.

Proof, run against a published complete report (58/58 judged):

```bash
# Baseline: unmodified catalog, published report
node scripts/validate_atomic_report.mjs principles.json \
  results/atomic/reports/0001-lectormangass_net.json; echo "exit=$?"
#   exit=0, 0 errors

# Variant: one slug appended to one check's guides array. Check count unchanged (58).
python3 - <<'PY'
import json
d = json.load(open('principles.json'))
for p in d['principles']:
    for c in p['checks']:
        if c['id'] == 'scroll-state-aware-chrome':
            c['guides'] = c['guides'] + ['state-aware-sticky-headers']
json.dump(d, open('/tmp/catalog-guides-only.json', 'w'), indent=2)
PY

node scripts/validate_atomic_report.mjs /tmp/catalog-guides-only.json \
  results/atomic/reports/0001-lectormangass_net.json; echo "exit=$?"
#   exit=1, 1 error:
#   ERROR: coverage.catalogChecksum="sha256:78ccfdb2…"; expected "sha256:f8d242f9…"
```

The measured difference from adding a check matters. Adding a check (recorded as
`state-of-the-web-2p6`) produced six errors, including `coverage.expected=58;
expected 59` and `coverage.complete=true; expected false`. A guide-only edit
produces **one** error — the checksum — while `expected`, `recorded`, `judged` and
`complete` all still agree. Nothing about measurement changed; only the intent
metadata did, and the gate cannot tell the difference.

Blast radius: `scripts/validate_atomic_report.mjs` (per-report gate),
`scripts/reconcile_atomic_run.py` (validates each report against the exact run
catalog it was produced with), `scripts/generate_atomic_checkpoint.py` (groups
reports by `catalogChecksum`), and the publication validators. Published reports
stay verifiable against the catalog bytes they were produced with — the reconciler
pins those — so the cost appears when a published report is validated against an
**updated** `principles.json`, which is what the gate does when handed that file.

Interpretation for the migration decision: a guide-only re-point is an **intent**
change, not a **measurement** change. Under a versioned-catalog story it can land
as a new catalog version with its own checksum while every published report
remains valid against its pinned bytes, and no site needs re-auditing, because no
check outcome changes. Under re-audit-or-defer it would force re-validation of
1,000 reports for zero measurement gain. The useful separation is between
(a) intent metadata — `guides`, `detectableVia`, `references`, this analysis — and
(b) the measurement contract — principle IDs, check IDs, counts, applicability.
Only (b) should invalidate a published run.

## Regenerating this document

```bash
python3 scripts/analyze_guide_coverage.py                      # facts as JSON
python3 scripts/analyze_guide_coverage.py --write docs/principles-analysis.md
python3 scripts/analyze_guide_coverage.py --check docs/principles-analysis.md
```

Every recorded number verifies offline. The guide **bodies** are still not
vendored, but their **slug names** are, in `scripts/fixtures/guide-slugs.json`,
which is what the package-side facts are derived from.

`--check` fails, rather than printing `OK`, when a recorded key cannot be
derived by that run, and reports how many keys it verified. Previously it
compared only the keys it happened to have, so the six package-side numbers —
the ones that needed two `npm pack` extractions and were least likely to be
re-derived by hand — could drift indefinitely while the command stayed green.

To verify against freshly extracted packs instead of the fixture, or to refresh
it after a new release:

```bash
npm pack modern-web-guidance@0.0.172 && tar xzf modern-web-guidance-0.0.172.tgz -C /tmp/mwg/0.0.172
npm pack modern-web-guidance@0.0.190 && tar xzf modern-web-guidance-0.0.190.tgz -C /tmp/mwg/0.0.190
python3 scripts/analyze_guide_coverage.py --check docs/principles-analysis.md \
  --guides-dir /tmp/mwg/0.0.172 --guides-dir /tmp/mwg/0.0.190
```

`--guides-dir` takes precedence over the fixture, so a stale fixture is
corrected by a real pack rather than silently believed. Both inputs record the
same keys, so a document written one way verifies the other way. Pack versions
are read from each pack's own `package.json`, not from argument order, so the
pinned pack cannot become the latest one by passing the flags backwards.

## Open questions

1. Which migration option does the published run take (`state-of-the-web-2p6`)?
   Guide-only re-pointing is cheap under versioned catalogs and expensive under a
   re-audit rule, so the answer decides all eleven guides here.
2. Should `prompt-api` and `sanitize-untrusted-html` become new checks rather than
   new references? Both are blocked on the same decision; the 0-pass/737
   not-applicable result for `on-device-inference` is the evidence to weigh.
3. Should the ten checks with no resolvable slug be given one as part of the same
   intent-only change, before any measurement work is budgeted?
