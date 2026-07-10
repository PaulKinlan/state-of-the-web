# Proposed New Tests for web-uplift

## 1. Secrets / API Key Detection (be-private-and-secure) — IMPLEMENTED

**Status:** Built and running (`results/gpt/secrets_scan.mjs`)

Scans page HTML, inline scripts, external JS resources, and meta tags for exposed:
- AWS Access Keys (`AKIA...`) and secret keys
- Google API Keys (`AIza...`)
- Stripe live keys (`sk_live_...`, `pk_live_...`)
- GitHub tokens (`ghp_...`)
- Slack tokens (`xox[baprs]-...`)
- JWTs (`eyJ...eyJ...`)
- Private keys (`-----BEGIN ... PRIVATE KEY-----`)
- Database connection strings with credentials (`mongodb://user:pass@...`)
- Generic API keys/secrets (32+ char strings near `api_key`/`apikey`/`secret`)
- Bearer/Authorization tokens
- Facebook/Twitter app secrets

Merges into `be-private-and-secure` principle — upgrades to "issues" if secrets found.

**To add to web-uplift:** New `secrets` evidence primitive in the CLI.

---

## 2. CSP & Security Headers Analysis (be-private-and-secure)

Check response headers for:
- Content-Security-Policy (present? permissive? `unsafe-inline`/`unsafe-eval`?)
- Strict-Transport-Security (HSTS) — max-age, includeSubDomains, preload
- X-Content-Type-Options: nosniff
- X-Frame-Options / CSP frame-ancestors (clickjacking protection)
- Referrer-Policy
- Permissions-Policy

**Method:** CDP `Network.getResponseHeaders` on the main document response. Already partially captured in HAR files — parse those.

---

## 3. Cookie Security Audit (be-private-and-secure)

Inspect all cookies set by the page:
- Secure flag (HTTPS-only)?
- SameSite (Strict/Lax/None)?
- HttpOnly (not accessible to JS)?
- Excessive expiry (tracking cookies lasting years)?
- Third-party cookies (tracking)

**Method:** CDP `Network.getCookies` after page load.

---

## 4. Third-Party Tracker Enumeration (be-private-and-secure / be-sustainable)

Enumerate all third-party domains the page connects to:
- Script origins, iframe origins, beacon/analytics endpoints
- Known tracker matching (EasyPrivacy/Disconnect list)
- Count of distinct third parties (privacy footprint)
- Total data sent to third parties (bytes)

**Method:** CDP `Network.requestWillBeSent` — collect all request domains, compare to first-party, match against tracker blocklist.

---

## 5. Image Optimization Check (be-sustainable / be-fast-and-stable)

- Images without `width`/`height` attributes (CLS contributor)
- Images without `loading="lazy"` below the fold
- Oversized images (decoded dimensions >> displayed dimensions)
- Missing `srcset` / responsive images
- AVIF/WebP vs JPEG/PNG (modern formats?)
- Images served without proper `Cache-Control`

**Method:** CDP `evaluate` — inspect `<img>` elements, compare `naturalWidth` to `getBoundingClientRect().width`.

---

## 6. Color Contrast Automation (be-inclusive)

Automated WCAG color contrast check beyond what axe catches:
- Sample text elements, compute contrast ratio against background
- Flag ratios below 4.5:1 (normal text) or 3:1 (large text)
- Check focus-visible contrast

**Method:** CDP `evaluate` — `getComputedStyle` on text elements, compute relative luminance, compare. (axe does this but only for detected text — a broader sample catches more.)

---

## 7. Touch Target Sizing (be-inclusive / adapt-to-the-form-factor)

On mobile viewport:
- Interactive elements (buttons, links) smaller than 44×44 CSS pixels
- Spacing between adjacent targets (< 8px gap)
- Tap targets overlapping

**Method:** CDP with mobile emulation — `evaluate` to measure `getBoundingClientRect()` of all interactive elements.

---

## 8. Font Loading Performance (be-fast-and-stable / be-sustainable)

- `font-display` strategy (`swap`/`optional`/`fallback` vs `block`)
- FOIT (flash of invisible text) vs FOUT (flash of unstyled text)
- Self-hosted vs Google Fonts / third-party font CDN
- Number of font files / total font bytes
- Variable fonts (more efficient?)

**Method:** CDP `evaluate` — inspect `FontFace` loading, `document.fonts`, and CSS `@font-face` rules.

---

## 9. Service Worker / PWA Detection (be-resilient)

- Does the site register a service worker?
- Is it installable (manifest + SW)?
- Offline fallback behavior
- Cache strategy (cache-first, network-first, stale-while-revalidate)

**Method:** CDP `evaluate` — check `navigator.serviceWorker.controller`, fetch `/manifest.json`, check for `beforeinstallprompt` support.

---

## 10. Form Autofill Correctness (be-trustworthy / be-inclusive)

- Form fields with correct `autocomplete` attributes
- Input types (`type="email"`, `type="tel"`, `type="url"`)
- Labels properly associated (`<label for>` or wrapping)
- Required field indication
- Error handling (inline validation, not just on submit)

**Method:** CDP `evaluate` — inspect `<form>` and `<input>` elements.

---

## Android Folder Picker (separate question)

`showDirectoryPicker()` (File System Access API) is **not** supported on Android Chrome — it's desktop-only (Chrome/Edge on Windows/macOS/Linux).

However, **`<input type="file" webkitdirectory>` IS supported on Android Chrome** — it opens the system folder picker and returns all files in the selected directory. This is the cross-browser way to do folder selection on mobile.

For full File System Access on Android, the Storage Access API (`showOpenFilePicker` for directories via `ACTION_OPEN_DOCUMENT_TREE`) is the native equivalent but doesn't have a direct web API mapping yet.
