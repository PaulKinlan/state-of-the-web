// Modern-web feature probe: one judgement-free Runtime.evaluate expression that
// reports whether a page USES (and whether the browser SUPPORTS) the modern CSS
// platform features the catalog judges in `anchored-positioning`,
// `scroll-driven-animations`, `view-transitions`, `scroll-state-aware-chrome`,
// and `physical-gestures`.
//
// It returns signals only, never verdicts: a page that uses declarative
// `animation-timeline: view()` is a pass candidate for scroll-driven-animations;
// a page that uses none is `not-run`/no-evidence until the auditor inspects
// further.
//
// DETECTION IS VALUE-AWARE, NOT NAME-AWARE. Matching a bare property name is
// wrong in both directions, and both directions were observed on real origins:
//
//   1. Opt-outs would count as usage. `view-transition-name: none` and
//      `position-anchor: unset` DISABLE the feature. microsoft.com ships
//      `.sa-modern-cta-button { ... position-anchor: unset; position-area: unset; ... }`,
//      an `all: unset`-style reset, and a name-matching probe reported it as
//      anchor-positioning adoption.
//   2. Ordinary shorthands would count as usage. The CSSOM expands
//      `animation: pulse 2s infinite` into `animation-timeline: auto` and
//      `animation-range-start: normal`, and `container: card / inline-size`
//      into `container-type: inline-size`. Any page with a plain CSS animation
//      would have registered as scroll-driven.
//
// So a declaration counts only when its value is neither a CSS-wide keyword
// (unset/initial/inherit/revert/revert-layer) nor that property's inert default.
// Values that were seen and filtered are reported under `optedOut`, so the
// auditor can tell "this site does not use the feature" apart from "this site
// explicitly turned the feature off" — a distinction the catalog needs to pick
// between `not-applicable` and `issues`.
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

  // CSS-wide keywords never indicate that a feature is in use.
  const CSS_WIDE = new Set(['unset', 'initial', 'inherit', 'revert', 'revert-layer']);

  // Per-property values that mean "declared, but the feature is NOT in use":
  // either the property's initial value (which is what an ordinary shorthand
  // expands to) or an explicit off switch.
  const INERT = {
    'view-transition-name': ['none'],
    'view-transition-class': ['none'],
    'view-transition-group': ['normal'],
    'anchor-name': ['none'],
    'anchor-scope': ['none'],
    'position-anchor': ['auto', 'none'],
    'position-area': ['none'],
    'position-try': ['normal', 'none'],
    'position-try-fallbacks': ['none'],
    'position-try-order': ['normal'],
    'animation-timeline': ['auto', 'none'],
    'animation-range-start': ['normal'],
    'animation-range-end': ['normal'],
    'scroll-timeline-name': ['none'],
    'scroll-timeline-axis': ['block'],
    'view-timeline-name': ['none'],
    'view-timeline-axis': ['block'],
    'view-timeline-inset': ['auto'],
    'timeline-scope': ['none'],
    'container-type': ['normal'],
    'overscroll-behavior': ['auto'],
    'overscroll-behavior-x': ['auto'],
    'overscroll-behavior-y': ['auto'],
    'overscroll-behavior-inline': ['auto'],
    'overscroll-behavior-block': ['auto'],
    'scroll-snap-type': ['none'],
    'scroll-snap-align': ['none'],
    'touch-action': ['auto'],
  };

  // Which family each tracked property belongs to.
  const PROPERTY_FAMILY = {
    'view-transition-name': 'viewTransitions',
    'view-transition-class': 'viewTransitions',
    'view-transition-group': 'viewTransitions',
    'animation-timeline': 'scrollDrivenAnimations',
    'animation-range-start': 'scrollDrivenAnimations',
    'animation-range-end': 'scrollDrivenAnimations',
    'scroll-timeline-name': 'scrollDrivenAnimations',
    'scroll-timeline-axis': 'scrollDrivenAnimations',
    'view-timeline-name': 'scrollDrivenAnimations',
    'view-timeline-axis': 'scrollDrivenAnimations',
    'view-timeline-inset': 'scrollDrivenAnimations',
    'timeline-scope': 'scrollDrivenAnimations',
    'anchor-name': 'anchorPositioning',
    'anchor-scope': 'anchorPositioning',
    'position-anchor': 'anchorPositioning',
    'position-area': 'anchorPositioning',
    'position-try': 'anchorPositioning',
    'position-try-fallbacks': 'anchorPositioning',
    'position-try-order': 'anchorPositioning',
    'container-type': 'scrollStateChrome',
    'overscroll-behavior': 'gesturePlatforms',
    'overscroll-behavior-x': 'gesturePlatforms',
    'overscroll-behavior-y': 'gesturePlatforms',
    'overscroll-behavior-inline': 'gesturePlatforms',
    'overscroll-behavior-block': 'gesturePlatforms',
    'scroll-snap-type': 'gesturePlatforms',
    'scroll-snap-align': 'gesturePlatforms',
    'touch-action': 'gesturePlatforms',
  };

  // Some properties only count for their family at a specific value:
  // `container-type` is shared with ordinary container queries, and only
  // `scroll-state` is scroll-state-aware chrome.
  const REQUIRED_VALUE = {
    'container-type': value => /(^|\s)scroll-state(\s|$)/.test(value),
  };

  // Anchor positioning is also used through value functions on untracked
  // properties, e.g. `left: anchor(--btn right)` / `width: anchor-size(--btn)`.
  const VALUE_FUNCTIONS = [
    { needle: 'anchor(', family: 'anchorPositioning' },
    { needle: 'anchor-size(', family: 'anchorPositioning' },
  ];

  const TRACKED = new Set(Object.keys(PROPERTY_FAMILY));
  const FAMILY_NAMES = ['viewTransitions', 'scrollDrivenAnimations', 'anchorPositioning', 'scrollStateChrome', 'gesturePlatforms'];

  const families = {};
  for (const name of FAMILY_NAMES) {
    families[name] = {
      used: false,
      declarations: [],
      samples: [],
      optedOut: [],
      optedOutCount: 0,
      usedCount: 0,
      inlineHit: false,
      _props: new Set(),
      _seen: new Set(),
      _seenOptedOut: new Set(),
    };
  }

  const RULE_LIMIT = 100000;
  const SAMPLE_LIMIT = 8;
  const css = {
    rules: 0,
    truncated: false,
    sheets: { total: 0, readable: 0, inaccessible: 0, unreadableUrls: [] },
  };
  const atRules = {
    crossDocumentViewTransitions: false,
    viewTransitionPseudo: false,
    scrollStateContainerQuery: false,
  };

  const record = (property, rawValue, { inline }) => {
    const family = families[PROPERTY_FAMILY[property]];
    if (!family) return;
    const value = String(rawValue || '').trim();
    if (!value) return;
    const lower = value.toLowerCase();
    const inert = (INERT[property] || []).includes(lower) || CSS_WIDE.has(lower);
    const gate = REQUIRED_VALUE[property];
    const counts = !inert && (!gate || gate(lower));
    const pair = `${property}: ${value.length > 80 ? `${value.slice(0, 80)}…` : value}`;
    if (!counts) {
      family.optedOutCount++;
      if (!family._seenOptedOut.has(pair) && family.optedOut.length < SAMPLE_LIMIT) {
        family._seenOptedOut.add(pair);
        family.optedOut.push(pair);
      }
      return;
    }
    family.used = true;
    family.usedCount++;
    if (inline) family.inlineHit = true;
    family._props.add(property);
    if (!family._seen.has(pair) && family.samples.length < SAMPLE_LIMIT) {
      family._seen.add(pair);
      family.samples.push(pair);
    }
  };

  const readDeclarations = (style, options) => {
    if (!style || typeof style.length !== 'number') return;
    for (let index = 0; index < style.length; index++) {
      const property = style.item(index);
      if (TRACKED.has(property)) record(property, style.getPropertyValue(property), options);
      if (!VALUE_FUNCTIONS.length) continue;
      // Only pay for the value lookup when the property could carry a function.
      const value = style.getPropertyValue(property);
      if (!value || value.indexOf('anchor') < 0) continue;
      for (const { needle, family } of VALUE_FUNCTIONS) {
        if (!value.includes(needle)) continue;
        const target = families[family];
        target.used = true;
        target.usedCount++;
        if (options.inline) target.inlineHit = true;
        target._props.add(needle);
        const pair = `${property}: ${value.length > 80 ? `${value.slice(0, 80)}…` : value}`;
        if (!target._seen.has(pair) && target.samples.length < SAMPLE_LIMIT) {
          target._seen.add(pair);
          target.samples.push(pair);
        }
      }
    }
  };

  const prelude = rule => {
    if (typeof rule.conditionText === 'string') return rule.conditionText;
    const text = rule.cssText || '';
    const brace = text.indexOf('{');
    return brace > 0 ? text.slice(0, brace) : text;
  };

  const visitRules = rules => {
    for (const rule of rules || []) {
      if (css.rules >= RULE_LIMIT) {
        css.truncated = true;
        return;
      }
      css.rules++;

      // At-rules and selectors are text-level signals: they are not declarations,
      // so they have no value to judge.
      const selector = typeof rule.selectorText === 'string' ? rule.selectorText : '';
      if (selector.includes('::view-transition')) {
        atRules.viewTransitionPseudo = true;
        families.viewTransitions.used = true;
        families.viewTransitions._props.add('::view-transition');
      }
      if (rule.constructor && rule.constructor.name === 'CSSViewTransitionRule') {
        atRules.crossDocumentViewTransitions = true;
        families.viewTransitions.used = true;
        families.viewTransitions._props.add('@view-transition');
      } else if (!selector && (rule.cssText || '').trim().startsWith('@view-transition')) {
        atRules.crossDocumentViewTransitions = true;
        families.viewTransitions.used = true;
        families.viewTransitions._props.add('@view-transition');
      }
      if (!selector && prelude(rule).includes('scroll-state(')) {
        atRules.scrollStateContainerQuery = true;
        families.scrollStateChrome.used = true;
        families.scrollStateChrome._props.add('scroll-state()');
      }

      if (rule.style) readDeclarations(rule.style, { inline: false });
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

  const inlineStyled = document.querySelectorAll('[style]');
  for (const element of inlineStyled) readDeclarations(element.style, { inline: true });

  for (const name of FAMILY_NAMES) {
    const family = families[name];
    family.declarations = [...family._props].sort();
    delete family._props;
    delete family._seen;
    delete family._seenOptedOut;
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
    matching: 'value-aware',
    supported,
    css: {
      sheets: css.sheets,
      rulesScanned: css.rules,
      truncated: css.truncated,
      atRules,
      families,
    },
    inlineStyledElements: inlineStyled.length,
    animations,
    viewTransitions: {
      apiAvailable: typeof document.startViewTransition === 'function',
      apiReferencedInInlineScript,
    },
    inlineScriptsScanned,
  };
})()
