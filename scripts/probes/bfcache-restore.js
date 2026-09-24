// Back/forward cache probe: one judgement-free expression that reports whether
// the document it runs in was restored from the back/forward cache, and if not,
// which blocker the browser named.
//
// It reads PerformanceNavigationTiming.notRestoredReasons, the browser's own
// account of why a back/forward navigation was not restored:
//   https://developer.chrome.com/docs/web-platform/bfcache-notRestoredReasons
//
// The value is only meaningful after a REAL back/forward navigation. On a first
// load it is null for a different reason (the navigation was neither a restore
// nor a failed restore), so `navigationType` must be read alongside it — and the
// driver (`scripts/bfcache_probe.mjs`) corroborates the verdict with the CDP
// signal that is independent of the page: the frame's loaderId is unchanged
// across a restore, because the document was never re-created.
//
// Judgement is left to the auditor: this returns raw evidence, an explicit
// `notRestoredReasonsSupported` flag, and the inference in `restoredFromBfcache`
// with its basis stated, rather than a pass/fail.
//
// Run it through the web-uplift evidence harness after driving history back:
//   node evidence/cli.mjs evaluate <url> --expr-file scripts/probes/bfcache-restore.js \
//     --out evidence/<site>/bfcache-restore.json
//
// Still NOT covered, reported rather than guessed: a restore that cannot be
// observed because the page never becomes eligible requires the driver to
// compare against a second navigation, and cross-origin iframes report their own
// reasons under `children` (surfaced, not walked recursively beyond one level).
(() => {
  const navigation = performance.getEntriesByType('navigation')[0] || null;
  const supported = !!navigation && 'notRestoredReasons' in navigation;
  const reasons = supported ? navigation.notRestoredReasons : undefined;

  const collect = node => {
    if (!node) return [];
    const blockers = [];
    for (const entry of node.reasons || []) {
      if (entry && entry.reason) blockers.push(entry.reason);
    }
    const children = [];
    for (const child of node.children || []) {
      children.push({
        url: child.url || null,
        src: child.src || null,
        id: child.id || null,
        name: child.name || null,
        sameOrigin: child.sameOrigin ?? null,
        blockers: collect(child),
      });
    }
    return { blockers, children };
  };

  const details = supported && reasons ? collect(reasons) : { blockers: [], children: [] };
  const navigationType = navigation ? navigation.type : null;
  // Measured semantics, not assumed ones (see scripts/bfcache_probe.mjs):
  //   - a page that WAS restored keeps its original document, so its navigation
  //     entry still has type 'navigate' and notRestoredReasons is null;
  //   - a page that was NOT restored (or was evicted) comes back as a new
  //     document with type 'back_forward' and a NotRestoredReasons OBJECT —
  //     often `{}` or `mixed`, because Chrome masks the reason when it is
  //     user-agent-specific.
  // So `null` alone cannot prove a restore. The recorder the driver installs
  // (`window.__bfcache.persisted`, from a pageshow event) is the authoritative
  // page-side signal; without it this probe reports `inconclusive` rather than
  // guessing.
  const recorder = (typeof window !== 'undefined' && window.__bfcache) || null;
  const persistedPageshow = recorder && typeof recorder.persisted === 'boolean' ? recorder.persisted : null;
  const masked = supported && reasons !== null && reasons !== undefined &&
    (!reasons.reasons || reasons.reasons.length === 0);
  const restoredFromBfcache = persistedPageshow === null
    ? null
    : persistedPageshow === true;
  const basis = persistedPageshow !== null
    ? 'pageshow event reported persisted=' + persistedPageshow
    : 'no pageshow recorder installed: notRestoredReasons null is ambiguous between a restored page and a non-history navigation';

  return {
    probe: 'bfcache-restore',
    ok: true,
    url: location.href,
    navigationType,
    notRestoredReasonsSupported: supported,
    notRestoredReasons: reasons === undefined ? null : reasons,
    bfcache: {
      restoredFromBfcache,
      basis,
      persistedPageshow,
      reasonsMasked: masked,
      blockers: details.blockers,
      childFrameCount: details.children.length,
      children: details.children,
    },
  };
})()
