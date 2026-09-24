# Speculative Loading Check Specification (Speculation Rules / Prefetch / Prerender)

- **Issue:** `state-of-the-web-dpn`
- **Principle:** `be-fast-and-stable`
- **Check ID:** `speculative-loading`
- **Catalog Versioning:** Generation 2 (Option A: retains immutable July 58-check publication snapshot)

---

## 1. Overview & Motivation

Modern Web Guidance (`modern-web-guidance@0.0.172` and `0.0.190`) includes guidance for speculative loading in `performance/improve-next-page-load-performance`. However, the 58-check catalog contained zero checks referencing `speculationrules`, `prefetch`, or `prerender`.

Speculation Rules is a declarative web platform standard (supported across Chrome 110+, with document rules in Chrome 121+ and Resource Timing `deliveryType: navigational-prefetch` in Chrome 128+) that allows pages to define rules for prefetching and prerendering likely next navigations based on URL patterns, CSS selectors, and user interaction eagerness (`immediate`, `eager`, `moderate`, `conservative`).

Under **Option A**, the existing July run remains an immutable 58-check historical baseline, and this check is designed for the generation 2 catalog expansion.

---

## 2. Catalog Check Definition

Conforming strictly to the catalog check schema (which permits keys `['id', 'summary', 'detectableVia', 'guides', 'references']`, with principle-level applicability only):

```json
{
  "id": "speculative-loading",
  "summary": "Likely next navigations are speculatively loaded (Speculation Rules prefetch/prerender with an appropriate eagerness), rather than every navigation paying full cost.",
  "detectableVia": "HINT: the evaluate primitive inspects <script type=\"speculationrules\"> for valid JSON, prefetch/prerender rules, document vs list sources, and eagerness levels; checks HTMLScriptElement.supports('speculationrules'); a HAR summary or network trace can corroborate Sec-Purpose: prefetch/prerender, Sec-Speculation-Tags, or deliveryType: navigational-prefetch requests. The model chooses.",
  "guides": [
    "improve-next-page-load-performance"
  ]
}
```

*Note on schema:* The check does not declare a check-level `applicability` object (which is invalid under the catalog schema). Per AGENTS.md, checks use standard outcome statuses (`pass`, `issues`, `not-applicable`, `blocked`, `not-run`) accompanied by check-specific rationale.

---

## 3. Signal Detection & Probe Architecture

Speculative loading signals are captured through two complementary layers:

### A. In-Page Runtime Evaluation (`scripts/probes/speculative-loading.js`)

Evaluated in-page via `node ~/.web-uplift/evidence/cli.mjs evaluate <url> --expr-file scripts/probes/speculative-loading.js`:

1. **Platform Support**:
   - `HTMLScriptElement.supports('speculationrules')`
   - `'prerendering' in document` and `document.prerendering`
2. **Speculation Rules Elements**:
   - Locates `<script type="speculationrules">` and `<script type="speculation-rules">`.
   - Parses ruleset JSON: distinguishes `prefetch` vs `prerender`.
   - Inspects rule sources: `source: "list"` (with URL lists) vs `source: "document"` (with `where` selectors and pattern matching).
   - Extracts configured `eagerness` (`immediate`, `eager`, `moderate`, `conservative`).
   - Captures JSON syntax errors without crashing, reporting exact syntax errors and malformed snippets.
   - Bounded payloads: caps `rawRulesets` to max 10 entries and URL lists to max 10 entries to prevent multi-MB payload bloat on large sites.
3. **Resource Timing Signals**:
   - Inspects `performance.getEntriesByType('resource')` for `deliveryType: 'navigational-prefetch'`.
4. **Legacy Hints**:
   - Inspects `<link rel="prefetch">`, `<link rel="prerender">`, `<link rel="modulepreload">`.
5. **Navigation Context & Behavioral Applicability Probe**:
   - Counts total, internal, and external `<a href>` links.
   - Tests real link click navigation behavior: samples internal links and dispatches bubbling cancelable `click` events to test for client-side router interception (`event.defaultPrevented` and `history.pushState` calls).
   - Distinguishes server-rendered framework sites (which execute real document navigations) from client-side routed SPAs (which intercept navigation).
   - Identifies framework presence signatures (Next.js, Nuxt, Remix, SvelteKit, Gatsby, Angular, Vue SSR, React SPA, hash routers).

### B. Network & HAR Signals (`scripts/speculative_loading.py`)

Parses network HAR / CDP logs:

1. **Request Headers**:
   - `Sec-Purpose: prefetch` or `Sec-Purpose: prefetch;prerender`
   - `Purpose: prefetch`
   - `Sec-Speculation-Tags: <tag>`
2. **Response Headers**:
   - `Speculation-Rules: <url>` (speculation rules delivered via HTTP header)
   - `Link: <url>; rel="speculationrules"`
   - `Supports-Loading-Mode: credentialed-prerender` (enables cross-origin / authenticated prerendering)

---

## 4. Scoring & Conformance Rules

- **`pass`**:
  - Valid, actionable Speculation Rules declared via `<script type="speculationrules">` or HTTP headers for likely next navigations (list rules with target URLs or document rules on link-bearing pages), OR active speculative network requests evidenced in HAR/traces.
- **`not-applicable`**:
  - **Single-Surface View**: The page contains zero navigation links (e.g. isolated tool, calculator, single form, error page).
  - **External Links Only**: The page contains only external links; same-origin Speculation Rules do not apply without cross-origin target opt-in.
  - **Single-Page Application (SPA)**: The page exhibits verified client-side routed link navigation (internal links intercepted via `preventDefault()` / `pushState()`); internal view transitions do not use document navigations.
  - **Unmatched Document Rules**: Document rules declared on a page with zero navigation links to match.
- **`issues`**:
  - **Missing Speculation Rules**: A multi-page site with internal document navigation links does not configure Speculation Rules to prefetch or prerender likely next navigations (including server-rendered framework sites that execute real document navigations).
  - **Empty/Inert Ruleset**: Speculation rules script declared with no target URLs or document conditions.
  - **Syntax Error**: Malformed or invalid JSON in `<script type="speculationrules">`.
- **`blocked`**:
  - Anti-bot interstitial (HTTP 403 / Cloudflare / bot defense) prevented reaching or inspecting representative navigation routes.

---

## 5. Leak Safety & CLI Persistence

- **Profile Leak Prevention**: On subprocess timeout, `kill_profile()` scans output for `/tmp/web-uplift-cdp-*` and executes `pkill -f -- --user-data-dir={profile}` plus `shutil.rmtree` to prevent orphaned Chrome processes.
- **Persistence**: `scripts/speculative_loading.py` supports single-target and batch manifest execution with immediate per-site JSON persistence (`--out`).
