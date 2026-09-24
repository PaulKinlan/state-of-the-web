#!/usr/bin/env node
// One navigation, one CSS probe, bounded inspection of already-loaded scripts.
// Uses web-uplift's raw-CDP harness; never re-fetches a URL or changes page APIs.
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { homedir } from 'node:os';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const LIMITS = {
  scripts: 32,
  perScriptBytes: 1024 * 1024,
  totalBytes: 4 * 1024 * 1024,
  bodyReadMs: 5000,
  commandMs: 1000,
  navigationMs: 45000,
};

function bounded(promise, ms) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('CDP deadline exceeded')), ms); }),
  ]).finally(() => clearTimeout(timer));
}

function sourceUrl(value) {
  // Retain source location, not query tokens, URL credentials or inline payloads.
  try {
    const url = new URL(value);
    if (!['http:', 'https:', 'file:'].includes(url.protocol)) return `${url.protocol}[omitted]`;
    url.username = url.password = url.search = url.hash = '';
    return url.href.slice(0, 2048);
  } catch {
    return '[invalid URL]';
  }
}

async function collect(client, url, expression, wait, navigate, evaluate, log) {
  await client.Network.enable({
    maxResourceBufferSize: LIMITS.perScriptBytes,
    maxTotalBufferSize: LIMITS.totalBytes,
  });
  const { frameTree } = await client.Page.getFrameTree();
  const frameId = frameTree.frame.id;
  const records = new Map();
  let capturing = true;
  let omittedRequestEvents = 0;
  client.Network.requestWillBeSent(event => {
    if (!capturing || event.type !== 'Script' || event.frameId !== frameId) return;
    if (!records.has(event.requestId) && records.size >= LIMITS.scripts) {
      omittedRequestEvents++;
      return;
    }
    // Redirects reuse a request id: inspect only the final response body.
    records.set(event.requestId, {
      url: sourceUrl(event.request.url),
      status: 'pending',
      receivedBytes: 0,
      finished: false,
    });
  });
  client.Network.responseReceived(event => {
    if (!capturing) return;
    const record = records.get(event.requestId);
    if (record) record.httpStatus = event.response.status;
  });
  client.Network.dataReceived(event => {
    if (!capturing) return;
    const record = records.get(event.requestId);
    if (record) record.receivedBytes += event.dataLength;
  });
  client.Network.loadingFinished(event => {
    if (!capturing) return;
    const record = records.get(event.requestId);
    if (record) record.finished = true;
  });
  client.Network.loadingFailed(event => {
    if (!capturing) return;
    const record = records.get(event.requestId);
    if (record) {
      record.status = 'unreadable';
      record.reason = 'network-failed';
      if (event.blockedReason) record.blockedReason = event.blockedReason;
    }
  });

  await bounded(navigate(client, url, { settleMs: wait, log }), LIMITS.navigationMs);
  capturing = false;
  const report = await bounded(evaluate(client, expression), LIMITS.navigationMs);
  if (!report?.ok || !report.url || /^(chrome-error:|about:)/.test(report.url)) {
    throw new Error(`navigation failed: ${report?.url || 'no document URL'}`);
  }

  let bytesInspected = 0;
  const deadline = Date.now() + LIMITS.bodyReadMs;
  for (const [requestId, record] of records) {
    if (record.status === 'unreadable') continue;
    let reason = !record.finished ? 'not-finished' : null;
    if (record.httpStatus >= 400) reason = 'http-error';
    if (record.receivedBytes > LIMITS.perScriptBytes) reason = 'per-script-byte-limit';
    if (bytesInspected + record.receivedBytes > LIMITS.totalBytes) reason = 'total-byte-limit';
    if (Date.now() >= deadline) reason = 'body-read-time-limit';
    if (reason) {
      record.status = 'unreadable';
      record.reason = reason;
      continue;
    }
    try {
      const response = await bounded(client.Network.getResponseBody({ requestId }),
        Math.min(LIMITS.commandMs, deadline - Date.now()));
      const body = response.base64Encoded ? Buffer.from(response.body, 'base64').toString('utf8') : response.body;
      const bytes = Buffer.byteLength(body, 'utf8');
      // Re-check actual decoded size: Content-Length can be absent or compressed.
      if (bytes > LIMITS.perScriptBytes || bytesInspected + bytes > LIMITS.totalBytes) {
        record.status = 'unreadable';
        record.reason = bytes > LIMITS.perScriptBytes ? 'per-script-byte-limit' : 'total-byte-limit';
        continue;
      }
      bytesInspected += bytes;
      record.status = 'inspected';
      record.bytesInspected = bytes;
      // A string/comment/dead branch can mention this name. Do NOT label a
      // textual hit as a call, runtime usage, or a CSS-family adoption signal.
      record.containsApiName = body.includes('startViewTransition');
    } catch {
      record.status = 'unreadable';
      record.reason = 'body-unavailable-or-deadline';
    }
  }
  const sources = [...records.values()].map(({ finished, ...record }) => record);
  const inspected = sources.filter(source => source.status === 'inspected').length;
  report.scriptInspection = {
    version: 1,
    method: 'CDP Network.getResponseBody',
    scope: 'top-frame Script requests during navigation and settle; not workers, child frames, or later interactions',
    limits: LIMITS,
    settleMs: wait,
    captured: sources.length,
    inspected,
    unreadable: sources.length - inspected,
    omittedRequestEvents,
    partial: inspected !== sources.length || omittedRequestEvents > 0,
    bytesInspected,
    sources,
  };
  report.viewTransitions.apiReferencedInExternalScript = sources.some(source => source.containsApiName);
  report.viewTransitions.runtimeUsage = 'not-measured';
  return report;
}

try {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      harness: { type: 'string', default: process.env.WEB_UPLIFT_CLI || join(homedir(), '.web-uplift/evidence/cli.mjs') },
      'expr-file': { type: 'string', default: join(dirname(fileURLToPath(import.meta.url)), 'probes/modern-web-features.js') },
      out: { type: 'string' },
      wait: { type: 'string', default: '3000' },
      quiet: { type: 'boolean', default: false },
    },
  });
  const wait = Number(values.wait);
  if (positionals.length !== 1 || !Number.isFinite(wait) || wait < 0 || wait > 30000) {
    throw new Error('Usage: collect_modern_web.mjs <url> [--out <file>] [--wait <0..30000 ms>] [--harness <cli.mjs>]');
  }
  const { withSession, navigate, evaluate } = await import(pathToFileURL(join(dirname(resolve(values.harness)), 'cdp.mjs')).href);
  const expression = readFileSync(values['expr-file'], 'utf8');
  const log = values.quiet ? () => {} : message => console.error(message);
  const report = await withSession(client => collect(client, positionals[0], expression, wait, navigate, evaluate, log), { log });
  const text = JSON.stringify(report, null, 2) + '\n';
  if (values.out) writeFileSync(values.out, text);
  process.stdout.write(text);
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
