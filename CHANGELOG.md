# Changelog

Notable changes to this project, newest first.

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
