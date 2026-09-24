#!/usr/bin/env node
// Drive a REAL back/forward navigation and report whether the page was restored
// from the back/forward cache — bead state-of-the-web-k26.
//
// A static page load cannot answer the bfcache question: the page must be
// visited, navigated away from, and returned to. This drives that sequence over
// CDP and reports three independent signals:
//
//   1. CDP FRAME — Page.frameNavigated for the return carries
//      `type: 'BackForwardCacheRestore'` when the document came back from the
//      cache, and the frame's loaderId is unchanged because the document was
//      never re-created. (Also the real restore signal; the bead named
//      `Page.getBackForwardCacheRestorationStatus`, which this Chrome does not
//      expose — checked at runtime and reported.)
//   2. PAGE — a `pageshow` recorder installed before navigation, and
//      PerformanceNavigationTiming.notRestoredReasons read by
//      scripts/probes/bfcache-restore.js.
//   3. CDP REASON — the Page.backForwardCacheNotUsed event, whose explanations
//      carry a TYPE. This is what makes the measurement usable:
//        PageSupportNeeded  the page must change (e.g. UnloadHandlerExistsInMainFrame)
//        SupportPending     the browser cannot cache it yet
//        Circumstantial     the environment evicted/flushed it (CacheFlushed,
//                           BrowsingInstanceNotSwapped) — NOT a page finding
//
// Two measured traps this driver exists to avoid:
//   - The away page decides eligibility. about:blank "sometimes produces
//     BrowsingInstanceNotSwapped failures" (Lighthouse's bf-cache gatherer), so
//     the default is `chrome://terms`, which is what DevTools and Lighthouse use.
//   - A Circumstantial reason is not stable: on a loaded machine the same clean
//     page both restored (BackForwardCacheRestore, pageshow persisted) and was
//     reported CacheFlushed with no restore on another run. The driver therefore
//     retries the whole sequence and only reports a page-attributable verdict
//     when a non-Circumstantial explanation appears.
//
// Usage:
//   node scripts/bfcache_probe.mjs <target-url> [away-url] [--out file]
//        [--settle ms] [--attempts n] [--headed] [--back cdp|page]
//
// Exit is non-zero when the sequence could not be driven at all; the JSON is
// still written with ok:false and the reason, so a failed drive can never be
// mistaken for a page that blocks bfcache.
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launchChrome, newSession, navigate, evaluate } from '/home/paulkinlan/.web-uplift/evidence/cdp.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const PROBE = resolve(HERE, 'probes/bfcache-restore.js');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const RECORDER = `(() => {
  window.__bfcache = { pageshow: 0, pagehide: 0, persisted: null, persistedOnHide: null, notRestoredReasons: null };
  addEventListener('pageshow', event => {
    window.__bfcache.pageshow++;
    window.__bfcache.persisted = event.persisted;
    const navigation = performance.getEntriesByType('navigation')[0];
    window.__bfcache.notRestoredReasons = navigation && 'notRestoredReasons' in navigation
      ? navigation.notRestoredReasons : undefined;
  });
  addEventListener('pagehide', event => {
    window.__bfcache.pagehide++;
    window.__bfcache.persistedOnHide = event.persisted;
  });
})()`;

function parseArgs(argv) {
  const positional = [];
  const options = { settle: 300, out: null, headless: true, backMode: 'cdp', attempts: 3 };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--out') options.out = argv[++i];
    else if (arg === '--settle') options.settle = Number(argv[++i]);
    else if (arg === '--attempts') options.attempts = Number(argv[++i]);
    else if (arg === '--headed') options.headless = false;
    else if (arg === '--back') options.backMode = argv[++i];
    else positional.push(arg);
  }
  return { target: positional[0], away: positional[1] || 'chrome://terms', options };
}

async function runAttempt(client, target, awayUrl, options) {
  const { Page } = client;
  const frames = [];
  const notUsed = [];
  const onFrame = event => frames.push({
    url: event.frame?.url || null,
    loaderId: event.frame?.loaderId || null,
    type: event.type || null,
  });
  const onNotUsed = event => notUsed.push(JSON.parse(JSON.stringify(event)));
  Page.frameNavigated(onFrame);
  Page.backForwardCacheNotUsed(onNotUsed);
  try {
    await Page.addScriptToEvaluateOnNewDocument({ source: RECORDER });
    await navigate(client, target, { settleMs: options.settle });
    const targetLoaderId = frames.at(-1)?.loaderId ?? null;

    const loaded = Page.loadEventFired();
    await Page.navigate({ url: awayUrl });
    await Promise.race([loaded, sleep(5000)]);
    await sleep(options.settle);
    const awayLoaderId = frames.at(-1)?.loaderId ?? null;

    const history = await Page.getNavigationHistory();
    const previous = history.entries[history.currentIndex - 1];
    if (!previous) throw new Error('no previous history entry to return to');

    const returned = new Promise(resolve => {
      const deadline = setTimeout(() => resolve(false), 8000);
      const check = () => {
        const last = frames.at(-1);
        if (last && last.url === target && last.loaderId !== awayLoaderId) {
          clearTimeout(deadline);
          resolve(true);
        } else {
          setTimeout(check, 100);
        }
      };
      setTimeout(check, 100);
    });
    if (options.backMode === 'page') await evaluate(client, 'history.back(); true');
    else await Page.navigateToHistoryEntry({ entryId: previous.id });
    const returnNavigationObserved = await returned;
    await sleep(200);

    const returnFrame = frames.at(-1) || {};
    const page = await evaluate(client, readFileSync(PROBE, 'utf8'));
    const document = await evaluate(client, 'window.__bfcache || null');
    const explanations = notUsed.flatMap(event => event.notRestoredExplanations || []);
    const restored = returnFrame.type === 'BackForwardCacheRestore'
      || document?.persisted === true
      || (targetLoaderId !== null && returnFrame.loaderId === targetLoaderId);
    return {
      attempt: null,
      returnNavigationObserved,
      returnNavigationType: returnFrame.type ?? null,
      loaderIdContinuity: { target: targetLoaderId, away: awayLoaderId, returned: returnFrame.loaderId ?? null,
        documentSurvived: targetLoaderId !== null && returnFrame.loaderId === targetLoaderId },
      page,
      document,
      explanations,
      restored,
      pageAttributable: explanations.some(explanation => explanation.type && explanation.type !== 'Circumstantial'),
      circumstantialOnly: explanations.length > 0 && explanations.every(explanation => explanation.type === 'Circumstantial'),
    };
  } finally {
    Page.frameNavigated.off?.(onFrame);
    Page.backForwardCacheNotUsed.off?.(onNotUsed);
  }
}

async function main() {
  const { target, away, options } = parseArgs(process.argv.slice(2));
  const report = {
    probe: 'bfcache-drive',
    ok: false,
    target,
    away,
    attempts: [],
    checkedAt: new Date().toISOString(),
    cdp: {},
    page: null,
    restoredFromBfcache: null,
    pageAttributableBlocker: null,
    verdict: null,
    error: null,
  };
  if (!target) {
    report.error = 'usage: node scripts/bfcache_probe.mjs <target-url> [away-url] [--out file]';
    process.stdout.write(JSON.stringify(report, null, 2) + '\n');
    return 2;
  }

  let chrome = null;
  let session = null;
  try {
    chrome = await launchChrome({ headless: options.headless });
    session = await newSession(chrome.port, {});
    const client = session.client;
    report.cdp.backForwardCacheRestorationStatusMethod =
      typeof client.Page.getBackForwardCacheRestorationStatus === 'function';

    for (let attempt = 1; attempt <= Math.max(1, options.attempts); attempt++) {
      const record = await runAttempt(client, target, away, options);
      record.attempt = attempt;
      report.attempts.push(record);
      if (record.restored || record.pageAttributable) break;
    }

    const last = report.attempts.at(-1);
    report.page = last.page;
    report.document = last.document;
    report.cdp = { ...report.cdp, ...last, attempts: undefined, page: undefined, document: undefined };
    report.restoredFromBfcache = report.attempts.some(record => record.restored);
    const blocker = report.attempts.flatMap(record => record.explanations)
      .find(explanation => explanation.type && explanation.type !== 'Circumstantial');
    report.pageAttributableBlocker = blocker || null;
    report.verdict = report.restoredFromBfcache
      ? 'restored-from-bfcache'
      : blocker
        ? 'not-restored-page-attributable'
        : report.attempts.some(record => record.circumstantialOnly)
          ? 'inconclusive-environment-eviction'
          : 'not-restored-unexplained';
    report.ok = true;
  } catch (error) {
    report.error = `${error.name || 'Error'}: ${error.message}`;
  } finally {
    try { if (session) await session.close(); } catch { /* ignore */ }
    try { if (chrome) await chrome.close(); } catch { /* ignore */ }
  }

  try { if (session) await session.close(); } catch { /* ignore */ }
  if (options.out) writeFileSync(options.out, JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
  return report.ok ? 0 : 1;
}

process.exit(await main());
