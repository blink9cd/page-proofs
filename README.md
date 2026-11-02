# page-proofs

Proofs for a built static site: tools that check its pages on your own machine before they go
out. Each tool has its own folder, with its full reference beside it; the other folders are the
small libraries the tools share.

## Install

Needs Python 3.12 or newer. Install into a virtual
environment, because Homebrew and Debian Python refuse a
system-wide `pip install`:

```bash
python3 -m venv .venv
# shellcheck source=/dev/null # the activate script exists only once the line above has run
source .venv/bin/activate
pip install '.[browser,print]'
```

The commands below assume the venv is active, so `python`
is the venv's interpreter.

The two extras hold the Python packages the tools need, each pinned exactly in
`pyproject.toml`: `browser` for browser_pass, and `print` for print_pass. lighthouse_run needs
neither, so to use only one tool, install only its extra, or plain `pip install .` for
lighthouse_run. Installing puts no code anywhere: run each tool from its folder in this
repository, as below.

## The tools

**lighthouse_run** measures the pages with a pinned Lighthouse, as interleaved runs reduced to
per-metric medians, and records exactly what it measured and with what. See
[lighthouse_run/lighthouse_run.md](lighthouse_run/lighthouse_run.md).

```bash
npm ci --ignore-scripts
python lighthouse_run/lighthouse_run.py --root . --serve "<built site>"
```

**browser_pass** renders the pages in pinned Chromium, Firefox and WebKit builds, at every
viewport a targets file names, light and dark, with scripts on and off, runs its page-load
detectors on every rendering, and compares each engine's layout with the first. See
[browser_pass/browser_pass.md](browser_pass/browser_pass.md).

```bash
python -m playwright install --only-shell chromium firefox webkit
python browser_pass/browser_pass.py --targets "<targets file>" --serve "<built site>"
```

**print_pass** prints the pages to PDF, in every state a targets file names, through
Playwright's pinned Chromium headless shell, installed Google Chrome, or installed Firefox
through geckodriver's WebDriver Print; reads any PDF with poppler, pypdf and pdfminer; and
compares two PDFs' renders. See [print_pass/print_pass.md](print_pass/print_pass.md).

```bash
python -m playwright install --only-shell chromium
python print_pass/print_pass.py --mode make --serve "<built site>" \
  --targets "<targets file>" --out "$(mktemp -d)/print"
```

## Requirements

- Python 3.12 or later. lighthouse_run runs on the standard library alone, and so does every
  library here; browser_pass needs Playwright, which the `browser` extra installs; print_pass
  needs pypdf, pdfminer.six and Playwright, which the `print` extra installs.
- For lighthouse_run, Node.js as Lighthouse 13.4.1 requires it (its `engines` field: 22.19 or
  later), and npm; and Google Chrome, which Lighthouse launches. `npm ci` and `npm test` also
  need Node.js as jsdom 30.0.1 requires it (its `engines` field: 22.22.2 or a later 22, 24.15.0
  or a later 24, or 26 or later).
- For browser_pass, the engine builds Playwright 1.63.0 pins, installed with
  `python -m playwright install` as above: about 800 MB for all three.
- For print_pass, in every mode, poppler's `pdfinfo`, `pdffonts`, `pdfimages` and `pdftoppm`
  on `PATH` (Homebrew's `poppler`, Debian's and Ubuntu's `poppler-utils`); for its default
  engine, the headless shell Playwright 1.63.0 pins, installed as above; and for its Firefox
  engine, macOS, with geckodriver and Firefox installed. print_pass is tested on macOS only.

## Tests

The `test` extra holds every package the suites import, the `browser` and `print` pins
included. Install it into the same venv, then run each suite:

```bash
pip install '.[test]'
python lighthouse_run/test_lighthouse_run.py
python browser_pass/test_browser_pass.py
python print_pass/test_print_pass.py
python csp_watch/test_csp_watch.py
python suite_select/test_suite_select.py
python bounded_run/test_bounded_run.py
python commit_facts/test_commit_facts.py
python help_formatter/test_help_formatter.py
python live_pid_guard/test_live_pid_guard.py
python loopback_site/test_loopback_site.py
python stdio_utf8/test_stdio_utf8.py
```

browser_pass's, csp_watch's and print_pass's live suites launch the engine builds, and skip,
naming the install command, where a build is not installed, print_pass's also where poppler is
not. To make a missing build or poppler fail them instead, set
`PAGE_PROOFS_REQUIRE_BROWSERS=1`. The in-page scripts' tests run under Node.js in jsdom:

```bash
npm ci --ignore-scripts
npm test
```

`npm run check` lints the JavaScript and HTML with prettier, html-validate and ESLint.

## License

AGPL-3.0-or-later. See `LICENSE`.
