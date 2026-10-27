# page-proofs

Proofs for a built static site: tools that check its pages on your own machine before they go
out. Each tool has its own folder, with its full reference beside it; the other folders are the
small libraries the tools share.

## The tools

**lighthouse_run** measures the pages with a pinned Lighthouse, as interleaved runs reduced to
per-metric medians, and records exactly what it measured and with what. See
[lighthouse_run/lighthouse_run.md](lighthouse_run/lighthouse_run.md).

```bash
npm ci
python3.12 lighthouse_run/lighthouse_run.py --root . --serve "<built site>"
```

**browser_pass** renders the pages in pinned Chromium, Firefox and WebKit builds, at every
viewport a targets file names, light and dark, with scripts on and off, runs its page-load
detectors on every rendering, and compares each engine's layout with the first. See
[browser_pass/browser_pass.md](browser_pass/browser_pass.md).

```bash
python3.12 -m pip install ".[browser]"
python3.12 -m playwright install --only-shell chromium firefox webkit
python3.12 browser_pass/browser_pass.py --targets "<targets file>" --serve "<built site>"
```

## Requirements

- Python 3.12 or later. lighthouse_run runs on the standard library alone, and so does every
  library here; browser_pass needs Playwright, which the `browser` extra installs.
- For lighthouse_run, Node.js as Lighthouse 13.4.1 requires it (its `engines` field: 22.19 or
  later), and npm; and Google Chrome, which Lighthouse launches. `npm ci` and `npm test` also
  need Node.js as jsdom 30.0.1 requires it (its `engines` field: 22.22.2 or a later 22, 24.15.0
  or a later 24, or 26 or later).
- For browser_pass, the engine builds Playwright 1.63.0 pins, installed with
  `python3.12 -m playwright install` as above: about 800 MB for all three.

## Tests

```bash
python3.12 -m pip install ".[test]"
python3.12 lighthouse_run/test_lighthouse_run.py
python3.12 browser_pass/test_browser_pass.py
python3.12 csp_watch/test_csp_watch.py
python3.12 suite_select/test_suite_select.py
python3.12 bounded_run/test_bounded_run.py
python3.12 commit_facts/test_commit_facts.py
python3.12 help_formatter/test_help_formatter.py
python3.12 live_pid_guard/test_live_pid_guard.py
python3.12 loopback_site/test_loopback_site.py
python3.12 stdio_utf8/test_stdio_utf8.py
```

browser_pass's and csp_watch's live suites launch the engine builds, and skip, naming the install
command, where a build is not installed. To make a missing build fail them instead, set
`PAGE_PROOFS_REQUIRE_BROWSERS=1`. The in-page scripts' tests run under Node.js in jsdom:

```bash
npm ci
npm test
```

`npm run check` lints the JavaScript and HTML with prettier, html-validate and ESLint.

## License

AGPL-3.0-or-later. See `LICENSE`.
