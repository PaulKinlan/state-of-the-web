// Speculative loading feature probe: one judgement-free Runtime.evaluate expression
// that reports whether a page USES (and whether the browser SUPPORTS) modern
// Speculation Rules (prefetch / prerender) and speculative navigation mechanisms.
//
// It returns signals only, never verdicts:
// - A page that declares valid <script type="speculationrules"> with document or
//   list rules is a pass candidate for speculative-loading.
// - A page with invalid JSON speculation rules produces explicit syntax errors.
// - A single-page application with client-side routing or a page with external-only
//   links provides navigation context so auditors can judge applicability.
// - Legacy signals (<link rel="prefetch|prerender">) and delivery metrics are
//   captured side-by-side to detect modern vs legacy migration state.
//
// Run it through the web-uplift evidence harness:
//   node evidence/cli.mjs evaluate <url> --expr-file scripts/probes/speculative-loading.js \
//     --out evidence/<site>/speculative-loading.json
(() => {
  const supportsSpeculationRules = () => {
    try {
      return (
        typeof HTMLScriptElement !== 'undefined' &&
        typeof HTMLScriptElement.supports === 'function' &&
        HTMLScriptElement.supports('speculationrules')
      );
    } catch {
      return false;
    }
  };

  const supported = {
    speculationRules: supportsSpeculationRules(),
    prerendering: typeof document !== 'undefined' && 'prerendering' in document,
    isPrerendering: typeof document !== 'undefined' && document.prerendering === true,
  };

  const scripts = Array.from(
    document.querySelectorAll('script[type="speculationrules"], script[type="speculation-rules"]')
  );

  const errors = [];
  const rawRulesets = [];
  let validScripts = 0;
  let invalidScripts = 0;

  const prefetchSummary = {
    count: 0,
    listRules: 0,
    documentRules: 0,
    eagerness: new Set(),
    sampleUrls: [],
    documentConditions: [],
  };

  const prerenderSummary = {
    count: 0,
    listRules: 0,
    documentRules: 0,
    eagerness: new Set(),
    sampleUrls: [],
    documentConditions: [],
  };

  function summarizeCondition(cond) {
    if (!cond || typeof cond !== 'object') return String(cond);
    const parts = [];
    for (const [k, v] of Object.entries(cond)) {
      if (Array.isArray(v)) {
        parts.push(`${k}: [${v.map(summarizeCondition).join(', ')}]`);
      } else if (typeof v === 'object' && v !== null) {
        parts.push(`${k}: {${summarizeCondition(v)}}`);
      } else {
        parts.push(`${k}: ${v}`);
      }
    }
    return parts.join(', ');
  }

  function inspectRule(rule, targetSummary) {
    targetSummary.count++;
    const source = rule.source || 'list';
    if (source === 'list') {
      targetSummary.listRules++;
      if (Array.isArray(rule.urls)) {
        for (const u of rule.urls) {
          if (targetSummary.sampleUrls.length < 10) {
            targetSummary.sampleUrls.push(String(u));
          }
        }
      }
    } else if (source === 'document') {
      targetSummary.documentRules++;
      if (rule.where) {
        const condSummary = summarizeCondition(rule.where);
        if (targetSummary.documentConditions.length < 5) {
          targetSummary.documentConditions.push(`where: ${condSummary}`);
        }
      }
    }
    if (rule.eagerness) {
      targetSummary.eagerness.add(String(rule.eagerness));
    }
  }

  function capRule(rule) {
    if (!rule || typeof rule !== 'object') return rule;
    const capped = { ...rule };
    if (Array.isArray(capped.urls) && capped.urls.length > 10) {
      capped.truncatedUrlsCount = capped.urls.length - 10;
      capped.urls = capped.urls.slice(0, 10);
    }
    return capped;
  }

  for (let i = 0; i < scripts.length; i++) {
    const el = scripts[i];
    const text = el.textContent ? el.textContent.trim() : '';
    if (!text) {
      invalidScripts++;
      errors.push({ scriptIndex: i, error: 'empty speculation rules script' });
      continue;
    }
    let parsed;
    try {
      parsed = JSON.parse(text);
      validScripts++;
    } catch (e) {
      invalidScripts++;
      errors.push({ scriptIndex: i, error: `JSON parse error: ${e.message}`, snippet: text.slice(0, 100) });
      continue;
    }

    // Cap rulesets to prevent multi-MB payload bloat on large sites (F7)
    if (rawRulesets.length < 10) {
      const cappedRuleset = {};
      if (parsed && typeof parsed === 'object') {
        if (Array.isArray(parsed.prefetch)) {
          cappedRuleset.prefetch = parsed.prefetch.slice(0, 10).map(capRule);
          if (parsed.prefetch.length > 10) cappedRuleset.prefetchTruncatedCount = parsed.prefetch.length - 10;
        }
        if (Array.isArray(parsed.prerender)) {
          cappedRuleset.prerender = parsed.prerender.slice(0, 10).map(capRule);
          if (parsed.prerender.length > 10) cappedRuleset.prerenderTruncatedCount = parsed.prerender.length - 10;
        }
      }
      rawRulesets.push(cappedRuleset);
    }

    if (parsed && typeof parsed === 'object') {
      if (Array.isArray(parsed.prefetch)) {
        for (const rule of parsed.prefetch) {
          if (rule && typeof rule === 'object') inspectRule(rule, prefetchSummary);
        }
      }
      if (Array.isArray(parsed.prerender)) {
        for (const rule of parsed.prerender) {
          if (rule && typeof rule === 'object') inspectRule(rule, prerenderSummary);
        }
      }
    }
  }

  // Legacy speculation / resource hints
  const linkPrefetches = Array.from(document.querySelectorAll('link[rel="prefetch"]'));
  const linkPrerenders = Array.from(document.querySelectorAll('link[rel="prerender"]'));
  const linkModulepreloads = Array.from(document.querySelectorAll('link[rel="modulepreload"]'));
  const linkDnsPrefetches = Array.from(document.querySelectorAll('link[rel="dns-prefetch"]'));
  const linkPreconnects = Array.from(document.querySelectorAll('link[rel="preconnect"]'));

  const legacySamples = [
    ...linkPrefetches.map(l => ({ rel: 'prefetch', href: l.getAttribute('href') })),
    ...linkPrerenders.map(l => ({ rel: 'prerender', href: l.getAttribute('href') })),
    ...linkModulepreloads.map(l => ({ rel: 'modulepreload', href: l.getAttribute('href') })),
  ].slice(0, 10);

  // Performance timeline: navigational-prefetch entries (Chrome 128+)
  const samplePrefetchEntries = [];
  let navigationalPrefetchCount = 0;
  try {
    if (typeof performance !== 'undefined' && typeof performance.getEntriesByType === 'function') {
      const resources = performance.getEntriesByType('resource');
      for (const res of resources) {
        if (res.deliveryType === 'navigational-prefetch') {
          navigationalPrefetchCount++;
          if (samplePrefetchEntries.length < 5) {
            samplePrefetchEntries.push({
              name: res.name,
              duration: Math.round(res.duration),
              transferSize: res.transferSize,
            });
          }
        }
      }
    }
  } catch {
    // performance API not available or constrained
  }

  // Navigation context: links and non-authoritative framework hints.
  const anchors = Array.from(document.querySelectorAll('a[href]'));
  let internalLinkCount = 0;
  let externalLinkCount = 0;
  const currentHost = location.host;

  for (const a of anchors) {
    try {
      const url = new URL(a.href, location.href);
      if (url.protocol === 'http:' || url.protocol === 'https:') {
        if (url.host === currentHost) {
          internalLinkCount++;
        } else {
          externalLinkCount++;
        }
      }
    } catch {
      // invalid URL
    }
  }

  // Framework markers are hints, not evidence that any link is intercepted.
  // SSR/hydrated framework pages can perform ordinary document navigations.
  let frameworkHint = null;
  if (typeof window !== 'undefined') {
    if (window.__NEXT_DATA__ || document.querySelector('#__next')) {
      frameworkHint = 'next';
    } else if (window.__NUXT__ || document.querySelector('#__nuxt')) {
      frameworkHint = 'nuxt';
    } else if (window.__remixContext) {
      frameworkHint = 'remix';
    } else if (window.__sveltekit || document.querySelector('[data-sveltekit-preload-data]')) {
      frameworkHint = 'sveltekit';
    } else if (window.___gatsby || document.querySelector('#___gatsby')) {
      frameworkHint = 'gatsby';
    } else if (document.querySelector('[data-reactroot], [data-react-helmet]')) {
      frameworkHint = 'react';
    } else if (document.querySelector('[ng-version]')) {
      frameworkHint = 'angular';
    } else if (document.querySelector('[data-server-rendered]')) {
      frameworkHint = 'vue-ssr';
    } else if (location.hash && location.hash.startsWith('#/')) {
      frameworkHint = 'hash-router';
    }
  }

  // A snapshot cannot establish navigation behaviour. The opt-in CDP driver
  // records a representative link activation separately; null is not false.
  const isClientSideRouted = null;
  const prefetchEagerness = Array.from(prefetchSummary.eagerness);
  const prerenderEagerness = Array.from(prerenderSummary.eagerness);
  const allEagerness = Array.from(new Set([...prefetchEagerness, ...prerenderEagerness]));

  const hasSpeculationRules = validScripts > 0;
  const hasPrefetch = prefetchSummary.count > 0;
  const hasPrerender = prerenderSummary.count > 0;
  const hasDocumentRules = prefetchSummary.documentRules > 0 || prerenderSummary.documentRules > 0;
  const hasListRules = prefetchSummary.listRules > 0 || prerenderSummary.listRules > 0;
  const hasLegacySpeculation = linkPrefetches.length > 0 || linkPrerenders.length > 0;

  return {
    probe: 'speculative-loading',
    ok: true,
    url: location.href,
    supported,
    speculationRules: {
      scriptsFound: scripts.length,
      validScripts,
      invalidScripts,
      errors,
      prefetch: {
        count: prefetchSummary.count,
        listRules: prefetchSummary.listRules,
        documentRules: prefetchSummary.documentRules,
        eagerness: prefetchEagerness,
        sampleUrls: prefetchSummary.sampleUrls,
        documentConditions: prefetchSummary.documentConditions,
      },
      prerender: {
        count: prerenderSummary.count,
        listRules: prerenderSummary.listRules,
        documentRules: prerenderSummary.documentRules,
        eagerness: prerenderEagerness,
        sampleUrls: prerenderSummary.sampleUrls,
        documentConditions: prerenderSummary.documentConditions,
      },
      rawRulesets,
    },
    legacySpeculation: {
      linkPrefetch: linkPrefetches.length,
      linkPrerender: linkPrerenders.length,
      linkModulepreload: linkModulepreloads.length,
      linkDnsPrefetch: linkDnsPrefetches.length,
      linkPreconnect: linkPreconnects.length,
      samples: legacySamples,
    },
    performance: {
      navigationalPrefetches: navigationalPrefetchCount,
      samplePrefetchEntries,
    },
    navigationContext: {
      anchorCount: anchors.length,
      internalLinkCount,
      externalLinkCount,
      isClientSideRouted,
      frameworkHint,
      navigationObservation: { type: 'unobserved', method: 'snapshot-only' },
    },
    signals: {
      hasSpeculationRules,
      hasPrefetch,
      hasPrerender,
      hasDocumentRules,
      hasListRules,
      hasLegacySpeculation,
      eagernessConfigured: allEagerness.length > 0,
      eagernessLevels: allEagerness,
    },
  };
})();
