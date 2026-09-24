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

  // A function name inside a string literal is text, not a function call:
  // `content: "anchor("` is not anchor positioning. Stripping strings is a
  // complete fix rather than a heuristic, because an invalid function call such
  // as `content: anchor(--x)` never parses into the CSSOM in the first place --
  // so whatever survives outside a string really was parsed as a function.
  const withoutStrings = value => value.replace(/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/g, '""');

  // Did the author WRITE this longhand, or did the CSSOM synthesise it by
  // expanding a shorthand? `style.item()` enumerates both, but only authored
  // declarations survive into the rule's serialised text: `animation: pulse 2s`
  // serialises back as the shorthand while still enumerating
  // `animation-timeline: auto`.
  //
  // This matters because an inert value means two completely different things.
  // `animation-timeline: none` written by hand is a decision. The same value
  // synthesised by an ordinary `animation:` shorthand is not a decision at all --
  // the author never considered the feature. Reporting both as "opted out" would
  // invent intent that is not in the CSS.
  //
  // Best-effort by construction: it reads a serialisation, so it is a strong
  // hint, not proof of intent. Known limitation: when a rule mixes a shorthand
  // WITH longhand overrides, Chrome cannot round-trip the shorthand and expands
  // every longhand into `cssText`, so untouched longhands in that rule look
  // authored. The common case -- a plain `animation:` with no overrides --
  // stays collapsed and is classified correctly. Never present `optedOut` as
  // proof of deliberate intent; it is evidence to read, not a verdict.
  const authoredIn = (text, property) => {
    if (!text) return false;
    return new RegExp(`(^|[{;\\s])${property}\\s*:`).test(text);
  };

  // Split a list value on top-level commas only, so `view(block 10% 20%), none`
  // becomes two parts and the function's own commas are left alone.
  const listParts = value => {
    const parts = [];
    let current = '';
    let depth = 0;
    let quote = '';
    for (const character of value) {
      if (quote) {
        current += character;
        if (character === quote) quote = '';
        continue;
      }
      if (character === '"' || character === "'") {
        quote = character;
        current += character;
        continue;
      }
      if (character === '(') depth++;
      else if (character === ')') depth--;
      else if (character === ',' && depth === 0) {
        parts.push(current);
        current = '';
        continue;
      }
      current += character;
    }
    parts.push(current);
    return parts.map(part => part.trim()).filter(Boolean);
  };

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
      inertDefaults: [],
      inertDefaultCount: 0,
      usedCount: 0,
      inlineHit: false,
      _props: new Set(),
      _seen: new Set(),
      _seenOptedOut: new Set(),
      _seenInertDefaults: new Set(),
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

  const record = (property, rawValue, { inline, authored }) => {
    const family = families[PROPERTY_FAMILY[property]];
    if (!family) return;
    const value = String(rawValue || '').trim();
    if (!value) return;
    const lower = value.toLowerCase();
    // List-valued properties must be judged PER PART. `animation-timeline:
    // auto, none` is two inert values, but comparing the whole serialised
    // string to a scalar token never matches, so it read as usage. A property
    // counts only if at least one part actually enables the feature.
    const inertTokens = INERT[property] || [];
    const isInert = part => inertTokens.includes(part) || CSS_WIDE.has(part);
    const inert = listParts(lower).every(isInert);
    const gate = REQUIRED_VALUE[property];
    const counts = !inert && (!gate || gate(lower));
    const pair = `${property}: ${value.length > 80 ? `${value.slice(0, 80)}…` : value}`;
    if (!counts) {
      // An authored inert value is a deliberate opt-out. One synthesised by a
      // shorthand is an inert default the author never wrote. Keeping them in
      // separate buckets is what makes `optedOut` mean something.
      if (authored) {
        family.optedOutCount++;
        if (!family._seenOptedOut.has(pair) && family.optedOut.length < SAMPLE_LIMIT) {
          family._seenOptedOut.add(pair);
          family.optedOut.push(pair);
        }
      } else {
        family.inertDefaultCount++;
        if (!family._seenInertDefaults.has(pair) && family.inertDefaults.length < SAMPLE_LIMIT) {
          family._seenInertDefaults.add(pair);
          family.inertDefaults.push(pair);
        }
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
      if (TRACKED.has(property)) {
        record(property, style.getPropertyValue(property), {
          inline: options.inline,
          authored: authoredIn(options.sourceText, property),
        });
      }
      if (!VALUE_FUNCTIONS.length) continue;
      // Only pay for the value lookup when the property could carry a function.
      const raw = style.getPropertyValue(property);
      if (!raw || raw.indexOf('anchor') < 0) continue;
      const value = withoutStrings(raw);
      if (value.indexOf('anchor') < 0) continue;
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
      // `@view-transition` only enables cross-document transitions when its
      // `navigation` descriptor says so. `navigation: none` is an explicit
      // opt-out, and an omitted descriptor is initially `none` -- neither is
      // usage, so the rule's mere existence cannot be the signal.
      const isViewTransitionRule = (rule.constructor && rule.constructor.name === 'CSSViewTransitionRule')
        || (!selector && (rule.cssText || '').trim().startsWith('@view-transition'));
      if (isViewTransitionRule) {
        const declared = typeof rule.navigation === 'string' && rule.navigation
          ? rule.navigation
          : (/navigation\s*:\s*([a-zA-Z-]+)/.exec(rule.cssText || '') || [])[1] || '';
        const navigation = declared.trim().toLowerCase();
        if (navigation && navigation !== 'none') {
          atRules.crossDocumentViewTransitions = true;
          families.viewTransitions.used = true;
          families.viewTransitions.usedCount++;
          families.viewTransitions._props.add('@view-transition');
        } else {
          const pair = `@view-transition navigation: ${navigation || 'none (initial)'}`;
          families.viewTransitions.optedOutCount++;
          if (!families.viewTransitions._seenOptedOut.has(pair)
            && families.viewTransitions.optedOut.length < SAMPLE_LIMIT) {
            families.viewTransitions._seenOptedOut.add(pair);
            families.viewTransitions.optedOut.push(pair);
          }
        }
      }
      if (!selector && prelude(rule).includes('scroll-state(')) {
        atRules.scrollStateContainerQuery = true;
        families.scrollStateChrome.used = true;
        families.scrollStateChrome._props.add('scroll-state()');
      }

      if (rule.style) readDeclarations(rule.style, { inline: false, sourceText: rule.cssText || '' });
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

  // querySelectorAll does not cross shadow boundaries. Visit each open root,
  // including nested roots, without recursive calls on deeply nested components.
  // Closed roots and iframe documents remain outside this page-level probe.
  const roots = [document];
  const seenSheets = new Set();
  let inlineStyledElements = 0;
  for (const root of roots) {
    for (const element of root.querySelectorAll('*')) {
      if (element.shadowRoot) roots.push(element.shadowRoot);
    }
    for (const sheet of [...(root.styleSheets || []), ...(root.adoptedStyleSheets || [])]) {
      // A constructed sheet may be adopted by the document and many components.
      // Count unique CSS sources, not adoptions, and preserve the rule budget.
      if (seenSheets.has(sheet)) continue;
      seenSheets.add(sheet);
      css.sheets.total++;
      walkSheet(sheet);
    }
    for (const element of root.querySelectorAll('[style]')) {
      inlineStyledElements++;
      readDeclarations(element.style, { inline: true, sourceText: element.getAttribute('style') || '' });
    }
  }

  for (const name of FAMILY_NAMES) {
    const family = families[name];
    family.declarations = [...family._props].sort();
    delete family._props;
    delete family._seen;
    delete family._seenOptedOut;
    delete family._seenInertDefaults;
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
    // How to read each family:
    //   used/usedCount   values that actually enable the feature
    //   optedOut         inert values the author appears to have WRITTEN
    //   inertDefaults    inert values the CSSOM synthesised from a shorthand
    // optedOut is a hint about intent, never a verdict: see authoredIn().
    buckets: ['used', 'optedOut', 'inertDefaults'],
    supported,
    css: {
      sheets: css.sheets,
      scope: {
        rootsScanned: roots.length,
        openShadowRootsScanned: roots.length - 1,
        closedShadowRoots: 'not-inspected',
        iframes: 'not-inspected',
      },
      rulesScanned: css.rules,
      truncated: css.truncated,
      atRules,
      families,
    },
    inlineStyledElements,
    animations,
    viewTransitions: {
      apiAvailable: typeof document.startViewTransition === 'function',
      apiReferencedInInlineScript,
    },
    inlineScriptsScanned,
  };
})()
