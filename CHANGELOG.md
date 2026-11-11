# Changelog

Notable changes to this project, newest first.

## 0.3.0 - 2026-11-10

### Added

- `print_pass` targets files take a `set_attribute` action, such as
  `{ set_attribute = { selector = ":root", name = "data-print-theme" } }`. It sets one
  attribute, with an optional `value` that defaults to empty, on the first element the
  selector matches, once and without waiting. It is for a page whose own print button sets
  an attribute and then calls `window.print()`: under Firefox, a WebDriver click on such a
  button does not return while the print dialog is open, so the state takes that first
  step itself. A selector that matches nothing stops the state with the same finding a
  failed click gives, and an attribute name outside XML's Name production, restricted to
  ASCII, is refused when the targets file is read. The report shows the action with its
  value as a JSON string.

### Changed

- A targets file whose action selector or `set_attribute` value holds a control character,
  a newline or tab included, is refused when it is read, naming the key and the character.
- The report passes each action and each stored value through the same scrub as its
  findings, so a home path in one reads as `~` or `<home>`.

## 0.2.3 - 2026-11-10

### Changed

- The license is now two files. `LICENSE` holds the GNU AGPL v3 text exactly as the Free
  Software Foundation publishes it, so GitHub and other tools recognize it. `NOTICE` holds
  the copyright line and the "or any later version" grant. The terms are unchanged:
  AGPL-3.0-or-later.

## 0.2.2 - 2026-11-09

### Added

- `bounded_run`'s `run_bounded` takes `inherit_stderr=True`, which gives the child the
  caller's own stderr instead of a pipe, so a long run's progress lines appear as they
  are written rather than when it ends. The result's `stderr` is then `""`.

### Fixed

- `browser_pass`'s live extension test no longer reads its guarded page before the
  WebRTC probe has a verdict. The probe now changes the page's layout signature until it
  writes one, so the settle waits for it.
- Two of `bounded_run`'s tests failed on Windows and now run only where their subject
  does. The check that an unkillable `ps` is abandoned runs on POSIX alone, as its only
  caller does, and the late-tracking live test waits for a grandchild process on POSIX
  alone, since on Windows the child is still suspended at that point.

## 0.2.1 - 2026-11-02

### Changed

- `browser_pass`, `print_pass`, and `lighthouse_run` no longer show `(default: None)` or
  `(default: False)` in `--help` for an option or flag that has no default to give.

### Fixed

- `print_pass --engine firefox` no longer says in its report that Firefox's PDF is always
  untagged and never carries a `Title`. Firefox 159 on macOS tags the PDF and writes the page's
  title as its `Title`, so the report's `cannot_control` now says that both are the Firefox
  build's choice. `print_pass.md` and the sample's README describe what Firefox 159 was
  measured to print, and `print_pass.md` says that the report's `tagged` is what was
  requested, while each PDF's `metadata.tagged` is what the build did.

### Documentation

- The README starts with an install step into a virtual environment, because Homebrew and
  Debian Python refuse a system-wide `pip install`. The commands in the README, in each tool's
  reference and samples, and in the live suites' install hints run the venv's `python` rather
  than `python3.12`.
- The docs, comments, docstrings, and `--help` text are reworded without dashes: the spaced
  double hyphens used as dashes are gone, and so are the em dashes, most of them in
  `bounded_run.md` and `stdio_utf8.md`, apart from a few that stand for an empty table cell or
  are test data. Scattered bold and capitals are gone from the docs. The shared libraries' docs
  no longer describe the larger repository they came from.
- `pyproject.toml` no longer holds settings for development tools that do not ship here.

## 0.2.0 - 2026-10-27

### Added

- `print_pass`, which prints a site's pages to PDF through Playwright's Chromium or through
  Firefox, reads any PDF back, and compares two.
- The `print` extra, which pins `print_pass`'s PDF readers and Playwright:
  `pip install ".[print]"`.

### Changed

- The `browser` extra also pins `typing-extensions`, which Playwright needs, so pip no
  longer picks its version freely.
- The install steps run `npm ci --ignore-scripts`: no package the lockfile pins needs an
  install script of its own, and a test holds the lockfile to that.

### Fixed

- `browser_pass --loopback-only` now lets a page reach only the served origin in Chromium and
  Firefox. In 0.1.0 a script in the page could reach any other port on 127.0.0.1, such as a
  local development server. WebKit still can, as `browser_pass/browser_pass.md` explains.
- A stop or deadline kill no longer hangs when macOS `ps` is stuck in an uninterruptible
  kernel wait under heavy load: the process snapshot is abandoned after one second, and the
  groups already found are killed as they stand.
