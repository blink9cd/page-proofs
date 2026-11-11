# print_pass

Prints a web project's pages to PDF, in every state a targets file names, through one of the
engines `--engine` names: Playwright's pinned Chromium headless shell, installed Google
Chrome, or installed Firefox through geckodriver's WebDriver Print. Reads any PDF with
poppler, pypdf and pdfminer, and compares two PDFs' renders. Every run writes one
`report.json` and exits `0`, `1` or `2`.

## Why it exists

Printing a site well means guarding against traps that are easy to relearn every time: a
screen emulating print media standing in for a print, an emulated dark scheme that no reader's
print shows, an animation still running when the print is taken, and a date that `pdfinfo`
shows in the local zone ("Traps it guards against" lists them). A targets file holds what one
site needs printed and read back, so each pass repeats the last one rather than resembling it.

Two rules run through all of it. A print is judged only from the PDF it made. And a run refuses
with exit 2 rather than report what it could not measure honestly.

The engines do not print alike. Chromium prints the live page. Firefox prints from a static copy
of the page made just after `beforeprint`. So a defect that only that copy shows needs Firefox
to print it, which is what `--engine firefox` is for (see "The Firefox engine").

## Usage

From the root of this repository. `$P` is the `python` of the virtual environment the
README's "Install" makes, with the repository's `print` extra installed; poppler's readers are
on `PATH`, in every mode; and a make run needs what its `--engine` runs on. "Installing what it
runs on" below gives each install. `--out` must be a new folder outside every git work tree, so
each example puts it in a new temp folder.

Print a served folder in the states a targets file names:

```bash
OUT="$(mktemp -d)/print"
"$P" print_pass/print_pass.py --mode make --serve "<site>" \
  --targets "<targets.toml>" --out "$OUT"
```

Print some pages in the default state, with no targets file:

```bash
"$P" print_pass/print_pass.py --mode make --serve "<site>" \
  --page / --page /about/ --out "$(mktemp -d)/print"
```

Print the same states through installed Firefox, on macOS:

```bash
"$P" print_pass/print_pass.py --mode make --engine firefox --serve "<site>" \
  --targets "<targets.toml>" --out "$(mktemp -d)/print"
```

Read one or more PDFs from any maker, a Safari print included:

```bash
"$P" print_pass/print_pass.py --mode read \
  --pdf "<first>.pdf" --pdf "<second>.pdf" --out "$(mktemp -d)/read"
```

Compare two PDFs' renders, page by page:

```bash
"$P" print_pass/print_pass.py --mode compare \
  --pdf "<first>.pdf" --pdf "<second>.pdf" --out "$(mktemp -d)/compare"
```

Each run prints its summary, findings first, then the path of `<out>/report.json`.
`samples/README.md` runs the first command over the committed sample and says what its report
shows.

**Platforms.** print_pass is tested on macOS only, and this reference makes no claim for Linux
or Windows. The Firefox engine runs on macOS alone, and refuses any other platform.

### Installing what it runs on

- **The `print` extra.** It pins pypdf and pdfminer.six with pdfminer.six's whole closure,
  and Playwright with its own, at the versions the `browser` extra pins. Install the extra,
  never Playwright alone, for the reason
  [browser_pass's "Installing the engines"](../browser_pass/browser_pass.md) gives:

  ```bash
  "$P" -m pip install ".[print]"
  ```

  pypdf and pdfminer.six are imported once any run starts, and Playwright only by a make
  run on the `shell` or `chrome` engine; a Firefox run never imports it. Without one of them
  the run exits 2 and names the extra; `--help` works without any.

- **The engine builds, for `--engine shell`.** It needs only Chromium's headless shell, and
  one Playwright release pins it along with the other two builds, so the install
  browser_pass's section gives covers this tool as well. `--engine chrome` and
  `--engine firefox` start no pinned build. For this tool alone:

  ```bash
  "$P" -m playwright install --only-shell chromium
  ```

  A make run on either Playwright engine looks for the builds in `--browsers`, else
  `$PLAYWRIGHT_BROWSERS_PATH`, else the folder a plain `playwright install` fills, which
  Playwright finds by itself. It prints which. A folder named there is made absolute; `0`,
  Playwright's hermetic mode (builds inside its own package), is handed on as it is. Since
  `--browsers` takes a folder alone, that mode is reached through the variable:
  `PLAYWRIGHT_BROWSERS_PATH=0`.

- **Installed Google Chrome**, for `--engine chrome`, and for any `native-dark` state under
  either Playwright engine.
- **macOS, geckodriver and Firefox**, for `--engine firefox`, which runs on macOS only (see
  "The Firefox engine"). geckodriver is found on `PATH`:

  ```bash
  brew install geckodriver
  brew install --cask firefox
  ```

  Mozilla's own geckodriver release and Firefox installer serve as well. geckodriver finds
  Firefox by itself. A Firefox run prints which geckodriver it found. Its live suite also
  needs `sandbox-exec`, which macOS ships, to fence the run it makes (see "Tests").

- **The shared CSP collector**, [`csp_watch`](../csp_watch/csp_watch.md), in the folder
  beside this tool's own, which `src/` puts on the path as it loads. It is part of the tool as
  `src/` is, and is imported as the CLI loads, in every mode: it imports no Playwright, so
  `--help` and a read or compare run still need none. Without it the CLI does not start,
  `--help` included: Python's import error names it, before any option is read, so nothing is
  written.

- **poppler's command-line readers**: `pdfinfo`, `pdffonts`, `pdfimages` and `pdftoppm`,
  each found on `PATH` by `shutil.which` and run by that name. `REQUIRED_TOOLS` in
  `src/pdf_tools.py` is the authority for the list, and `--help` prints it. Package managers
  ship them as poppler (Homebrew's `poppler`, Debian's and Ubuntu's `poppler-utils`). A run
  names each reader missing and exits 2.

## Options

| Option                            | Modes         | Default    | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                           |
| --------------------------------- | ------------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--mode {make,read,compare}`      | all           | required   | Make PDFs of served pages, read given PDFs, or compare two.                                                                                                                                                                                                                                                                                                                                                                       |
| `--out DIR`                       | all           | required   | A new folder for the PDFs, renders, text and `report.json`. Its parent must exist, and it must lie outside every git work tree (see "Traps it guards against").                                                                                                                                                                                                                                                                   |
| `--dpi N`                         | all           | `96`       | Render resolution for every page render and comparison. `--help` gives the range. The ceiling exists because rasters are parsed in plain Python, and past it one Letter page's raster alone passes 100 MB.                                                                                                                                                                                                                        |
| `--timeout SECONDS`               | all           | `30`       | The bound on each step. In make, each page's load, its actions and its settle, and each browser call. In every mode, each poppler call that reads a PDF or renders one of its pages, and the pypdf and pdfminer reads of one PDF (its links, metadata and text), together, in one child process: a reader that times out is a finding in make and read, and exit 2 in compare, which has nothing to compare without both renders. |
| `--serve DIR`                     | make          | none       | The folder served on 127.0.0.1 and printed from. A make run needs it.                                                                                                                                                                                                                                                                                                                                                             |
| `--targets FILE`                  | make          | none       | The targets file (see "The targets file"). Exclusive with `--page`.                                                                                                                                                                                                                                                                                                                                                               |
| `--page PATH`                     | make          | `/`        | A server path printed in the default state; repeatable. Its targets are named `page-1`, `page-2` and on, in the order given.                                                                                                                                                                                                                                                                                                      |
| `--engine {shell,chrome,firefox}` | make          | `shell`    | Playwright's pinned headless shell; installed Google Chrome through Playwright's `chrome` channel, with a throwaway profile; or installed Firefox through geckodriver's WebDriver Print, on macOS only (see "The Firefox engine").                                                                                                                                                                                                |
| `--browsers DIR`                  | make          | none       | Where the pinned engine builds are; without it, the search "Installing what it runs on" describes. Refused with `--engine firefox`.                                                                                                                                                                                                                                                                                               |
| `--backgrounds {off,on,both}`     | make          | `off`      | Print backgrounds off (the print dialog's default), on, or both from _one_ load, then compare the two.                                                                                                                                                                                                                                                                                                                            |
| `--header-footer {off,on}`        | make          | `off`      | The print dialog's own header and footer (see "Traps it guards against"). Refused with `--engine firefox`, whose WebDriver Print never prints one.                                                                                                                                                                                                                                                                                |
| `--viewport WxH`                  | make          | `1280x800` | The window each state is laid out in.                                                                                                                                                                                                                                                                                                                                                                                             |
| `--pdf FILE`                      | read, compare | none       | A PDF to read (repeatable), or one of exactly two to compare. Read refuses two PDFs whose file stems match, case ignored (a `print.pdf` from one folder beside a `print.pdf` from another, or `a.pdf` beside `A.PDF`): their text and renders would share names under `--out`. Rename one.                                                                                                                                        |

An option of another mode is refused with exit 2, naming the mode that takes it. Under
`--engine firefox`, `--header-footer` and `--browsers` are refused the same way, given at all,
even at a value Firefox would honor.

A key the targets file sets wins over the matching option: `viewport` over `--viewport`, and
`backgrounds` over `--backgrounds`. A file that leaves `viewport` out can therefore be printed
at several widths, one run per `--viewport`. No option sets the print margin: each state is
printed at its engine's own, unless its `page_width` sets a page width of its own (see
"Printing at a phone's width").

## What `--mode make` does

1. **Refuses what it could not measure honestly** (exit 2): an option it cannot use, an
   `--out` it may not write, a poppler reader or PDF reader that is missing, Playwright
   missing under a Playwright engine, a targets file it cannot read or refuses, and, under
   `--engine firefox`, a platform other than macOS or geckodriver missing.
2. **Serves `--serve` on 127.0.0.1**, on a port the operating system picks, through the shared
   `loopback_site`, and _reads each target's page for a meta refresh_ before
   `--out` is made (see "A page that is a meta refresh"). Then it starts the engine's driver:
   Playwright's, or geckodriver.
3. **Prints each state of each target**, in file order. A Playwright engine launches each
   browser the first time a state needs it: the `--engine`, or installed Chrome launched with
   `--force-dark-mode` for a `native-dark` state, whatever that engine is. Firefox gives each
   state a WebDriver session of its own, and its steps, which "The Firefox engine" lists,
   match these. For one state under Playwright:
   1. A fresh context: the viewport, one device pixel per CSS pixel, the color scheme and
      reduced motion as the state sets them (anything not emulated is left to the browser),
      service workers blocked, and `storage` set through Playwright's `storage_state`, so no
      script is injected. Routes keep every request and WebSocket on the served origin (see
      "Privacy").
   2. The shared CSP collector is installed on the context before its page opens, so every
      Content Security Policy violation its documents report is heard from the first load
      on. The page's failed requests, responses of 400 or more, uncaught script errors and
      blocked requests are recorded from here on too.
   3. The page loads, waiting for its `load` event. The state's clock starts as navigation
      begins.
   4. The actions run in order.
   5. **The settle**: the in-page probe is polled every `SETTLE_POLL_MS` until every image
      with a `src` attribute is complete (one without requested nothing, so nothing is on its
      way) and `document.fonts.status` is `loaded`, or until `--timeout` has passed since
      navigation began. A page that never settles is printed anyway, and its read-back says
      what was missing.
   6. With `print_at_ms` set, the state waits until that moment after navigation began; if
      the moment has already passed, it warns with the actual time.
   7. The probe reads the page once more, so the values recorded are the ones the print
      saw. Then one PDF is printed, or two from that same load, backgrounds off and then on.
      Each string the page supplies to the probe (its title, a source, an animation's name
      or target, a read or stored value) is cut to `MAX_PROBE_TEXT` (1,000) characters,
      ending in `…`, and each list it can grow (its images, its running animations, a read's
      values) stops at `MAX_PROBE_ITEMS` (5,000), so no page's megabytes are polled or
      reported; a result past either is refused, as a probe that raises is.
   8. The context closes.
4. **Reads every PDF made**, as read mode does, once every state has printed, and compares
   each state's backgrounds off and on pair.
5. **Records** `<out>/report.json`, prints its summary, and exits with the report's code.

Every Chromium print is US Letter, tagged, at scale 1, with no CSS page-size preference, and
with 1 cm margins on every side unless the state's `page_width` sets its own (see "Printing
at a phone's width"). Letter and those margins are what Chrome's own print dialog defaulted
to on the machine this tool was measured on, while Playwright's own margin is zero. A Firefox
print takes its paper and default margins from Firefox's own dialog instead, as "The Firefox
engine" says. The report's `print` entry says what each engine is passed by default, read from
what its print builder sends; each state's entry names the margin it was printed with.

Under `--out`, the PDFs go to `pdfs/<target>--<state>--bg-<off|on>.pdf`, each PDF's text layer
to `text/<stem>.txt` with a form feed between pages, and its PNG renders to
`renders/<stem>-<page>.png`.

### A page that is a meta refresh

A page whose `<meta http-equiv="refresh">` sends the browser on navigates as it loads, so a
probe evaluated in it races that navigation, and loses: the probe fails with `Execution context
was destroyed` in every state. An archive index that forwards to its newest page is a common
case. So once the tree is served, and before any engine runs, every target's page is read over
loopback exactly as the target names it, a query included, through `loopback_site`'s
meta-refresh reader (`src/print_redirects.py`, each read bounded by `READ_TIMEOUT_SECONDS`). A
refresh is followed only where the targets file declares it, so an accidental one never passes
quietly:

- **Declared** (`follows_redirect = true`) and the page refreshes at once (a delay of 0) to a
  page on the served origin, served as its own file and not itself a refresh: the target's
  states print the destination, in every engine, and the report's `redirects` pairs the page
  with it. The destination is never written in the targets file: the refreshing page stays its
  one source.
- **Declared, and anything else**: the page is no refresh, waits before refreshing, leads off
  the served origin, leads to a page not served, leads to another refresh, or cannot be read.
  The run is refused with exit 2 before `--out` is made, by a message naming the key's path,
  the target and the reason, such as
  `target[3].follows_redirect: target 'archive': /archive/ is not a meta refresh`.
- **Not declared, and a refresh**: the target's states are not printed, and the report holds
  one finding for the target, naming the page and the destination it refreshes to and saying
  to set `follows_redirect = true` on its target to print the page it leads to. The run exits
  1, as for any finding.
- **Not declared, and no refresh, or a page that does not answer**: printed as named; the
  engine's own load reports a page that does not answer.

### When a state cannot go on

- **One finding, and the run goes on.** An action that does not complete (a `click` or
  `wait_for` whose selector matches nothing within `--timeout`, or a `set_attribute` whose
  selector matches nothing when it runs) or a probe that raises stops
  its state with one finding and no PDF. What the steps before it found stands beside it, and
  the run goes on to the next state.
- **Any other browser failure is exit 2.** Under Playwright, a launch (installed Chrome
  absent), opening or closing a context, a page load, a wait, a print or a browser close that
  fails. Under Firefox, a session that cannot start (Firefox absent), a WebDriver call outside
  the actions and the probe, geckodriver's start or stop, a scratch folder that cannot be made
  or removed, and a page whose isolation did not hold (see "The Firefox engine"). Each is an
  engine error naming the state, the browser or the scratch folder it concerns. The run
  prints it on stderr, writes no report, and exits 2. `--out` already exists by then, holding
  any PDF printed before the failure, so the next run needs a new folder.
- **A payload the CSP collector's listener never sends is a finding, never a fault.** The
  collector's binding is a global any script in the page can call, so such a payload is the
  page's own doing: it is screened out before the collector sees it, and never handed back
  to the page. The state goes on, and its `csp_violations` ends with one line counting such
  payloads and giving the first one's reason, cut to `MAX_REFUSAL_CHARS` (200) characters:
  `a page sent the collector what its listener never sends (2), the first: <reason>`.
- **A pair that cannot render is not compared**, nor one holding a page past the render
  budget. Its PDF's own reading already carries the render failure as a finding, so the run
  still exits 1; stderr names the pair.

### What bounds the browser and poppler

Under a Playwright engine, every Playwright call, and the driver's start and stop, runs inside
a watchdog's bound (`src/print_watchdog.py`). A call handed a Playwright timeout needs that
bound as well, because Playwright's driver delivers the timeout on the synchronous API's
dispatcher, and a dispatcher wedged by a handler never delivers it: then only the watchdog, on
a thread of its own, can end the run. Such a call is bounded at `TIMED_CALL_BOUND_FACTOR`
times its timeout (`src/print_engine_state.py`). The factor is above one only so that, on a
healthy browser, the call's own timeout fires first and it fails as a timeout always has. A
wait is bounded at its own length plus `--timeout`. Each close, a context's or a browser's,
takes no timeout and is bounded as a timed call is, at `TIMED_CALL_BOUND_FACTOR` times
`--timeout`: installed Chrome's close has taken just over 30 s under load, and a bound at the
default `--timeout` would abandon a run at its very end. Each other call that takes no timeout
of its own (a print, a probe, the CSP collector's two calls on a context, the driver's start
and stop) is bounded at `--timeout`.

Under Firefox, each WebDriver call is sent with a timeout `TRANSPORT_MARGIN_SECONDS` above
`--timeout`, so that Firefox's own script or page-load timeout error arrives first, and is
bounded at `TIMED_CALL_BOUND_FACTOR` times that. Each state runs inside one bound of its own,
summed from every call and wait it can make (`state_seconds`, in `src/gecko_budget.py`), and
geckodriver's start and stop inside one each (`src/gecko_bound.py`), so the watchdog fires
only when a step's own bound failed.

A call past its bound ends the process at once, with exit 2 and no report. Its last line on
stderr is `print_pass: abandoned: <call> <where> overran its bound`, naming the call and the
state or browser it was made for. The driver's start and stop name the call alone:
`print_pass: abandoned: playwright.start overran its bound`. Under Playwright that is the one
line; ending the process closes the driver's pipe, and the driver then closes its browsers.
Under Firefox, the expiry first kills geckodriver's whole process tree, then names the scratch
folder it leaves in place on a line of its own,
`print_pass: the scratch folder was left in place: <path>` (see "The Firefox engine"), and
only then writes that last line and ends the process. Each poppler call runs through the
shared `bounded_run`, which kills a reader that outlives `--timeout` along with its whole
process tree.

The pypdf and pdfminer reads of one PDF, together, run in one child process
(`src/pdf_read.py`): `src/pdf_read_worker.py` reads its links, its metadata and its text, in
that order, in an isolated interpreter (`python -I`) through the same `bounded_run`, bounded by
`--timeout`, and writes one line for each read as it finishes. A child that outlives the bound
is killed with its process tree, and each read that wrote its line by then keeps it. The
child's environment holds only what an interpreter needs to start: `PATH`, the temporary-folder
and locale variables, and Windows' `SYSTEMROOT`; nothing else of the run's environment reaches
a library parsing the PDF. The worker writes a read whose line would pass `MAX_REPLY_CHARS`
(10,000,000) characters as that read's error instead, so a PDF whose streams inflate to
gigabytes of text never reaches the run. On Linux it first limits its own address space to
`MEMORY_BUDGET_BYTES` (2 GiB), so a read inflating past that fails as a `MemoryError`. macOS
refuses such a limit, so there a watchdog thread in the child reads its peak resident memory
every 50 ms and ends the child once that passes the same 2 GiB: each read already written keeps
its line, and the reads left fail as past the memory budget. That bound is approximate, since a
read can pass it by what it allocates in one 50 ms interval, and memory reserved but never
written is not resident, so it is not counted. Windows has neither, so there `--timeout` alone
bounds a read. A read that crashes either library, or whose line is no reply or holds more
than `MAX_REPLY_CHARS` characters, is a finding naming its step and the PDF by its name alone,
with the exception's class and never its message, which can quote a path; so is each read left
without a line when the child times out, exits non-zero or prints nothing. Either way that
PDF's other steps, and every other PDF, are still read.

## The Firefox engine

`--engine firefox` prints through installed Firefox, driven by geckodriver, Mozilla's WebDriver
server, with WebDriver's Print command. That command takes Firefox's own print path, the one a
reader's print takes: Firefox fires `beforeprint`, prints from the static copy of the page made
just after it, then fires `afterprint`. The `gecko_*` modules under `src/` hold the engine, and
each one's docstring is the authority for what this section summarizes.

### What it needs, and when it refuses

It needs neither Playwright nor any of its builds: a Firefox run never imports Playwright. It
does need what follows, and refuses with exit 2 and no report when any of it is missing:

- **macOS.** Gecko finds the reader's own Firefox folder before it uses the profile it is
  handed, and what keeps it out rests on `CFFIXED_USER_HOME`, a macOS mechanism; the dead
  port's hold on its port (below) was measured on macOS too. So on any other platform the run
  refuses before it looks anything up, rather than risk the reader's own Firefox folders.
- **geckodriver**, found on `PATH`, which "Installing what it runs on" installs. Without it
  the run refuses before it reads the targets file, naming the install, and that Firefox is
  needed too.
- **Firefox itself**, which geckodriver finds. Without it, the first state's session cannot
  start: geckodriver answers WebDriver's `session not created`, and the run refuses with that
  error, adding how to install Firefox. `--out` already exists by then.
- **Neither `--header-footer` nor `--browsers`**: either one, given at all, is refused the
  same way, as "Options" says.

### How a state is printed

The run makes one scratch folder (below), holds one dead port, and starts one geckodriver,
listening on 127.0.0.1 at a port the system picks. Then each state, in file order:

1. A WebDriver session of its own, and so a fresh profile, asks for the isolation prefs, the
   prefs that keep the record the page's own, and the state's color scheme and reduced
   motion (all below). Firefox's script and page-load timeouts are `--timeout`.
2. The window is sized until the page reads the viewport asked, exactly: sized, read back,
   corrected once by the difference, and confirmed, or the run exits 2.
3. With `storage`, the values are set on the served origin before the page loads, on a
   reserved missing path that the loopback server answers with its own script-free error
   page, confirmed as a 404 holding no script, so none of the page's scripts runs first.
4. The page loads, waiting for its load event. The actions, the settle and the print moment
   run as under Playwright, with the same problem texts, and every wait is a poll in this
   process. A `click` that Firefox says is not interactable, or intercepted by an element over
   it, is retried until `--timeout`, as Playwright's actionability wait would. A
   `set_attribute` is one script, run once.
5. The same probe as under Playwright reads the page once more. Then the page's Resource
   Timing and the state's slice of geckodriver's log are read back into the network record
   (below), and a page whose isolation did not hold is refused.
6. One PDF is printed, or two from that one load, backgrounds off and then on.
7. The session's crash helper is recorded while its Firefox still runs, and the session is
   deleted.

Then geckodriver is stopped with every process in its group, along with each crash helper
recorded, and its whole log is checked once more for Firefox's non-local guard (below).

Every PDF is US Letter at scale 1, shrink-to-fit and portrait, with 0.5 in (1.27 cm) margins,
what Firefox's own print dialog puts on Letter, so a print here is laid out as a reader's would
be who left the dialog alone (`src/gecko_print.py`); a state's `page_width` replaces those
margins with its own, sent in the command's centimeters. WebDriver's Print takes no tagging
parameter and no title, so whether a PDF is tagged, and whether its `Title` is the page's
title, is the Firefox build's choice. Measured on macOS: Firefox 159 does both, and writes its
PDFs through Skia's PDF backend; an earlier build did neither, and wrote through macOS's own
Quartz PDF writer. It never prints the dialog's header and footer. The report's `print` entry
says what the command cannot set, in words, in its `cannot_control`, and each PDF's own
`metadata` says what the build did. Every PDF has `Firefox` as its `Creator`.

### Off the network, and out of the reader's own folders

In summary only: each module named below is the authority for its layer.

- **The network.** Classic WebDriver gives no listener to route a request through, so each
  session's prefs keep Firefox off the network (`isolation_prefs`, in `src/gecko_prefs.py`).
  A proxy auto-config script sends every request but the served origin's to a dead proxy: a
  loopback port the run holds, whose listener resets each connection at once. Only the served
  origin's pages and WebSockets go direct, so a page reaches no other port on 127.0.0.1, nor
  `localhost` or `[::1]`: Firefox hands loopback to a proxy only with
  `network.proxy.allow_hijacking_localhost` on, and the prefs turn it on. Firefox runs a script
  it cannot evaluate as no proxy at all, every request direct (measured), so the script is one
  fixed text that a suite holds, the node suite parses and runs offline, and the live suite
  proves. Firefox may not bypass that proxy,
  and every name it looks up itself resolves to loopback. What no HTTP proxy carries,
  connections opened on a guess, Firefox's own reachability checks, and service workers are
  all off.
- **The tripwire.** geckodriver's environment sets Firefox's non-local guard
  (`MOZ_DISABLE_NONLOCAL_CONNECTIONS`), which ends the browser at a direct TCP connection to an
  address it counts as non-local, instead of letting it reach the network. A connection the
  guard refused is the run's error, exit 2, naming the host, and it outranks any other error.
  The log is read for the guard's line only once a state or the run has failed: the guard ends
  the browser, and a page can write the same sentence to the log through its console, so a
  run that went on is never called a breach. A request answered anywhere but the served
  origin, and a document off it or not the served page, are refused with exit 2 too, before
  the print: such a page measures nothing honestly.
- **The folders.** The scratch folder holds every folder Firefox would otherwise find for
  itself but one, its crash helper's (see "What it cannot observe"): a home for macOS's own
  frameworks, Firefox's application-data folders, its temporary folder, and each session's
  profile. Each is set on geckodriver's environment, which the Firefox it starts inherits,
  after every `MOZ_*` and `NSPR_LOG_*` variable and `SSLKEYLOGFILE` of the caller's are
  dropped, so none changes what Firefox does or where it writes. The engine never names,
  reads or touches the reader's own Firefox folder.
- **Bounded reads.** A page decides how long some WebDriver replies are and how much its
  console writes to geckodriver's log, so a reply longer than `MAX_REPLY_BYTES` (256 MB) fails
  its command, exit 2, and each read of the log takes at most the last `MAX_LOG_READ_BYTES`
  (32 MB) of a state's slice, from the first line it holds whole, since the guard's refusal is
  written last: past it, the state warns that its page errors may be incomplete.
- **No authentication on loopback.** geckodriver's port and the Marionette port Firefox opens
  for it accept any local connection, as WebDriver's do everywhere: another account on the
  same Mac could open a session on geckodriver between two states, naming a program for it to
  start as the reader, and the run's next state would then fail, one session being all
  geckodriver holds.

### Keeping the record the page's own

Some behaviors of Firefox's own show in a state's record as if the page had done them, and
every session sets a pref against each (`_PAGE_OWN_PREFS`, in `src/gecko_prefs.py`). The
Chromium engines need only the last, which every launch sets by switch (`_BLINK_SETTINGS`, in
`src/print_engine.py`; see "A lazy image may not have arrived"). Each behavior, and why it
matters to a print pass:

- **No site icon is fetched.** Firefox times its tab icon's fetch, a guessed `/favicon.ico`
  included, in the page's own Resource Timing, so a site with no such file had an HTTP 404
  finding in every state. The price is that a page's own broken `<link rel="icon">` goes
  unreported under Firefox.
- **Links print unvisited.** Firefox looks a link's visited color up again for each print's
  copy, so two prints of one load, backgrounds off and on, differed where no background did.
- **Lazy images within a million pixels load as eager ones.** Firefox's lazy-load margin is
  narrower than Chromium's own, so the probe read an image below the window incomplete and the
  settle waited out `--timeout`, for an image the print then loaded anyway. Widened, the
  margin lets the probe read each such image as the print will hold it, and a broken one is
  still caught.

A state's color scheme and reduced motion are Firefox's own preferences too, set for its
session, so each is reported as not emulated, and a `dark` state carries no emulated-dark
warning. `light` and `dark` set `layout.css.prefers-color-scheme.content-override`, what the
page's `prefers-color-scheme` reads. `native-dark` sets `ui.systemUsesDarkTheme`, darkening the
system theme Firefox reads: its counterpart to Chrome's `--force-dark-mode`, and a Firefox run
never launches Chrome. `reduce` sets `ui.prefersReducedMotion`. `native` sets nothing, leaving
Firefox's own setting.

### The network record, read back

With no listener on a page's requests, a Firefox state's network record is read back once the
page has settled, from the page's own Resource Timing and the state's slice of geckodriver's
log (`src/gecko_network.py`). Each entry means what a Chromium state's means:

- a request off the served origin that got no status is `blocked`, another port on loopback
  included: a finding, or a warning on an expected host;
- a request on the served origin that got no status `failed`, as `<path> (no response)`,
  since Resource Timing gives no reason; one whose status is 400 or more is an HTTP error;
- a page error is a `JavaScript error:` line of the log from one of the page's own scripts,
  recorded as its message. That keeps the error's class name, where Chromium's text leaves it
  off: `ReferenceError: boom is not defined`;
- `csp_violations` is always empty, as "What it cannot observe" says;
- as many Resource Timing rows as the browser's default buffer holds, or more, are a warning:
  the browser may have stopped listing requests.

### What it cannot observe

The report's `cannot_observe` states, one sentence each, what a Firefox run cannot observe,
most of it what a state's network record cannot show, so that a quiet record is never read as
a quiet page; `CANNOT_OBSERVE`, in `src/gecko_network.py`, is its source. In short:

- **Resource Timing is the page's own.** A page that clears it, shrinks its buffer or replaces
  `window.performance` decides what is listed, and what it never times, a WebSocket or a
  connection Firefox makes outside any page, is refused but never listed.
- **Requests made while printing** are not in the record: it is read before the print.
- **An opaque response reads no status**, answered or not. So a no-cors request to another
  origin that a server answered on loopback reads as failed with no response, and one off
  loopback cannot be told from one refused.
- **The crash helper's temporary folder** is outside the scratch folder: Firefox hands its
  crash helper a folder in the reader's own per-user temporary folder, which `TMPDIR` does not
  redirect, and a crash may write there.
- **The non-local guard's scope** is direct TCP connections alone, and which addresses it
  counts as local was not measured.
- **No Content Security Policy violation is heard.** The shared collector is an init script
  reporting through a Playwright binding, and WebDriver's classic commands offer neither, so a
  Firefox state's `csp_violations` is always empty (see "The report").

### The scratch folder

Each run makes one fresh scratch folder in the system's temporary folder. It holds Firefox's
redirected folders, each session's profile, and geckodriver's log.

- **Removed** once geckodriver's stop has returned. That stop has seen geckodriver's whole
  process group end, and each crash helper it recorded, so nothing it recorded writes into the
  folder any more. macOS gives some folders it makes in a home it is pointed at an ACL that
  denies their deletion, so every ACL in the tree is stripped first, each entry named, a
  symlink never followed. A removal that fails is the run's error (exit 2), or, beside another
  error, a line on stderr naming the folder.
- **Left in place, and named** on stderr as `the scratch folder was left in place: <path>`,
  whenever the run ends without a stop that returned: geckodriver's start or stop failed, or
  the watchdog's bound expired. No stop then vouches that nothing still writes into it.

To remove a left one by hand, first make sure nothing of that run still runs: no process a
listing shows (`ps -A -ww -o pid=,args=`) should name the folder in its arguments, as
geckodriver names it in its `--profile-root` and each Firefox it starts in its `-profile`. A
crash helper the run left need not be found first: the folder it is handed, and where a crash
may write, is the reader's own per-user temporary folder, outside the scratch folder (see
"What it cannot observe"). Then strip the scratch folder's ACLs entry by entry, never
following a symlink, and remove it, with `<scratch>` the absolute path stderr named:

```bash
find "<scratch>" -exec /bin/chmod -h -N {} +
rm -rf "<scratch>"
```

Never use `chmod -R -N` here: it also strips the ACL of whatever a symlink in the tree points
at, outside the tree, and a print leaves such a symlink in the scratch folder.

## The targets file

TOML, and the only knowledge of one site the engine is ever given. A targets file belongs to
the project it prints. Every key is checked: an unknown key or a bad value is refused with
exit 2, by a message that opens with the path to the key (`target[1].state[2].color_scheme`,
arrays counted from zero in file order), so a misspelled key fails loudly instead of passing as
its default.

```toml
viewport = { width = 1280, height = 800 }  # optional; else --viewport
backgrounds = "both"                         # optional; else --backgrounds
expected_offsite_hosts = ["example.org"]     # optional; still blocked, but a warning

[[read]]                                     # applied to every state of every target
name = "mode attribute"
selector = ":root"
attribute = "data-mode"                      # exactly one of: attribute, property, text

[[target]]
name = "home"
page = "/"
storage_reads = ["mode"]                     # stored values read back in-page

[[target.state]]
name = "default"                             # every key below is optional

[[target.state]]
name = "after-start"
actions = [{ click = "#menu-toggle" }]       # click | wait_for (a selector) | wait_ms

[[target.state]]
name = "stored-dark"
storage = { mode = "dark" }                  # set through storage_state: no injected JS

[[target.state]]
name = "os-dark-emulated"
color_scheme = "dark"                        # native (default) | light | dark | native-dark

[[target.state]]
name = "caught-at-3.4s"
print_at_ms = 3400                           # print this long after navigation began
```

The constants in `src/print_targets.py` are the authority for every key and the values each
takes but an action's, those in `src/print_actions.py` (`_ACTION_KINDS`, `_SET_ATTRIBUTE_KEYS`
and `_ATTRIBUTE_NAME`) for the action kinds and a `set_attribute` table's keys and names, and
those in `src/print_offsite.py` for what an expected host may be; the tables below mirror
them.

| Key                      | Takes                                                                                                                                                                                                                                                                                 | Meaning                                                                                                                                                                                                                                                                                            |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `viewport`               | `{ width, height }`, whole pixels, each at least 1                                                                                                                                                                                                                                    | The window every state is laid out in, in place of `--viewport`.                                                                                                                                                                                                                                   |
| `backgrounds`            | `off`, `on` or `both`                                                                                                                                                                                                                                                                 | In place of `--backgrounds`.                                                                                                                                                                                                                                                                       |
| `expected_offsite_hosts` | an array of hostnames, each listed once: lowercase ASCII letters, digits and inner hyphens, in two labels or more, an internationalized name in its `xn--` form (`xn--bcher-kva.example`); no scheme, port, path, wildcard, IP address or loopback name (`localhost` or one under it) | The hosts off loopback the site's pages are known to load from; leave it out, or leave it empty, for none. A request to one, or to a name under one (`example.org` covers `img.example.org`, never `badexample.org`), is still aborted, and is a warning rather than a finding (see "The report"). |
| `[[read]]`               | the read tables below                                                                                                                                                                                                                                                                 | Read back in every state of every target, before a target's own.                                                                                                                                                                                                                                   |
| `[[target]]`             | the target tables below; at least one                                                                                                                                                                                                                                                 | The pages to print.                                                                                                                                                                                                                                                                                |

Each `[[read]]`, at the top level or a target's own `[[target.read]]`:

| Key                               | Takes                                                                 | Meaning                                                                                                                                |
| --------------------------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `name`                            | a non-empty string; required                                          | The label the values are reported under, unique across the file's reads and each target's own, since the report keys the values by it. |
| `selector`                        | a CSS selector; required                                              | The elements read.                                                                                                                     |
| `attribute`, `property` or `text` | exactly one: an attribute's name, a property's name, or `text = true` | What is read of each element: its text has every run of whitespace collapsed to one space, and is trimmed at both ends.                |
| `all`                             | `true`, or `false` (the default)                                      | Read every match rather than the first.                                                                                                |

Each `[[target]]`:

| Key                | Takes                                             | Meaning                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------ | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`             | a name (below); required and unique               | Names the target's PDFs.                                                                                                                                                                                                                                                                                                                                                              |
| `page`             | a server path opening with a single `/`; required | The page printed, appended to the served origin. One opening `//` or `/\` is refused, since a URL resolver would read it as another host. So is a `.` or `..` segment before any `?` or `#`, percent-encoded or not, a backslash separating segments too: no page needs one, a `..` can name a file outside the served folder, and a browser resolves an encoded one to another page. |
| `storage_reads`    | an array of `localStorage` keys                   | Read back in-page in every state; one with nothing stored reads `null`.                                                                                                                                                                                                                                                                                                               |
| `[[target.read]]`  | read tables, as above                             | This target's own reads, after the file's.                                                                                                                                                                                                                                                                                                                                            |
| `[[target.state]]` | the state tables below; at least one when present | Without any, the target prints once, in a state named `default`.                                                                                                                                                                                                                                                                                                                      |
| `follows_redirect` | `true`, or `false` (the default)                  | The page is a meta refresh to follow: its states print the page it leads to. See "A page that is a meta refresh".                                                                                                                                                                                                                                                                     |

Each `[[target.state]]`:

| Key              | Takes                                                                                                                                                                            | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`           | a name (below); required, and unique in its target                                                                                                                               | Names the state's PDFs.                                                                                                                                                                                                                                                                                                                                                                                                       |
| `color_scheme`   | `native` (the default), `light`, `dark` or `native-dark`                                                                                                                         | `native` emulates nothing and leaves the engine's own setting. Under Playwright, `light` and `dark` are Playwright's emulation, reported as emulated, and a `dark` state always carries a warning (see "Traps it guards against"), and `native-dark` prints in installed Chrome launched with a real dark setting. Under Firefox, each is a preference of Firefox's own, reported as not emulated (see "The Firefox engine"). |
| `reduced_motion` | `native` (the default) or `reduce`                                                                                                                                               | `reduce` is Playwright's emulation, reported as emulated; under Firefox, a preference of Firefox's own, reported as not emulated.                                                                                                                                                                                                                                                                                             |
| `storage`        | a table of strings to strings                                                                                                                                                    | Stored in `localStorage` for the served origin before the page loads.                                                                                                                                                                                                                                                                                                                                                         |
| `actions`        | an array of tables, each holding exactly one of `click` (a selector), `wait_for` (a selector, until visible), `wait_ms` (whole milliseconds) or `set_attribute` (a table, below) | Run in order after the load.                                                                                                                                                                                                                                                                                                                                                                                                  |
| `print_at_ms`    | whole milliseconds                                                                                                                                                               | Print this long after navigation began.                                                                                                                                                                                                                                                                                                                                                                                       |
| `page_width`     | whole CSS px, from `MIN_PAGE_WIDTH` (320) to a pixel under `LETTER_WIDTH_PX` (816)                                                                                               | Lay the page out this wide: the engine prints it with a margin of `(816 - page_width) / 2` CSS px on every side of Letter, in place of its own. Left out, the engine's own margin. See "Printing at a phone's width".                                                                                                                                                                                                         |

A `wait_ms` or a `print_at_ms` is at most `MAX_MS` milliseconds, a day: a ceiling far inside
what the watchdog can wait. A wait's watchdog bound is its own length plus `--timeout`, so a
wait long enough to pass the watchdog's limit would otherwise be refused only after the run
had started; anything over a day is refused with its key when the targets file is read.

A `set_attribute` is a table of a `selector`, an attribute's `name` and an optional
`value`, empty by default as a bare attribute such as `hidden` is:
`{ set_attribute = { selector = ":root", name = "data-print-theme" } }`. It sets that attribute
on the first element the selector matches, once, without waiting and without printing. It
exists for a page whose own button sets an attribute and then calls `window.print()`: under
Firefox, WebDriver's click on such a button does not return while the real print dialog is
open, so the state takes the button's first step itself. A name outside XML's Name
production, restricted to ASCII, is refused when the file is read. HTML lowercases attribute
names in `setAttribute`, so `data-printTheme` is set as `data-printtheme`, while the report
shows the name as written. A selector that matches nothing when the action runs is the
finding a failed click is, so put a `wait_for` before it for an element that arrives late.

A selector, in any action, and a `set_attribute`'s value are refused when the file is read if
they hold a control character, a newline or a tab included: each is written into the report
on one line.

A targets file is trusted as the page is. An attribute such as an `on*` handler or `srcdoc`
can run script in the page, within the page's own loopback sandbox and its off-origin
fences.

A target or state name is lowercase letters and digits, runs of them joined by one dot or one
hyphen each: no doubled or trailing separator. A PDF's name joins the two with `--`, so a name
holding `--` could let two pairs write one file.

A first-match read whose selector matches nothing is `null` in the report, and an `all` read
that matches nothing an empty list; neither is a finding. An action's selector that matches
nothing _is_ one, and stops its state.

### Printing at a phone's width

`page_width` exists to print a page the way a phone does. A phone lays a printed page out
narrower than a desktop browser: on US Letter at their own default margins, Chromium lays a
page out about 740 CSS px wide and Firefox about 720, where iOS Safari lays it out about 675.
A site whose layout changes between those widths, one splitting at 44rem (704 px), say,
prints its one-column phone layout on the phone and never in a standard run. A state with
`page_width = 672` printed that one-column layout in both engines, as measured.

It is a margin, not a scale. The page stays Letter and the margin on every side becomes
`(816 - page_width) / 2` CSS px, derived by `state_margin_inches`
(`src/print_targets.py`) and sent to both engines: as a CSS length in Chromium's `page.pdf`
options, and in centimeters in Firefox's WebDriver Print body. A print `scale` was measured and
rejected: Firefox's does not reflow the page, so a two-column layout stays two columns, only
smaller. Measured on both engines, a 0.75 in margin (`page_width = 672`) prints the
one-column layout in each. The same margin is the one each PDF's blank share is measured
against, and the one the state's report entry names.

`MIN_PAGE_WIDTH` is 320 px, the narrowest phone a page is laid out for (the smallest iPhones'
CSS width): a width exists to approximate a phone, and none is narrower. The widest taken is
815 px, a pixel under Letter's 816, since 816 would leave no margin at all.

It _approximates_ a phone's print, and never replaces printing on the device itself. A phone
prints more than its width: layouts a page shows only on a phone, such as a touch carousel or a
control behind a hover-less media query, come from the device, not the page width, and no
margin reproduces them.

## The report

`<out>/report.json`: sorted keys, two-space indent, UTF-8, written whole through a temp file
moved into place, and readable only by the account that ran it (mode 0600). A lone surrogate,
which a page's string may hold and UTF-8 cannot encode, is written as its JSON escape, so the
report still reads back as the strings the page handed over; in a text layer's file it is
written as the same escape, which there cannot be told from the same six characters typed in
the PDF's text. The printed summary lists every finding, then every warning, then a line per
PDF and per comparison, then the totals. A summary line can quote what a page or a PDF chose
(an animation's id, a page error, a font's name), so each character a terminal would act on, a
control character, a direction mark or a line separator, is printed as its escape (`\x1b`,
`\u2028`). A _finding_ is something a reader's print would get wrong, and fails the run with
exit 1; a _warning_ is reported and never fails it. `src/print_report.py` shapes the report and
is the authority for every key but `redirects`, which `src/print_report_redirects.py` adds.
`schema` rises when a key changes meaning or leaves.

A request blocked on a host the targets file expects is the warning
`off-loopback request blocked as expected: <url>`, where any other blocked request is the
finding `blocked off-loopback request: <url>`, a request to another port on 127.0.0.1 among
them, named with its port; under Playwright, a WebSocket off the served origin is judged the
same way, while Firefox refuses one without listing it. An image that loaded broken
(`image broken: <src>`) or had not arrived (`image not complete at print: <src>`) keeps its
text, and is a warning rather than a finding when its source is an http or https URL on an
expected host. A source the browser could not parse (a port past 65535, say) was never
requested, and a `file:` or `javascript:` source asks no host for anything, so nothing was
blocked, and each stays a finding. Those warnings alone never fail a run, and the report's
`targets` entry says which hosts were expected, so a reader sees why each is only a warning.

Only an image with a `src` attribute is judged. An `<img>` with no `src` attribute at all
requested nothing, so it is _never requested_: neither broken nor incomplete, it raises no
finding and holds no settle, and the read-back lists it with `has_src: false`. `src=""` is a
`src` attribute, and is judged like any other. The case is a carousel that leaves its hidden
slides without a source until they are needed: a carousel prints only its showing slide, and a
static `<img>` missing its `src` is an HTML validator's to catch (html-validate flags it).
Every image that has a `src` is judged as above, in every engine.

Each Content Security Policy violation a state's documents report is the finding `csp
violation: <directive> blocked <what> on <document>`, with `: <sample>` after it when the
policy asks for `'report-sample'`: `csp violation: style-src-attr blocked inline on /`. The
line is the shared collector's own (`describe()` in `csp_watch`). It reports every refusal,
those that load nothing included, which would otherwise reach only the browser console, and
this tool reads no console: a style attribute, an inline `<style>` or script, an `eval`. A
refused load is reported twice. Measured on the pinned shell (Chromium 153), a load the policy
refuses fails as a request whose failure is `csp`, before any route sees it, so it is the
finding `request failed: <path or url> (csp)` and a `csp violation` as well, two findings for
one refusal. A violation naming a host the targets file expects is still a finding: the page's
own policy refused what the site is known to load. Under a Playwright engine, every state is
measured, never skipped: the collector is an init script, which runs only with a page's scripts
on, and a make run never turns them off. Under Firefox, none is heard: the collector is an init
script reporting through a Playwright binding, and WebDriver's classic commands offer neither,
so a Firefox state's `csp_violations` is always empty, and the report's `cannot_observe` says
so.

| Key                      | Modes              | Holds                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------ | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `tool`, `schema`, `mode` | all                | `print_pass`, the report's version, and the mode run.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `print`                  | make               | How every page was printed by default: a state's `page_width` changes only its margin, which its own entry names. Under Playwright, `format`, `margin`, `tagged`, `header_footer`, `prefer_css_page_size` and `scale`. Under Firefox, the WebDriver Print body's values under the report's own names (`_GECKO_PRINT_KEYS`, in `src/print_report.py`), less `background`, which each PDF sets for itself; `tagged` and `header_footer`, each false, since each says what was requested and the command takes no tagging parameter and prints no header or footer (whether the build tagged a PDF anyway is that PDF's `metadata.tagged`); and `cannot_control`, what that command cannot set, in words. |
| `cannot_observe`         | make, Firefox only | What a Firefox run cannot observe, most of it what a state's network record cannot list, one sentence each (see "The Firefox engine").                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `targets`                | make               | What the targets file set that weighs the problems: `expected_offsite_hosts`, its list in file order, scrubbed as every text is, and empty for a file that names none or a run given pages. Recorded once for the run, beside no state.                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `redirects`              | make               | One entry per target whose page is a meta refresh, in file order, and empty for none: `target`, `page` as the target names it, `destination` (a server path and query on the served origin, else the whole URL, scrubbed), `followed`, and `problems`: none when followed, and the one finding when not (see "A page that is a meta refresh").                                                                                                                                                                                                                                                                                                                                                         |
| `states`                 | make               | One entry per state, below.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `pdfs`                   | make, read         | One entry per PDF, below.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `comparisons`            | make, compare      | One entry per pair compared, below.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `totals`                 | make, read         | `states` (make), `pdfs`, `findings` and `warnings`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

Each state:

| Key                                 | Holds                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `target`, `state`, `page`           | What was printed.                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `engine`, `browser_version`         | The browser that printed it. Under a Playwright engine, a `native-dark` state is always `chrome`; a Firefox run never launches Chrome.                                                                                                                                                                                                                                                                                                                                  |
| `viewport`                          | `width` and `height`, after the targets file's override.                                                                                                                                                                                                                                                                                                                                                                                                                |
| `color_scheme`, `reduced_motion`    | Each as `requested`, and whether it was `emulated`: the emulation is stated every time.                                                                                                                                                                                                                                                                                                                                                                                 |
| `storage`, `actions`, `print_at_ms` | What the state set, each action written as `click #start`, `wait_for #ready`, `wait_ms 300` or `set_attribute :root data-print-theme=""`.                                                                                                                                                                                                                                                                                                                               |
| `page_width`, `margin_px`           | The `page_width` the state asked for, `null` for none; and the margin on every side it was printed with, in CSS px to 4 places: half of what its page width leaves of Letter, else its engine's own (37.7953 for 1 cm, 48.0 for 0.5 in). A state stopped before it printed names the margin it would have printed with.                                                                                                                                                 |
| `read_back`                         | What the page held when it printed: its `color_scheme`, `reduced_motion`, `ready_state`, `fonts_status` and `title`; every image's `src`, `has_src` (false for a never-requested image), `complete`, `loading` and `natural_width`; each animation still running, by `name` and `target`; each read's values, by name; and each storage read. `null` for a state stopped before its read-back.                                                                          |
| `network`                           | `failed` requests, `http_errors`, `blocked` requests, `page_errors` and `csp_violations`, each list present even when empty. A request on the served origin is named by its path and query, never its port, and any other whole, another loopback port's included; a violation is the collector's line, with a refused URL named the same way. Under Firefox it is read back rather than listened for, and `csp_violations` is always empty (see "The Firefox engine"). |
| `timings`                           | `load_ms`, and `printed_at_ms` after navigation began.                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `pdfs`, `problems`                  | The state's PDFs, and its findings and warnings.                                                                                                                                                                                                                                                                                                                                                                                                                        |

A `set_attribute`'s value is written as a JSON string, so a quote inside it cannot be
read as its end; a non-ASCII character in it is written as itself, not escaped. Each action,
and each stored key and value, passes through the scrub a finding does, since a targets file
may name a home path in one.

Each PDF:

| Key                               | Read by             | Holds                                                                                                                                                                                                                                                                                                                                |
| --------------------------------- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `file`                            |                     | Its path under `--out`, or its name alone for a PDF outside it.                                                                                                                                                                                                                                                                      |
| `pages`, `page_sizes`, `metadata` | `pdfinfo -rawdates` | The page count; each page's size in points, named when it is Letter or A4; the `title`, `creator`, `producer`, `tagged`, `pdf_version` and `encrypted` fields, whether an `author` is present, and each date's class (see "Privacy").                                                                                                |
| `fonts`                           | `pdffonts`          | Each font's `base_name` without its subset tag, whether it is a `subset`, its `type` and `encoding`, whether it is `embedded`, and whether it has a ToUnicode map (`unicode`). A font not embedded, or without ToUnicode, is a warning.                                                                                              |
| `images`                          | `pdfimages -list`   | Each image's page, pixel size, color, bits per component, encoding, stored size and effective ppi on each axis, and the lowest and highest ppi of any.                                                                                                                                                                               |
| `links`                           | pypdf               | Each link annotation's page, target `uri` or internal `destination`, and rectangle. A same-origin link's URI names the loopback port the page was served on. A `/GoToR` or `/Launch` action's file is its `uri`, as `file:<spec>`; a destination that is neither an array nor a name is given by its class, as `<DictionaryObject>`. |
| `text`                            | pdfminer            | The text layer's file under `--out`, a form's text included; its characters per page; its count of glyphs pdfminer could not map (`(cid:N)`, a warning); which fonts draw each non-ASCII glyph; and pdfminer's `log` (below).                                                                                                        |
| `renders`                         | `pdftoppm`          | The resolution, the PNG files, each page's gray levels and colors that each cover at least `DEFAULT_MIN_SHARE` of it (`src/pdf_raster.py`), and the blank share of the content area below each page's last ink.                                                                                                                      |
| `privacy`, `problems`             |                     | The privacy flags (see "Privacy"), and the PDF's findings and warnings. A reader that fails is a finding naming its step, and the other readers still run.                                                                                                                                                                           |

The blank share's content area is the page less its print margin at top and bottom, and the
margin is the one that PDF was printed with. A make run takes it from the value its engine's
print builder was sent, which the PDF's record carries: for a state with a `page_width`, the
margin derived from it; otherwise the engine's own, 1 cm under the shell and installed Chrome
(`MARGIN_INCHES` in `src/print_playwright.py`), 0.5 in under Firefox (`MARGIN_INCHES` in
`src/gecko_print.py`). A
PDF given to `--mode read`, Safari's or any other made outside the tool, states no margin, so
it is measured as though printed at 1 cm, Chrome's dialog default: an assumption named at the
one call that makes it (`OUTSIDE_PDF_MARGIN_INCHES` in `src/print_modes.py`). `--mode compare`
measures no blank share, so it assumes no margin.
Before a PDF is rendered, each page's size as pdfinfo read it is judged at `--dpi` against
`MAX_RASTER_PIXELS` (100,000,000 pixels, `src/pdf_raster.py`), counting an edge's fraction of
a pixel as a whole one, as `pdftoppm` does. A page past it makes the PNG, gray and color
renders each a finding, `gray render failed: page 3 is 19200x19200 px at 96 dpi, past the
render budget of 100,000,000 pixels`, and `pdftoppm` is never run on that PDF; a PDF pdfinfo
could not read has no size to judge, so its renders are findings too. In compare, either PDF
holding such a page ends the run with exit 2, naming it. The gray and color renders run
`pdftoppm` once per page, each run bounded by `--timeout`, and keep only what is measured of a
page, its levels or colors and its ink, before the next is drawn; compare renders a page of
each PDF at a time. So one page's raster is the most held at once: a Letter page in color at
600 dpi is about 100 MB.

The `DEFAULT_MIN_SHARE` floor under the gray levels and colors is kept on purpose. They
measure fills, a page's paper or a dark background nobody asked for, so anti-aliased text
falling below the floor is intended. An encrypted PDF skips the pypdf and pdfminer readers,
with a warning.

A PDF's `text.log` counts what pdfminer logged while reading it, each record a warning or
worse, by its message's template:
`{"Could not get FontBBox from font descriptor because %r cannot be parsed as 4 floats": 42}`.
A template never holds the values its message was given. The one message pdfminer formats
itself quotes the open file, whose path reads as the PDF's name, and every template is
scrubbed as every text is. None of it reaches stderr, and none is a warning: none changes a
measurement the report states. The normalized copy drops it, since it follows the fonts
embedded.

Each comparison names its PDFs `a` and `b`, says whether they are `identical`, gives each one's
`page_count`, and for each page both have, whether it is `identical`, how many pixels differ,
and their bounding box in points from the page's top-left corner. Pages of two sizes differ
uncounted, and a page-count mismatch alone makes the pair differ.

Measured on the pinned shell's prints: Chromium writes no XMP packet; its Info dictionary holds
the page's `<title>` as `Title`, `Chromium` as `Creator`, a `Producer` naming the engine's
milestone, and both dates in UTC; and each PDF embeds its font subset with a ToUnicode map.
Firefox's prints come from another writer, and "The Firefox engine" says what it writes.

## Traps it guards against

Each is a guard, measured where it says so.

- **Emulated dark persists into the print.** Playwright's emulated color scheme reaches
  `page.pdf()`, while a real dark operating system prints light (measured on Chrome 153), so
  wherever an emulated-dark PDF differs from a light print, no reader's print looks like it;
  a page whose print styles force light prints the two alike. Under a Playwright engine, the
  report marks `light` and `dark` as emulated, and every `dark` state carries a warning that
  says so. Firefox sets neither by emulation (see "The Firefox engine").
- **`emulate_media(media="print")` is not the print path.** A screen emulating print media
  shows what the stylesheets say, not what the print engine lays out. The tool never calls it,
  and judges a print only from its PDF; the engine suite's fake page fails any test that calls
  it.
- **The pinned shell ignores `--force-dark-mode`** (measured), so a real dark setting is
  reachable only in installed Chrome. Under either Playwright engine, a `native-dark` state
  always prints there, and without installed Chrome its launch fails, with exit 2. Under
  Firefox it prints in Firefox, with Firefox's own dark setting. Under any engine, it is a
  finding when the page does not read dark.
- **A lazy image may not have arrived** when the print is taken, which leaves a blank on paper.
  The settle polls until every image with a `src` attribute is complete and the fonts are
  loaded. Such an image still incomplete at the print is a finding, and so is a complete one
  with no natural width, a broken one; either is a warning instead when its source is on a host
  the targets file expects. An image with no `src` attribute is never requested, and never
  judged (see "The report"). Every engine starts a lazy image only once it is near the window,
  so every engine widens that margin to a million pixels, and the probe reads each image as the
  print will hold it while a broken one is still caught. Firefox's sessions set prefs (see "The
  Firefox engine"). Every Chromium launch passes one switch, `--blink-settings`, setting
  `lazyLoadingImageMarginPx<Type>` for each connection type after Playwright's own headless
  settings, since Chromium honors only the last `--blink-settings` it is given (see "Only
  loopback" under "Privacy"). Without it, a carousel's lazy slides clipped far to the side in a
  horizontal scroller were never fetched: a race when the page's own script turns a column of
  images into the carousel after parsing (measured on installed Chrome: 7 of 8 runs left the
  last slides incomplete), and every run on both engines with slides 4,000 px apart. Each was an
  `image not complete at print` finding, and each settle waited out `--timeout`, although the
  slides are clipped off paper. With it, 8 of 8 runs read every slide complete, and a missing
  one read broken.
- **A print can catch a running animation** wherever it stands. The probe lists the
  animations still running on screen just before the print, the document's and every _open_
  shadow root's, and the report warns of them. The paper may still show each one finished,
  where a print stylesheet lays it out so: compare the state's PDF with a reduced-motion
  state's to see which. `print_at_ms` prints at a chosen moment instead. An animation driven
  by `requestAnimationFrame` alone is invisible to `getAnimations()`, so it is never reported.
- **Motion inside a shadow tree.** The measurement and the decision are `browser_pass`'s,
  made in its three pinned engines, Chromium, Firefox and WebKit (the "Stilling motion"
  section of [`browser_pass.md`](../browser_pass/browser_pass.md)): `document.getAnimations()`
  does not list an animation whose target lives _inside_ a shadow root (one on a shadow host, or
  on an element slotted into a root, is listed): only that root's own `getAnimations()` lists
  it, a root nested in another is listed by the inner root alone, and an element's
  `getAnimations({ subtree: true })`, a host's or `<html>`'s, crosses into none. So the probe
  reads the document's list and every _open_ root's, found recursively through each element's
  `shadowRoot`, as `browser_pass`'s settles do. Each animation is in exactly one list, so none
  is reported twice. Every engine here runs the same probe file, so each applies the same
  walk; the live suite checks it on the pinned shell, and installed Chrome and installed
  Firefox, whatever versions a host has, were not measured.
- **A known limit: motion inside a _closed_ shadow root.** A closed root, and any root inside
  one, is out of the page's reach (measured by `browser_pass`: its host's `shadowRoot` reads
  null), so its motion is never warned of: a page printed mid-motion there prints
  without the warning. Chromium's DevTools could reach it, and `browser_pass` declined that
  for its settles, with the reasons in its "Stilling motion" section; the probe follows the
  same rule.
- **Plain `pdfinfo` shows each date converted to the local time zone**, a display artifact,
  not what the file stores (measured). The report reads `pdfinfo -rawdates`, and keeps only
  each date's class.
- **A PDF records when it was made**, so `--out` is refused inside any git work tree: the
  tool keeps its PDFs out of every work tree by construction, whatever that work tree's
  ignore rules say. git is asked, and only its two answers for outside allow the run:
  `false`, inside a `.git` folder, which is no work tree; and its own words for a search
  that found no repository in any parent. Any other answer (git missing or hung, a dangling
  `.git` file, a search stopped at a filesystem boundary) refuses the run as unverifiable.
- **Chrome's own `--headless --print-to-pdf` was rejected as the engine.** Measured, it prints
  Letter, tagged, with 1 cm margins and a header and footer. But it cannot set a state, read
  one back, wait for a lazy image, or print backgrounds off and on from one load, and installed
  Chrome hung after writing its PDF until it was killed. Playwright's `page.pdf()` prints
  instead, with the dialog's paper and margins passed explicitly.
- **Playwright's `displayHeaderFooter` is `--header-footer`, off by default.** Measured on
  Chromium 153, with no templates it prints the dialog's own header and footer (date, title,
  URL, page count) on the pinned shell and installed Chrome alike, and every word sits in the
  same place with it on or off, so page breaks cannot differ. It is off by default because the
  printed date changes between runs, and would break a pixel comparison. WebDriver's Print,
  Firefox's, never prints one, so `--engine firefox` refuses the option.

## Privacy

- **Only loopback.** Only a folder the tool serves itself on 127.0.0.1 is ever printed. Under
  Firefox, the layers that keep a page on the machine are Firefox's own, and "The Firefox
  engine" summarizes them. Under a Playwright engine, three layers keep on the machine what a
  page sends through the channels they cover: its requests, its WebSockets, and its hostname
  and IP-literal lookups.
  - `context.route` aborts every request whose URL is not on the served origin, plain HTTP on
    127.0.0.1 at the port served, recorded as a `blocked off-loopback request` finding, or as
    a warning when its host is one the targets file's `expected_offsite_hosts` names. A
    request to an expected host is still aborted and recorded exactly as any other; only its
    severity differs, and nothing leaves the machine. Another port on 127.0.0.1 is another
    server, a dev server or a local API the user runs, so it is blocked and recorded with its
    port. `localhost` is a name the host resolves and `::1` another address, so both are
    blocked; a `data:`, `blob:` or `about:` URL is no network request and passes. Service
    workers are blocked in the context, since the requests one makes meet no context route.
  - `context.route_web_socket` records every WebSocket off the served origin and never
    connects it. A socket the handler does not connect reaches no server: the page talks to a
    stand-in. The handler never closes it either, because under the synchronous API a close
    from the handler waits on the dispatcher it runs on, and never returns.
  - Every launch resolves no hostname at all, which covers what no route sees: a
    `<link rel="preconnect">` or a DNS prefetch. `--host-resolver-rules` maps every name to
    NOTFOUND, `localhost` included, and excludes 127.0.0.1 alone: Chromium applies the rules
    to IP literals too, so without the exclusion the site itself would fail. Beside it, each
    launch carries one `--blink-settings` switch and, for a `native-dark` state,
    `--force-dark-mode`. That switch widens the lazy-image margin (see "A lazy image may not
    have arrived"), and repeats first the hover and pointer settings Playwright's own headless
    launch passes in a `--blink-settings` of its own: Chromium honors only the last such
    switch, so without them the margin would silently drop Playwright's emulation. A suite
    pins the copy against the installed Playwright's driver bundle, so an upgrade that
    changes Playwright's default fails a test.

  The routes admit the served origin alone, so a page reaches no other service listening on
  127.0.0.1; the resolver's exclusion covers the whole address, which only a name lookup
  meets, and every request meets the routes first. WebRTC is not covered: whether a page's
  peer connection to a server named by an IP address leaves the machine is unmeasured. The
  containment assumes the page's own scripts, the served folder's, are trusted; a third-party
  script fetched from off loopback is itself a request, and is blocked before it runs.

- **No browser profile is read.** Under Playwright, every state has a fresh context, and
  installed Chrome, for `--engine chrome` or a `native-dark` state, launches through
  Playwright with a throwaway profile, never the user's. Under Firefox, every state is a
  session of its own, with a fresh profile made in the run's scratch folder, and Firefox's
  own folders are redirected there too.
- **The report is safe to share.**
  - Every path in it is relative to `--out`, or a PDF's bare name.
  - The scrub in `src/print_report_scrub.py` rewrites every problem, every network entry,
    each template of pdfminer's log, and each string the page supplied to the read-back: an
    image's source, an animation's name and target, each string a read returns, and each
    storage key and value read back. The scrub writes the out dir as `<out>` and this
    account's home as `~`, in any case, sought only when the home is an absolute path. It
    writes another
    account's home-rooted path as `<home>`, and this machine's hostname, where it stands
    alone, as `<hostname>`. A same-origin image is reported by its path, so no port is
    recorded.
  - Some values are withheld whole instead of scrubbed: the page's title in the read-back,
    the PDF's `Title`, `Creator` and `Producer`, and each link's URI and destination. When the
    privacy matcher flags one, judged alone, it reads `(withheld: flagged)` in the report. Font
    names are neither scrubbed nor withheld.
  - A login standing alone in page text or a title is _not_ scrubbed. A personal site's words
    legitimately carry its author's name, often the login; the privacy flags still cover the
    login in metadata.
  - A date is reported only by its class: `utc`, `local offset (withheld)` or `unparsed`,
    never its value, since a local offset says where the file was made.
  - An engine's error goes to stderr and never into the report: its first line can name an
    executable's path. The path of the geckodriver a Firefox run found is printed on stdout,
    and never reaches the report either.
  - Under Firefox, geckodriver's log can name the scratch folder, so its path is written as
    `<scratch>` in each state's slice of the log before any of it is recorded; and of the log,
    only the page's own error lines are kept, each by its message alone.
- **Privacy flags.** Each PDF read gets a flag, which is a finding, for every place its
  metadata, links or text identify this machine or its account. A flag names where and the
  class of identifier, never the value. What is sought where is the table atop
  `src/pdf_privacy.py`. In short: an `Author` naming someone, this machine's login, the
  account's full name, its hostname, or a home-rooted path in the metadata; and a `file:`
  link, `file://` text, the hostname or a home-rooted path in the links (a URI and a
  destination alike) and the text layer.
- **Where names are not sought.** The full name and the login are not sought in the text
  layer or the links' URIs and destinations, where a personal site's own words legitimately
  carry them. Neither are they sought in the Info `Title`, because Chromium copies the page's
  `<title>` into it; the hostname and home checks still apply there. An XMP packet is
  searched whole, names included, though Chromium writes none.
- **What is not recognized.** A home path wrapped across two lines of text, or drawn
  letter-spaced, reads as separate pieces and is not recognized, on the page and in a
  figure alike; nor is a hostname drawn letter-spaced. A file embedded in the PDF (its
  `/EmbeddedFiles`, or a file attachment) is never opened or searched.
- **One seam reads the machine.** This machine's identity is read by one function,
  `host_identity`. Every unit suite injects an identity of its own (the suite of
  `host_identity` itself fakes each host read it makes); the live suites read this machine's.

## Exit codes

| Code | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `0`  | Every state printed and every PDF read without a finding; warnings are allowed. In compare, the two PDFs render identically.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `1`  | A finding. In compare, the two PDFs differ, a page-count mismatch included.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `2`  | The run could not measure honestly, and wrote no report. The causes: an option it cannot use; a targets file it cannot read or refuses; a declared redirect it cannot follow; an `--out` refused; Playwright, a PDF reader, an engine build, installed Chrome, geckodriver, installed Firefox or a poppler reader missing; Firefox asked for off macOS; a browser call that failed outright; a Firefox page whose isolation did not hold; a scratch folder that could not be made or removed; a compare PDF that cannot render; a call the watchdog abandoned; or an unexpected error, whose traceback is printed. |

## Tests

From the root of this repository, after the installs in the [root README](../README.md):

```bash
python print_pass/test_print_pass.py
```

`test_print_pass.py` aggregates the grouped suites under `tests/`, one or more per module, and
floors each file's test count separately in `_MIN_TESTS_BY_FILE`, so a suite that loses tests
fails rather than shrinking unseen. One suite, then the live suites alone:

```bash
(cd print_pass && python -m unittest discover -s tests -t tests -p 'test_print_targets.py' -v)
(cd print_pass && python -m unittest discover -s tests -t tests -p 'test_print_pass_live*.py' -v)
```

The aggregate includes the live suites, so it launches the pinned headless shell, installed
Google Chrome where it is present, and installed Firefox, fenced, on a macOS host with what
the Firefox suite needs.

**The live suites need poppler and the engine build.** Where poppler's readers are not on
`PATH`, or the headless shell Playwright pins is not installed, each live suite skips, with one
line on stderr naming what is missing and what installs it; everything else still runs. A skip
is a passing run, so a run that must measure the engine sets `PAGE_PROOFS_REQUIRE_BROWSERS=1`,
and what is missing then fails the suite instead:

```bash
PAGE_PROOFS_REQUIRE_BROWSERS=1 python print_pass/test_print_pass.py
```

- **Unit suites** fake the browser, poppler and this machine's identity, and write only under
  temporary folders. git, pypdf and pdfminer run for real, on the repositories, folders and
  PDFs the suites build, the PDF readers' worker as a real child process where the bound is
  what is tested. The poppler readers run as real fake executables on `PATH`, and the
  privacy matcher is handed its identity.
  - The Playwright engines run against a duck-typed fake Playwright
    (`tests/_print_playwright_fixtures.py`) in which these calls are traps that fail the test:
    `emulate_media`, which is not the print path; a socket route's `close`, which hangs the
    real synchronous API from the route's handler; and its `connect_to_server`, which would
    reach the socket's own server. Its context hands scripted payloads to the binding the
    real shared CSP collector exposes, as the collector's in-page listener would; how a
    violation becomes a finding, and so does a payload the listener never sends, is
    `tests/test_print_csp.py`'s.
    That suite also shows, in fresh interpreters, that the collector loads with the engine
    and brings no measuring package with it, and that without it the CLI stops before
    writing anything.
  - The Firefox engine's suites run against the stand-ins in `tests/_gecko_*_fixtures.py`: a
    WebDriver session that answers each command as its case scripts it, a recording transport,
    a scripted geckodriver with its process, group, clock and process listing, and every seam
    of a whole run. None of them starts geckodriver or Firefox. Where a suite needs the
    operating system's own answer, it acts only on what the suite made, such as a process
    group of its own children, or an ACL it set on a folder of its own. The live Firefox
    suite's fence and watch fixtures share that name pattern (below).
- **Property tests.** Each function marked `# fuzz-target`, every parser of untrusted text or
  bytes among them, has a Hypothesis property test named `test_fuzz_<function>`, run in the
  suites under a deterministic profile.
- **Live suites** drive a real engine through the whole CLI, each run a process of its own,
  killed at a bound. Each run's bound, the fenced Firefox run's included, is its share of one
  budget, `_live_run_bounds()` in `tests/test_print_pass_live_sites.py`, which a case there
  holds, with room for the other suites, inside the aggregator's own ten-minute budget, so that
  a CI or coverage timeout of that length still sees each hung case fail by name.
  - **The Playwright engines' suites** skip, or with `PAGE_PROOFS_REQUIRE_BROWSERS=1` fail,
    where poppler or the headless shell Playwright pins is missing, naming the install; they
    fail where Playwright itself is, which the `test` extra installs. Of their cases, the
    installed-Chrome suite's also skip on a host without it.
    - `tests/test_print_pass_live.py` prints the sample once. Its report, normalized, must
      equal `samples/expected_report.json`. It checks that the stored mode reads back dark,
      the lazy image is complete before the print, the running animation is reported,
      emulated dark is warned of and prints differently from native light, and real poppler,
      pypdf and pdfminer read the PDF.
    - `tests/test_print_pass_live_sites.py` holds one page, run once on the shell: an image
      off loopback is blocked and fails its state, two off-loopback WebSockets are reported
      without a hang, and the one style attribute its policy refuses is a `csp violation`
      finding, and of the endless animations its script starts in shadow roots, the
      warning names the open root's and the nested open root's, never the closed root's.
      Its carousel of lazy slides, which its script lays 4,000 px apart in a horizontal
      scroller, reads complete, and the missing slide broken. Beside it, and run with or
      without the engine, sit a run past its bound failing its case, every live run's bound
      fitting the budget, the installed packages matching the `print` pins, which the `test`
      extra repeats, the sample README's command writing the image the suites serve, and the
      skip and its opt-in.
    - `tests/test_print_pass_live_chrome.py` prints the sample, with that carousel added,
      once on installed Chrome: `native-dark` reads dark and prints light, and in each of its
      three states every slide reads complete and the missing one broken.
  - **The Firefox engine's suite**, `tests/test_print_pass_live_firefox.py`, runs the tool
    once with `--engine firefox`, over one page in its default state, and checks what only a
    real Firefox shows: the off-site requests alone fail the state, blocked; no site icon was
    fetched and the lazy image below the window arrived, so the prefs that keep the record the
    page's own still take; a word the page writes at `beforeprint` is in every PDF, so
    Firefox's own print path ran; Firefox's own host-resolver log names no host off loopback,
    so its own layers held, not the fence alone; nothing the run started still runs, by the
    pids and arguments a watcher recorded, never by name; and the scratch folder is gone and
    named nowhere in the report.
    - **Its fence.** The run finds first on its `PATH` a geckodriver shim of the suite's own,
      which runs the real geckodriver inside a Seatbelt profile, through macOS's
      `sandbox-exec`, refusing every connection off loopback, the system resolver, and the
      reader's own Firefox folders, by their resolved paths under both the environment's home
      and the account's. Before anything launches, the profile's canaries run under the same
      file, in a process and in a child it starts. Each proves both ways to the resolver shut
      before it looks any name up, and stops at the first that is open, so no weakened fence is
      ever asked a name. `sandbox_check` then asks from outside the canary whether the rules no
      canary can prove by behavior hold. A fence that does not hold _fails_ the suite, and is
      never a skip. Firefox runs under such a fence only with its own sandboxes off, which the
      shim turns off.
    - **When it skips.** It skips, naming what is missing in the skip and in one line on
      stderr, off macOS or without geckodriver, Firefox or `sandbox-exec`, even with
      `PAGE_PROOFS_REQUIRE_BROWSERS=1`, which covers poppler and the engine build alone; without
      poppler it skips or fails as the suites above do; with all of them present it never
      skips. `tests/test_gecko_live_fence.py` and
      `tests/test_gecko_live_watch.py` prove the fence's parts and the watcher without Firefox.
- **JavaScript.** `src/print_probe.js` is one bare function expression. Under Playwright,
  `page.evaluate` calls it in the page; under Firefox, WebDriver's Execute Script runs it,
  wrapped as a synchronous script (`PROBE_SCRIPT`, in `src/gecko_page.py`). Both hand it the
  same argument (`run_probe`, in `src/print_probe.py`). `tests/test_print_probe.mjs` runs the
  same file under `node --test` through `vm`, with plain fakes (a shadow root among them, with
  its own `getAnimations()` and `querySelectorAll`), so coverage lands on the file itself. The
  repository's `npm test` holds it at 100% branch coverage beside the other tools' in-page
  scripts, and its `tsconfig.json` type-checks it:

  ```bash
  npm ci --ignore-scripts
  npm test
  ```

`samples/` is the hand-verification sample: one page printed in each state its
`targets.toml` names, and the report it must produce. `samples/expected_report.json` is a
run's report normalized by `normalize_report` in `src/print_report_output.py`, the one
statement of what varies by run, machine, installed fonts or engine build. What it keeps
still depends on the pinned engine laying the page out in the host's default serif: the page
count and whether two prints compare identical. A Playwright pin bump regenerates it, with
the command `samples/README.md` gives.
