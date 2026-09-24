// Speculative loading feature probe: one judgement-free Runtime.evaluate expression
// that reports whether a page USES (and whether the browser SUPPORTS) modern
// Speculation Rules (prefetch / prerender) and speculative navigation mechanisms.
//
// It returns signals only, never verdicts:
// - A page that declares valid <script type="speculationrules"> with document or
//   list rules is evaluated for speculative-loading adoption.
// - A page with invalid JSON speculation rules produces explicit syntax errors.
// - Real link navigation behavior is measured by sampling internal links and testing
//   for client-side click interception (preventDefault / pushState), so server-rendered
//   framework sites with document navigations are distinguished from client-routed SPAs.
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

    // Cap rulesets to prevent multi-MB payload bloat on large sites
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

  // Navigation context: internal vs external links + real navigation interception behavior
  const anchors = Array.from(document.querySelectorAll('a[href]'));
  const internalLinks = [];
  let externalLinkCount = 0;
  const currentHost = location.host;
  const isFileOrigin = location.protocol === 'file:';

  for (const a of anchors) {
    try {
      const url = new URL(a.href, location.href);
      if (isFileOrigin) {
        if (url.protocol === 'file:') {
          internalLinks.push(a);
        } else {
          externalLinkCount++;
        }
      } else if (url.protocol === 'http:' || url.protocol === 'https:') {
        if (url.host === currentHost) {
          internalLinks.push(a);
        } else {
          externalLinkCount++;
        }
      }
    } catch {
      // invalid URL
    }
  }

  // Framework presence markers
  let frameworkMarker = null;
  if (typeof window !== 'undefined') {
    if (window.__NEXT_DATA__ || document.querySelector('#__next')) {
      frameworkMarker = 'next';
    } else if (window.__NUXT__ || document.querySelector('#__nuxt')) {
      frameworkMarker = 'nuxt';
    } else if (window.__remixContext) {
      frameworkMarker = 'remix';
    } else if (window.__sveltekit || document.querySelector('[data-sveltekit-preload-data]')) {
      frameworkMarker = 'sveltekit';
    } else if (window.___gatsby || document.querySelector('#___gatsby')) {
      frameworkMarker = 'gatsby';
    } else if (document.querySelector('[data-reactroot], [data-react-helmet]')) {
      frameworkMarker = 'react-spa';
    } else if (document.querySelector('[data-server-rendered]')) {
      frameworkMarker = 'vue/ssr';
    } else if (document.querySelector('[ng-version]')) {
      frameworkMarker = 'angular';
    } else if (location.hash && location.hash.startsWith('#/')) {
      frameworkMarker = 'hash-router';
    }
  }

  // Real navigation interception probe: sample internal links to check whether
  // clicks trigger client-side routing (preventDefault / pushState) vs real document navigation
  const sampleLinks = internalLinks.slice(0, 5);
  let interceptedLinksCount = 0;
  let defaultPreventedIntercepted = false;
  let pushStateIntercepted = false;

  for (const link of sampleLinks) {
    let pushStateCalled = false;
    const origPush = history.pushState;
    const origReplace = history.replaceState;
    try {
      history.pushState = () => { pushStateCalled = true; };
      history.replaceState = () => { pushStateCalled = true; };
      const ev = new MouseEvent('click', { bubbles: true, cancelable: true });
      link.dispatchEvent(ev);
      if (ev.defaultPrevented || pushStateCalled) {
        interceptedLinksCount++;
        if (ev.defaultPrevented) defaultPreventedIntercepted = true;
        if (pushStateCalled) pushStateIntercepted = true;
      }
    } catch {
      // ignore dispatch issues
    } finally {
      history.pushState = origPush;
      history.replaceState = origReplace;
    }
  }

  let linkNavigationMode = 'none';
  let isClientSideRouted = false;

  if (sampleLinks.length > 0) {
    if (interceptedLinksCount === sampleLinks.length) {
      linkNavigationMode = 'client-intercepted';
      isClientSideRouted = true;
    } else if (interceptedLinksCount === 0) {
      linkNavigationMode = 'document-navigation';
      isClientSideRouted = false;
    } else {
      linkNavigationMode = 'mixed';
      isClientSideRouted = false;
    }
  } else if (frameworkMarker === 'hash-router') {
    linkNavigationMode = 'hash-router';
    isClientSideRouted = true;
  } else if (frameworkMarker && internalLinks.length === 0) {
    linkNavigationMode = 'framework-isolated';
    isClientSideRouted = true;
  }

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
      internalLinkCount: internalLinks.length,
      externalLinkCount,
      sampledLinksCount: sampleLinks.length,
      interceptedLinksCount,
      linkNavigationMode,
      isClientSideRouted,
      frameworkMarker,
      defaultPreventedIntercepted,
      pushStateIntercepted,
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
