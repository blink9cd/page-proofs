# csp_watch

A library with no CLI: it collects every Content Security Policy violation a page reports, for
the tools here that drive a browser engine through Playwright. It records what the engine says
was refused; whether a refusal is a finding is the caller's call.

## What it collects

Each `securitypolicyviolation` event a document fires becomes one `Violation`:

| Field       | What it holds                                                                              |
| ----------- | ------------------------------------------------------------------------------------------ |
| `document`  | the reporting document's path and query (`location.pathname + location.search`)            |
| `directive` | the event's `effectiveDirective`, such as `style-src-attr` or `img-src`                    |
| `blocked`   | the event's `blockedURI` as the engine gives it: `inline`, `eval`, a URL, or empty         |
| `sample`    | the refused text's first 40 characters, empty unless the policy asks for `'report-sample'` |

`describe()` is one line for a report: `style-src-attr blocked inline on /404.html`, with
`: <sample>` after it when there is one.

## Using it

```python
log = csp_watch.install(context)  # before the context's first new_page()
page = context.new_page()
page.goto(url)
# ... wait for the page to settle ...
findings = [violation.describe() for violation in log.violations]
```

| Name                       | What it is                                                                          |
| -------------------------- | ----------------------------------------------------------------------------------- |
| `install(context)`         | exposes the binding, then adds the listener as an init script; returns the `CspLog` |
| `install(context, admit=)` | the same, with a log keeping only the reports whose caller `admit` admits           |
| `CspLog.violations`        | every violation so far, in arrival order                                            |
| `CspLog.mark()`            | how many so far: take it before a plant to split the log there                      |
| `policy_of(page)`          | the content of the page's first policy `<meta>`, or `None`                          |
| `plant(page)`              | adds a policy of its own to the page, then breaks it                                |
| `plant_caught(log, since)` | whether a violation after the first `since` carries the plant's sample              |
| `CspWatchError`            | an answer from the page the in-page code never gives: an instrument fault           |
| `ContextLike`              | what `install` takes: anything with `expose_binding` and `add_init_script`          |
| `PageLike`                 | what `policy_of` and `plant` take: anything with `evaluate`                         |
| `Admit`                    | what `admit` is: given the caller Playwright hands the binding, whether to keep it  |
| `SAMPLE_LIMIT`             | the most characters of a `sample` the log admits: the listener's own cut            |
| `URL_LIMIT`                | the most characters of each other value the log admits, sized for a URL             |

The two protocols hold only the calls the module makes, so Playwright's own `BrowserContext` and
`Page` are handed over as they are, and so is any object of a caller's that makes the same calls:
a wrapper that runs each call inside a watchdog's bound, say. The unit suite has mypy check both
Playwright's classes and its own stand-ins against them, with no cast.

A violation is reported as a task the engine queues after the refusal, and its call then crosses
the driver, so it reaches the log some time after the code that caused it returns. Read the log
once it has stopped changing, or poll `plant_caught`; never at a fixed delay. Each read waits in
a Playwright call, which is what lets the binding's calls be dispatched to the log.

## Why a binding

The listener sends each violation out of the page the moment it fires, through a binding
Playwright exposes. A record kept in the page and read at the end is erased by the next
navigation, and a meta refresh is one: measured on a page that redirects, an in-page record read
at the end held nothing, while the binding had caught the one violation before the redirect.
Every page of the context reports to the same log.

So a caller measuring one page among others hands `install` an `admit`. Playwright hands a
binding's callback its caller first, a mapping naming the `context`, `page` and `frame` that
called, and `admit` is given that: a caller keeping the pages it opened admits a report when its
`page` is one of them. A report not admitted is dropped unread, so its payload is never a fault
either: it was not the measured page's, so not the instrument's. browser_pass does this, since a
browser extension can open a tab of its own beside the page measured, and that tab's violations
are the extension's own.

In the sync API the binding's callback runs on Playwright's dispatcher, which is a greenlet on the
caller's own thread, and only while the caller is inside a Playwright call. So it never runs
beside a read of the log, and the log needs no lock.

## Scripts off: nothing arrives, and the console still says so

The listener is an init script, and an engine runs no init script with the page's scripts off.
With them off the log stays empty whatever the page violates, so a caller must report such a
rendering as not measured, never as clean. The live suite pins that empty log in all three
engines.

Every engine still logs each violation to the console with scripts off: measured on the
fixture's policy page in the pinned builds, one console error for its one refused style
attribute in each of Chromium, Firefox and WebKit, with scripts on and off alike. A caller that
already collects console errors therefore still sees a violation there, without its fields.

`policy_of` works either way, because an engine still runs the code the driver sends with the
page's scripts off.

## The plant

`plant` shows the collector catches what it is meant to, on the page being measured. It appends
a policy `<meta>` to the head, `style-src 'self' 'report-sample'`, then sets
`style="--csp-watch-plant: 1"` on the root element. The attribute breaks the planted policy, so
a watched page reports one `style-src-attr` violation whose sample is that attribute's text,
and `plant_caught` looks for exactly that sample after a `mark()`. The attribute sets a custom
property only, so it changes nothing on the page even where no policy blocks it.

`'report-sample'` is what makes the engine send the refused text at all, and the text is what
tells the plant's violation from any the page makes itself. It sits in `style-src`, not in
`style-src-attr`, because of what the pinned builds measured: Chromium and Firefox fill the
sample from either directive's keyword, but WebKit fills it only from `style-src`'s, reporting an
empty sample for `style-src-attr 'none' 'report-sample'` (and logging that `'none'` beside
another source is invalid). All three report the attribute's violation as `style-src-attr`
whichever directive held the keyword. `'self'` keeps the plant from refusing a same-origin
stylesheet the page loads after it.

The planted policy stays in force for the life of the document, so it refuses every style
attribute and inline `<style>` the page adds after it: measured in all three pinned builds, each
is one more violation after `since` (`style-src-attr`, `style-src-elem`), and neither styles the
page. Plant last on a rendering, once its measurement is taken.

A plant needs the page's scripts on, since only the listener reports it.

## Instrument faults

The listener sends an object of exactly four strings. Anything else, such as a missing or extra
key or a value that is not text, is an instrument fault, and `record` raises `CspWatchError`.
Raising there alone would lose it: Playwright hands a binding callback's exception back to the
page, as the binding call's rejection, so it never reaches the code measuring. The log therefore
keeps the first fault and raises it again from `violations` and `mark()`.

The listener hands on a `sample` that is not a string exactly as it came, rather than cutting it,
so an engine that reports none is refused here as a fault instead of throwing in the page and
losing the violation unseen. It cuts a sample by character, never inside a UTF-16 surrogate pair.

Each value is bounded too, since a page's own script can call the binding with anything. A
`sample` longer than the listener's cut (`SAMPLE_LIMIT`), or any other value longer than
`URL_LIMIT`, which is sized for a URL, is refused as an instrument fault, exactly as a value that
is not text is. The fault names the fields alone, so an over-long value never reaches a report.

## What it leaves in the page

One global: the binding `__cspWatch` (`BINDING`). The listener's function, `cspWatch`, stays in
the init script's own scope, because the pinned Playwright runs every init script inside an
arrow function of its own: measured after `install` in Chromium, Firefox and WebKit,
`typeof window.cspWatch` is `undefined` and `typeof window.__cspWatch` is `function`. The binding
is reachable from the page's own scripts, so a page can add an entry to the log, or fault it, by
calling it. The tools here measure pages their operator points them at, so
that is a page measuring itself, not an attack the collector defends against, beyond bounding
what one call can hand it (see "Instrument faults").

## Tests

- `test_csp_watch.py`: the contract, with stand-ins for the context and the page that record
  what was asked of them, handed over uncast; a Hypothesis property test that any payload is a
  violation or the fault, never another exception; and, in a fresh interpreter, that importing
  the module loads no Playwright. It is also the aggregator of `tests/`, and floors it, and it
  holds the one check of the live suite that needs no engine: that its setup fails where no
  build is installed.
- `tests/test_csp_watch_bounds.py`: the bound on each value, read from the listener's own cut
  for the sample, and a Hypothesis property test that every over-length value is refused.
- `tests/test_csp_listener.mjs`: the listener in a jsdom page, run by the repository's
  `npm test` at 100% lines, branches and functions. jsdom implements no CSP, so each test
  dispatches a plain event carrying the three properties the listener reads.
- `tests/test_csp_watch_live.py`: Chromium, Firefox and WebKit over `tests/fixture/`, served on
  loopback. Per engine: the policy page's one style attribute arrives with scripts on and nothing
  arrives with scripts off; `policy_of` reads the policy and its absence, scripts on and off; a
  plant on the bare page is caught. Firefox runs with its per-user data folders redirected into a
  scratch folder. With an engine build missing where Playwright looks (`PLAYWRIGHT_BROWSERS_PATH`,
  else the folder a plain `playwright install` fills), the module's setup skips, naming where it
  looked and the command that installs the builds. With `PAGE_PROOFS_REQUIRE_BROWSERS=1` it fails
  instead, since a skip would let a gate pass with no engine measured. Installing the builds is in
  `browser_pass/browser_pass.md`, "Installing the engines".
- `tests/test_csp_watch_live_setup.py`: that setup with no engine build installed, with and
  without the opt-in, so the skip and the failure are both checked on any host, and where it
  looks on each platform.

## Importing it

A tool reaches it by putting this folder on `sys.path`, as a folder beside its own, and the
repository's `pyproject.toml` names the folder in `mypy_path`. It imports only the standard
library, and reads the listener's source from `src/` as it loads, so a tool may import it at its
own load without importing Playwright. The context and page it is handed are the caller's, and
so is the Playwright behind them: the `browser` extra there pins it.
