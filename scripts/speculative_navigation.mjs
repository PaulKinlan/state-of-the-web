#!/usr/bin/env node
// Opt-in, one-link observation. Never chooses or follows links automatically.
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

export async function observeNavigation(client, url, href, expression, wait, navigate, evaluate, log) {
  await navigate(client, url, { settleMs: wait, log });
  const report = await evaluate(client, expression);
  if (!report?.ok || !report.url || /^(chrome-error:|about:)/.test(report.url)) {
    throw new Error(`navigation failed: ${report?.url || 'no document URL'}`);
  }
  const observation = {
    method: 'cdp-link-activation', type: 'unobserved',
    fromUrl: report.url, requestedHref: href,
    scope: 'one explicitly selected link; not a site-wide routing classification',
  };
  report.navigationContext.navigationObservation = observation;
  report.navigationContext.isClientSideRouted = null;
  const target = new URL(href, report.url);
  if (!['http:', 'https:'].includes(target.protocol) || target.origin !== new URL(report.url).origin || target.username || target.password) {
    observation.reason = 'Only an explicitly selected same-origin HTTP(S) link is allowed';
    return report;
  }
  observation.requestedHref = target.href;
  const point = await evaluate(client, `(() => {
    const links = [...document.querySelectorAll('a[href]')];
    const index = links.findIndex(link => link.href === ${JSON.stringify(target.href)});
    const link = links[index];
    if (!link) return { reason: 'Requested link not found' };
    const target = link.target || document.querySelector('base[target]')?.target || '_self';
    if (link.hasAttribute('download') || target.toLowerCase() !== '_self')
      return { reason: 'Downloads and other browsing contexts are not followed' };
    link.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
    const rect = link.getBoundingClientRect();
    const x = Math.max(0, rect.left) + Math.min(rect.width, innerWidth - Math.max(0, rect.left)) / 2;
    const y = Math.max(0, rect.top) + Math.min(rect.height, innerHeight - Math.max(0, rect.top)) / 2;
    if (!rect.width || !rect.height || document.elementFromPoint(x, y)?.closest('a') !== link)
      return { reason: 'Requested link is not visible or is obscured' };
    return { x, y, anchorIndex: index, sourceUrl: location.href };
  })()`);
  if (point.reason || point.sourceUrl !== report.url) {
    observation.reason = point.reason || 'Source URL changed before link activation';
    return report;
  }
  observation.anchorIndex = point.anchorIndex;
  const { frameTree } = await client.Page.getFrameTree();
  const before = frameTree.frame;
  observation.beforeLoaderId = before.loaderId;
  let wake;
  let timer;
  let httpStatus;
  const observed = new Promise(resolve => { wake = resolve; });
  const onDocument = event => {
    if (event.frame.id !== before.id || event.frame.loaderId === before.loaderId) return;
    observation.type = 'document';
    observation.afterLoaderId = event.frame.loaderId;
    wake();
  };
  const onSameDocument = event => {
    if (event.frameId !== before.id || observation.type === 'document') return;
    observation.type = event.navigationType === 'fragment' ? 'fragment-only' : 'same-document';
    observation.sameDocumentType = event.navigationType;
    wake();
  };
  const onResponse = event => {
    if (event.frameId === before.id && event.type === 'Document') httpStatus = event.response.status;
  };
  client.on('Page.frameNavigated', onDocument);
  client.on('Page.navigatedWithinDocument', onSameDocument);
  client.on('Network.responseReceived', onResponse);
  try {
    await client.Input.dispatchMouseEvent({ type: 'mousePressed', x: point.x, y: point.y, button: 'left', clickCount: 1 });
    await client.Input.dispatchMouseEvent({ type: 'mouseReleased', x: point.x, y: point.y, button: 'left', clickCount: 1 });
    await Promise.race([observed, new Promise(resolve => { timer = setTimeout(resolve, 3000); })]);
    await sleep(150);
    const after = (await client.Page.getFrameTree()).frameTree.frame;
    const afterUrl = after.url + (after.urlFragment || '');
    observation.toUrl = afterUrl;
    observation.afterLoaderId = after.loaderId;
    if (httpStatus !== undefined) observation.httpStatus = httpStatus;
    if (/^(chrome-error:|about:)/.test(after.url) || httpStatus >= 400) {
      observation.type = 'unobserved';
      observation.reason = 'Destination navigation failed';
    } else if (observation.type === 'same-document' &&
               (afterUrl !== target.href || afterUrl === report.url || after.loaderId !== before.loaderId)) {
      observation.type = 'unobserved';
      observation.reason = 'Same-document event did not reach the requested route in the original document';
    } else if (observation.type === 'fragment-only') {
      observation.reason = 'A fragment jump alone does not establish client-side routing';
    } else if (observation.type === 'unobserved') {
      observation.reason = 'No qualifying top-frame navigation within 3000 ms';
    }
    report.navigationContext.isClientSideRouted = observation.type === 'same-document' ? true
      : observation.type === 'document' ? false : null;
  } finally {
    clearTimeout(timer);
    client.removeListener('Page.frameNavigated', onDocument);
    client.removeListener('Page.navigatedWithinDocument', onSameDocument);
    client.removeListener('Network.responseReceived', onResponse);
  }
  return report;
}

async function main() {
  const { values, positionals } = parseArgs({ allowPositionals: true, options: {
    'follow-link': { type: 'string' },
    harness: { type: 'string', default: process.env.WEB_UPLIFT_CLI || join(homedir(), '.web-uplift/evidence/cli.mjs') },
    'expr-file': { type: 'string', default: join(dirname(fileURLToPath(import.meta.url)), 'probes/speculative-loading.js') },
    wait: { type: 'string', default: '2000' },
  } });
  const wait = Number(values.wait);
  if (positionals.length !== 1 || !values['follow-link'] || !Number.isFinite(wait) || wait < 0 || wait > 30000) {
    throw new Error('Usage: speculative_navigation.mjs <url> --follow-link <safe same-origin href> [--wait <0..30000 ms>]');
  }
  const { withSession, navigate, evaluate } = await import(pathToFileURL(join(dirname(resolve(values.harness)), 'cdp.mjs')).href);
  const expression = readFileSync(values['expr-file'], 'utf8');
  const report = await withSession(async client => {
    let timer;
    try {
      return await Promise.race([
        observeNavigation(client, positionals[0], values['follow-link'], expression, wait, navigate, evaluate, console.error),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Navigation observation exceeded 45000 ms')), 45000); }),
      ]);
    } finally { clearTimeout(timer); }
  }, { log: console.error });
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  try { await main(); } catch (error) { console.error(error.message); process.exitCode = 1; }
}
