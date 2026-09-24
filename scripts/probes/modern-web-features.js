// Modern-web feature probe: one judgement-free Runtime.evaluate expression that
// reports whether a page USES (and whether the browser SUPPORTS) the Chrome 134+
// CSS platform features the catalog judges in `anchored-positioning`,
// `scroll-driven-animations`, `view-transitions`, `scroll-state-aware-chrome`,
// and `physical-gestures`.
//
// It returns signals only, never verdicts: a page that uses declarative
// `animation-timeline` is a pass candidate for scroll-driven-animations; a page
// that uses none is `not-run`/no-evidence until the auditor inspects further.
//
// Run it through the web-uplift evidence harness:
//   node evidence/cli.mjs evaluate <url> --expr-file scripts/probes/modern-web-features.js \
//     --out evidence/<site>/modern-web-features.json
(() => {
  const supports = (property, value) => {
    try {
      return CSS.supports(property, value);
    } catch {
      return null;
    }
  };

  const supported = {
    viewTransitionName: supports('view-transition-name', 'none'),
    viewTransitionClass: supports('view-transition-class', 'fade'),
    animationTimelineScroll: supports('animation-timeline', 'scroll()'),
    animationTimelineView: supports('animation-timeline', 'view()'),
    scrollTimelineName: supports('scroll-timeline-name', '--x'),
    viewTimelineName: supports('view-timeline-name', '--x'),
    timelineScope: supports('timeline-scope', '--x'),
    anchorName: supports('anchor-name', '--x'),
    positionAnchor: supports('position-anchor', '--x'),
    positionTryFallbacks: supports('position-try-fallbacks', 'flip-block'),
    positionArea: supports('position-area', 'top'),
    scrollStateContainer: supports('container-type', 'scroll-state'),
    scrollSnapType: supports('scroll-snap-type', 'x mandatory'),
    overscrollBehavior: supports('overscroll-behavior', 'contain'),
  };

  const FAMILIES = {
    viewTransitions: [
      'view-transition-name',
      'view-transition-class',
      'view-transition-group',
      '::view-transition',
      '@view-transition',
    ],
    scrollDrivenAnimations: [
      'animation-timeline',
      'scroll-timeline',
      'view-timeline',
      'timeline-scope',
      'animation-range',
    ],
    anchorPositioning: [
      'anchor-name',
      'position-anchor',
      'position-try',
      'position-area',
      'anchor(',
      'anchor-size(',
    ],
    scrollStateChrome: ['scroll-state(', 'container-type: scroll-state', 'container-type:scroll-state'],
    gesturePlatforms: ['overscroll-behavior', 'scroll-snap-type', 'scroll-snap-align', 'touch-action'],
  };

  const css = { rules: 0, sheets: { total: 0, readable: 0, inaccessible: 0, unreadableUrls: [] }, text: [] };
  const inlineText = [];
  const collect = style => {
    if (style && style.cssText) css.text.push(style.cssText);
  };
  const visitRules = rules => {
    for (const rule of rules || []) {
      css.rules++;
      if (rule.cssText) css.text.push(rule.cssText);
      if (rule.style) collect(rule.style);
      if (rule.cssRules) visitRules(rule.cssRules);
    }
  };
  const walkSheet = sheet => {
    try {
      visitRules(sheet.cssRules);
      css.sheets.readable++;
    } catch {
      // Cross-origin stylesheets throw on cssRules; count them and record where
      // the unread CSS lives so the auditor can pull it over CDP or from a HAR
      // with bodies instead of silently judging a page with partial CSS.
      css.sheets.inaccessible++;
      if (sheet.href && css.sheets.unreadableUrls.length < 10) css.sheets.unreadableUrls.push(sheet.href);
    }
  };

  const sheets = [...document.styleSheets, ...(document.adoptedStyleSheets || [])];
  css.sheets.total = sheets.length;
  for (const sheet of sheets) walkSheet(sheet);
  for (const style of document.querySelectorAll('[style]')) inlineText.push(style.getAttribute('style') || '');

  const cssBlob = css.text.join('\n');
  const inlineBlob = inlineText.join('\n');
  const families = {};
  for (const [family, needles] of Object.entries(FAMILIES)) {
    const hits = needles.filter(needle => cssBlob.includes(needle));
    families[family] = {
      used: hits.length > 0,
      declarations: hits,
      inlineHit: needles.some(needle => inlineBlob.includes(needle)),
    };
  }

  let animations = { total: 0, withTimeline: 0, scrollTimelines: 0, viewTimelines: 0 };
  try {
    const running = document.getAnimations({ subtree: true });
    animations = { total: running.length, withTimeline: 0, scrollTimelines: 0, viewTimelines: 0 };
    for (const animation of running) {
      const timeline = animation.timeline;
      if (!timeline) continue;
      animations.withTimeline++;
      const type = Object.prototype.toString.call(timeline);
      if (/ScrollTimeline/.test(type)) animations.scrollTimelines++;
      else if (/ViewTimeline/.test(type)) animations.viewTimelines++;
    }
  } catch {
    // getAnimations is unavailable or threw; leave the zeroed shape.
  }

  let apiReferencedInInlineScript = false;
  let inlineScriptsScanned = 0;
  for (const script of document.querySelectorAll('script:not([src])')) {
    inlineScriptsScanned++;
    if ((script.textContent || '').includes('startViewTransition')) apiReferencedInInlineScript = true;
  }

  return {
    probe: 'modern-web-features',
    ok: true,
    url: location.href,
    supported,
    css: {
      sheets: css.sheets,
      rulesScanned: css.rules,
      atRules: {
        crossDocumentViewTransitions: cssBlob.includes('@view-transition'),
        viewTransitionPseudo: cssBlob.includes('::view-transition'),
        scrollStateContainerQuery: cssBlob.includes('scroll-state('),
      },
      families,
    },
    inlineStyledElements: inlineText.length,
    animations,
    viewTransitions: {
      apiAvailable: typeof document.startViewTransition === 'function',
      apiReferencedInInlineScript,
    },
    inlineScriptsScanned,
  };
})()
