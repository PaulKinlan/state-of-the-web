# Vision audit methodology notes

## Evidence reviewed vs collected

For batch 001/002 scale runs, judgments were mostly derived from parsed evidence JSON, not manual visual inspection of every artifact.

### Reviewed/used directly
- `axe.json` — parsed for violation groups, impact, node counts; used for `be-inclusive`.
- `layout-desktop.json`, `layout-mobile.json` — parsed for CLS, horizontal overflow, viewport meta, long tasks; used for `be-fast-and-stable` and `adapt-to-the-form-factor`.
- `discoverability.json` — parsed for `coveragePct`, `isJsShell`, raw title/h1/meta, text character counts; used for `be-discoverable` and `be-agent-ready`.
- `page-summary.json` — parsed for request counts, transferred bytes, third-party request/byte counts; used for `be-sustainable`, `be-private-and-secure`, and page-noise findings.
- `probes.json` — parsed for `lang`, meta description, viewport, unnamed controls/links, `colorScheme`, animation count, manifest/service-worker signals, script hosts.
- `heap.json` — parsed initially, but baseline-only evidence is not sufficient for leak claims; existing JSONs were patched so memory is pending unless `evidence.memoryAudit` exists.
- `evidence.memoryAudit` — rigorous same-session memory recheck where present: baseline heap usage, interaction cycles, forced GC, final heap snapshot summary.

### Collected but not manually reviewed per site
- `screenshot.png`, `mobile.png`, discoverability rendered/crawler PNGs — retained for auditability; not manually opened for every scale-run site.
- Raw `page.har` — retained; judgments used `page-summary.json` rather than raw HAR, as recommended by web-uplift for large HAR files.
- `heap-after.json` — collected in batch 002 as a coarse signal, superseded by `evidence.memoryAudit`.

### Missing from original scale batches, patched for future batches
- `lighthouse.json` — absent in original scale batches; future `run_gpt_batch.py` collects it.
- `trace.json` / `trace-summary.json` — absent in original scale batches; future `run_gpt_batch.py` collects it.
- Reduced-motion video — absent in original scale batches; future `run_gpt_batch.py` collects `reduced-motion.mp4` under `prefers-reduced-motion=reduce`.

## Principles patched to avoid false passes

The following principles require active flow testing and are marked `not-applicable` / low confidence until such tests exist:
- `be-trustworthy` — needs consent/account/commerce/dark-pattern flow review.
- `be-resilient` — needs no-JS/offline/reload fallback testing.
- `implement-natural-interactions` — needs keyboard/focus/input-modality testing.
- `provide-guided-navigation` — needs wayfinding/search/menu-flow testing.

`be-memory-efficient` is also pending unless `evidence.memoryAudit` exists.

## Current confidence model

High-confidence / evidence-backed where present:
- axe/layout/discoverability/HAR-summary/probes-derived findings.

Lower-confidence / pending supplemental evidence:
- Performance and SEO on original scale sites until Lighthouse/trace supplemental pass runs.
- Reduced-motion on original scale sites until reduced-motion video/probe supplemental pass runs.
- Memory until same-session memory audit is present.
- Active-flow UX principles until dedicated flow tests are implemented.
