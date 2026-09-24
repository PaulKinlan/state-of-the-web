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

```json
{
  "principleId": "be-fast-and-stable",
  "checkId": "speculative-loading",
  "summary": "Likely next navigations are speculatively loaded (Speculation Rules prefetch/prerender with an appropriate eagerness), rather than every navigation paying full cost.",
  "detectableVia": "HINT: the evaluate primitive inspects <script type=\"speculationrules\"> for valid JSON, prefetch/prerender rules, document vs list sources, and eagerness levels; checks HTMLScriptElement.supports('speculationrules'); a HAR summary or network trace can corroborate Sec-Purpose: prefetch/prerender, Sec-Speculation-Tags, or deliveryType: navigational-prefetch requests. The model chooses.",
  "applicability": {
    "expectation": "contextual",
    "description": "Applies to multi-page sites and applications with internal navigation links. Single-page applications that execute purely client-side transitions without navigation, or standalone single-surface tools without internal navigation journeys, are legitimately not-applicable with an explicit rationale."
  },
  "guides": [
    "improve-next-page-load-performance",
    "speculative-loading-speculation-rules",
    "prerender-pages-chrome"
  ]
}
```

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
3. **Resource Timing Signals**:
   - Inspects `performance.getEntriesByType('resource')` for `deliveryType: 'navigational-prefetch'`.
4. **Legacy Hints**:
   - Inspects `<link rel="prefetch">`, `<link rel="prerender">`, `<link rel="modulepreload">`.
5. **Navigation Context**:
   - Counts total and internal `<a href>` links to establish multi-page applicability.

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
  - Valid Speculation Rules declared via `<script type="speculationrules">` or HTTP headers for likely next navigations (list or document rules), OR active speculative network requests evidenced in HAR/traces.
- **`issues`**:
  - The site has internal navigation flows but provides no Speculation Rules (relying solely on legacy `<link rel="prefetch">` or no speculative loading).
  - Malformed or invalid JSON in `<script type="speculationrules">`.
- **`not-applicable`**:
  - The page is a single-surface utility (e.g. calculator, single form, error page) with 0 navigation links, or an SPA that performs client-side transitions without navigation.
- **`blocked`**:
  - Anti-bot interstitial (HTTP 403 / Cloudflare / bot defense) prevented reaching or inspecting representative navigation routes.

---

## 5. Verification & Test Evidence

Tested against headless Chrome 134+ over local fixtures (`scripts/test_speculative_loading.py`):

1. **Speculation Rules Fixture** (`scripts/fixtures/speculative-loading.html`):
   - Correctly detects 2 prefetch rules (list + document with eagerness `eager` and `moderate`) and 1 prerender rule (`immediate`).
2. **Malformed Fixture** (`scripts/fixtures/invalid-speculation-rules.html`):
   - Captures JSON parse errors safely, isolates broken script, marks `hasSpeculationRules: false`, produces `issues` verdict.
3. **Plain Page Fixture** (`scripts/fixtures/plain-page.html`):
   - Zero false positives on plain HTML; produces `not-applicable` verdict when link count is 0.
4. **HAR Network Analyzer**:
   - Successfully extracts `Sec-Purpose`, `Sec-Speculation-Tags`, `Speculation-Rules`, and `Supports-Loading-Mode`.
