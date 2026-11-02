# browser_pass

Renders a web project's pages in pinned Chromium, Firefox and WebKit builds, at every viewport in a
targets file (phones in portrait _and_ landscape), light and dark, with scripts on and off, and
each _state_ the file names: a page reached by stored values and clicks rather than by its load
alone. It runs its page-load detectors on every rendering, loads each page once more in Chromium
to catch what a late script moves, compares each engine's layout with the first engine's, and
exits `0`, `1` or `2`.

## Why it exists

Four browser passes of this shape were assembled by hand before this tool: a Chromium phone pass,
a WebKit plus Chrome pass, a Firefox pass, and a pass over a site's archive pages. Every rebuild
paid again for the same instrument faults, and each one produced a confident zero before it was
caught:

- a CSP standing in for scripts off, which left `(scripting: enabled)` matching, so the
  stylesheets laid out a page no scripts-off browser ever shows;
- an emulation option an engine silently ignored;
- a classic 15px scrollbar that shifted every width;
- planted faults that never planted, because they were planted before `<body>` existed.

Building this tool found two more:

- the shared loopback server reset part of every Chromium page load, because it listened with
  `socketserver`'s queue of five (fixed in `loopback_site`);
- Firefox reads the real profile folder in the home directory at startup, even when handed a
  throwaway profile.

The tool turns each of these into a guard that refuses to measure, rather than a note someone
has to remember.

The late-script load came later, for a fault class the page-load detectors cannot see at all:
a page that first paints, then moves when its scripts upgrade it. Three such movers were each
found by a scratch instrument assembled for the occasion, the thing this tool exists to end.

## Usage

From the root of this repository, after the installs under "Installing the engines", name the
targets file and the folder of pages to serve on loopback. Every command here runs in the
virtual environment the README's "Install" makes, so `python` is the venv's interpreter:

```bash
python browser_pass/browser_pass.py --targets "<targets file>" --serve "<built site>"
```

That is the whole pass: every page in the targets file at every viewport, both orientations of
each phone, light and dark, scripts on and off, and every state at every viewport and in both
modes with scripts on, in all three engines. Add `--prove` to also plant one fault per detector
in every rendering. A narrower run while chasing one thing:

```bash
python browser_pass/browser_pass.py --targets "<targets file>" --serve "<built site>" \
  --engines webkit --orientations landscape --modes dark
```

The sample under `samples/` is a run to check the tool by hand: its `README.md` gives the
command and says what each rendering should report.

## Options

| Option                     | Default                                                    | Meaning                                                                                                                                                                            |
| -------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--targets PATH`           | required                                                   | TOML file naming the pages and viewports (see "The targets file").                                                                                                                 |
| `--serve PATH`             | none                                                       | Folder to serve on loopback and measure. Without it, the targets file's `base_url`.                                                                                                |
| `--engines NAME ...`       | `chromium firefox webkit`                                  | Engines to render in. The first is the reference the others are compared against.                                                                                                  |
| `--orientations NAME ...`  | `portrait landscape`                                       | Which orientations of a rotating (phone) viewport run. A fixed viewport always runs.                                                                                               |
| `--modes NAME ...`         | `light dark`                                               | Color schemes, set with the engine's own `prefers-color-scheme` emulation.                                                                                                         |
| `--scripts NAME ...`       | `on off`                                                   | Scripting on, and off through the engine's own switch. Never a CSP.                                                                                                                |
| `--browsers PATH`          | see "Installing the engines"                               | Folder holding the engine builds.                                                                                                                                                  |
| `--out PATH`               | none: a `browser_pass_runs` folder beside the targets file | Folder receiving one new timestamped run folder. The default is gitignored.                                                                                                        |
| `--timeout SECONDS`        | `30.0`                                                     | Seconds allowed for each step of a page: its load, its settle, each action of a state, and each wait of the late-script load.                                                      |
| `--prove`                  | off                                                        | Also plant one fault per detector in every rendering, and fail if one goes uncaught.                                                                                               |
| `--require-csp`            | off                                                        | Also find each page as it loads, scripts on and off, whose markup declares no Content-Security-Policy meta. Never a state.                                                         |
| `--chromium-extension DIR` | none                                                       | An unpacked extension folder holding `manifest.json`, loaded into every Chromium context, in Chromium's full build. Implies `--loopback-only` (see "Measuring with an extension"). |
| `--firefox-extension XPI`  | none                                                       | A Mozilla-signed `.xpi` whose manifest gives a gecko id, sideloaded into every Firefox context's throwaway profile. Implies `--loopback-only`.                                     |
| `--loopback-only`          | off                                                        | Refuse requests off the served origin in Chromium (full build) and Firefox, off 127.0.0.1 in WebKit, whose other loopback ports and UDP stay open: an extension run's baseline.    |

## The targets file

```toml
base_url = "http://127.0.0.1:8123"   # optional: used only when --serve is not given
not_found = "/404.html"              # optional, with --serve only: answers a page with no file, 404
pages = ["/", "/archive/", "/index.html#latest", "/?panel=1"]  # server paths, each starting with /
redirects = ["/archive/"]            # optional, with --serve only: meta-refresh pages to follow

[[viewport]]        # one table per size
width = 390
height = 844
rotates = true      # a phone: also runs on its side (844x390)

[[state]]           # optional: a page reached by more than its load
name = "after-start"
page = "/"
storage = { mode = "dark" }                  # set through storage_state: no injected script
actions = [{ click = "#start" }, { wait_for = "#panel" }, { wait_ms = 300 }]
```

A viewport marked `rotates` runs as its taller shape (portrait) and its wider one (landscape),
whichever way round the file writes it. A viewport without it runs once, as written. An unknown
key is refused rather than ignored, so a misspelled `rotate` fails loudly instead of quietly
measuring portrait only. A targets file belongs to the project it measures, since which pages
and sizes matter is that project's call.

A page may carry a `#fragment`. It is loaded with it, so the browser lands scrolled to the
target: an anchored arrival is its own reading of a page, and the one a late-moving layout hurts
most. `--serve` confirms such a page against the file its path names, since a fragment never
reaches a server.

A page may carry a `?query` as well, for a page whose scripts read it: `/?panel=1` opens the
first panel. It is loaded with it, and the query does reach the server, but a static server
answers it from the file its path names, so `--serve` confirms that file, as for a fragment.

No server path the file names, whether a page, a state's page or `not_found`, may hold a `.` or
`..` segment: none needs one, and a `..` could name a file outside the `--serve` folder, which is
checked for and read before anything renders. Such a path is refused, saying so. A dot inside a
segment, as in `/a.html` or `/.well-known/`, is no segment of its own, and a query or fragment is
not part of the server path. A backslash separates segments as a slash does.

### A page with no file

GitHub Pages answers a request that has no file with the site's own `404.html`, status 404, so
an address with no page behind it still shows the site's 404 page, and a site can make that
page react to the address. `not_found` lets `--serve` answer the same way, so such a page can be
measured as a visitor who typed the address meets it. It names a file in the served tree as a
server path with one leading `/`, such as `/404.html`. Without it, a page with no file is refused
(exit `2`), because the server does not hold it.

`not_found` needs `--serve`: a server this run does not start answers a missing page its own
way, so the key is refused without it. One naming no file in the tree is refused as well, before
anything renders. Both exit `2`.

Whether a page has a file is judged by its server path, its query and fragment aside: the file
`--serve` would confirm. A page with none is confirmed to answer 404 with exactly the not-found
file's bytes, and since it is _expected_ to answer 404, that one answer is excused, and nothing
else. Excused: the response to the page's own document, and the console line Chromium and WebKit
log for a 404 at that URL (Firefox logs none), each once per rendering. Still findings: a
subresource such a page asks for and cannot have, the `--prove` missing-path plant among them
(the not-found file answers it 404 too, so the plant is still caught); its document answering
any other status, or a second time; another document; and a page that has a file answering 404.
Whether such a page answers 200 instead is not judged in the browser: confirmation has already
required its 404 before anything renders.

### A page that is a meta refresh

A page whose markup is a `<meta http-equiv="refresh" content="0; url=2026/">` navigates away as it
loads. Loaded in a browser and then measured, it is a page being torn down, and reading it fails
with `Execution context was destroyed`. So with `--serve`, once every page is confirmed, each one
is read over loopback for a meta refresh before any browser opens it, through the shared
`loopback_site` reader. A page with no file is not read (it answers 404), and a page with a
`#fragment` or a `?query` is read at its server path.

`redirects` lists the pages that are one, each a page `pages` also lists, once, and never the
`not_found` page. A listed page must refresh at once (a delay of 0) to a page on the same server
that answers with its own file, and itself declares no meta refresh: then that destination is
measured _in its place_, every rendering of the listed page moving to it, and the report names the
pair. A destination `pages` lists as well is measured once, not twice. An archive stub is the case
it exists for: `/archive/` points at the current year's page, so the targets file names
`/archive/` and never a year that would go stale each January.

A redirect is followed only when it is declared. Each of these stops the run with exit `2` before
anything renders, naming the page and the reason:

- a page not listed in `redirects` is a meta refresh (any delay): the message names where it
  points, and says to list it in `redirects`;
- a listed page declares no meta refresh, or has no file;
- a listed page refreshes after a delay rather than at once;
- its destination is off the served site, is not served with its own file, or is itself a meta
  refresh (one hop is followed, never a chain).

`redirects` needs `--serve`, as `not_found` does: only a tree this run serves is read before it
is measured, so the key is refused without it. Without `--serve` no page is read for a refresh.

### States

A state is a page as a person reaches it rather than as it loads: values a returning visitor has
stored, then clicks and waits. Two limits are deliberately narrow: a name's alphabet
(`[a-z0-9][a-z0-9-]*`) and the `wait_ms` ceiling (10000).

| Key       | Takes                                                                                                                                     | Meaning                                                                                                                                                                                                     |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`    | lowercase letters, digits and hyphens, opening with a letter or a digit; required, and unique in the file                                 | Names the state in every report line, after its page: `/ [after-start]`.                                                                                                                                    |
| `page`    | a server path starting with `/`; required. It may carry a `?query` and a `#fragment`, as a page may                                       | The page the state starts from. `--serve` confirms it like any other page.                                                                                                                                  |
| `storage` | a table of strings to strings                                                                                                             | Stored in `localStorage` for the served origin before the page loads, through Playwright's storage state (see "Measuring with an extension" for a persistent context): no script is injected to write them. |
| `actions` | an array of tables, each holding exactly one of `click` (a selector), `wait_for` (a selector, until visible) or `wait_ms` (1 to 10000 ms) | Run in order once the page has loaded and settled. A page that needs longer than ten seconds is one to wait on by a selector, which is why `wait_ms` stops there.                                           |

One rendering of a state is that state in one engine, at one viewport size, in one mode, with
scripts _on_: the actions click and wait on what a page's scripts build, so a state never
renders with scripts off, and a run whose `--scripts` leaves `on` out renders none of them and
says how many it left out. Each rendering of a state is a fresh context holding its stored
values: the page loads and settles, the actions run in order, the page settles again, and only
then is anything measured: the read-back, every detector, the digest and, with `--prove`, every
plant. The late-script load does not run for a state: it measures a page's load, and a state is
reached after it, so the state's late shift is `n/a`. A state is compared across engines only
with the same state.

An action that does not complete within `--timeout` (a selector that never matches, a click on
something that never becomes clickable) is an instrument fault, exit `2`, naming the state and
the action, counted from one: `state after-start: action 2 (click "#x") timed out`. The state was
never reached, so nothing measured after it would be a measurement of it.

## The detectors

| Detector          | Fires when                                                                                                                                                                                                                                                                                                                                                            |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `page_overflow`   | the document is wider than the viewport (`scrollWidth > clientWidth`).                                                                                                                                                                                                                                                                                                |
| `past_right_edge` | a displayed element's right edge is past the viewport, with nothing between it and `<body>` that scrolls or clips it sideways. A carousel's off-screen slides are inside a scroller and do not count; `<body>` and `<html>` clipping does _not_ excuse an element, since clipped content is content nobody can reach. Only the outermost offending element is listed. |
| `hidden_rendered` | an element carrying `hidden` is still visibly rendered (it has a box and is not `visibility: hidden`), usually because a class rule's `display` beat the `[hidden]` rule. Exempt: `hidden="until-found"`, and a layout-shift reserve, a script-revealed element deliberately held as `visibility: hidden` so its space is kept.                                       |
| `small_target`    | a link, button, form control, `summary`, button or link role or focusable element is under 24px on either side (WCAG 2.5.8). Exempt: `display: inline` (a link inside a sentence), a 1px visually-hidden box, `:disabled`, and anything `inert`.                                                                                                                      |
| `console_error`   | the page logs a console error. Chromium and WebKit log one for every 404 response, so a page with no file, served with `not_found`, is excused the line for its own document's 404, and only that one.                                                                                                                                                                |
| `page_error`      | a script throws an exception nothing catches.                                                                                                                                                                                                                                                                                                                         |
| `http_error`      | a response has status 400 or above, or a request fails outright (a reset connection is one). A page with no file, served with `not_found`, is excused its own document's 404 and nothing else (see "A page with no file").                                                                                                                                            |
| `csp_violation`   | the page's Content Security Policy refused something: one finding per distinct refusal, `style-src-attr blocked inline on /404.html`. With `--require-csp`, also a page as it loads whose markup declares no policy. `not measured` with scripts off; see "Content Security Policy violations".                                                                       |
| `late_shift`      | the page moves after its first paint once its scripts run: a session window of layout shifts scoring above the threshold, in its own load with the scripts held (see "The late-script load"). Chromium only; elsewhere `not measured`, and `n/a` with scripts off and in a state.                                                                                     |

**Inside shadow trees.** A selector query on the document never crosses into a shadow root, so
`past_right_edge`, `hidden_rendered` and `small_target`, the layout signature the settle polls
and the digest the cross-engine comparison reads all walk the document's elements and every
_open_ root's, found recursively through each host's `shadowRoot`, through one walker,
`src/page_probe_trees.js`, which the motion stilling uses too. A root's elements are read right
after their host and named through it, `body>x-card:1>#shadow-root>button:1`, so each engine
names the same element alike. The two climbs toward `<body>` follow the route a box is laid out
by, the flat tree: from a slotted element to its slot and from the top of a root to its host. So
a scroller inside a root excuses what is slotted into it, and an `inert` host makes its root's
controls inert. An element slotted into a root lives in the light tree and is read there, once,
and a box already found past the edge stands for what sits inside it across a box-less `<slot>`
or `display: contents` wrapper. A root on `<body>` itself is read too, ahead of its light
children.

A _closed_ root is out of every page script's reach (its host's `shadowRoot`, and a slotted
element's `assignedSlot`, read null), so nothing inside one is read: a known limit, with the
reasons the alternatives were declined under "A known limit: motion inside a closed shadow root".
Measured in all three engines on a 1,601-element page, the walk costs a page with no roots 0.02
to 0.06 ms more per signature poll; a page with roots pays for the boxes inside them.

## Content Security Policy violations

The `csp_violation` detector reads the shared [`csp_watch`](../csp_watch/csp_watch.md) collector.
With scripts on, every rendering's context gets it before its first page, so a refusal during the
load is caught, and so is one a state's actions cause. Each violation the engine reports reaches
the collector the moment it fires, through a binding, so a navigation such as a meta refresh
erases nothing. The rendering's findings are read when it is measured, before anything is
planted: one line per distinct refusal, naming the directive, what it blocked and the document.
A refused URL on the measured origin is named by its path, as an HTTP error's is, so `--serve`'s
port never reaches a finding. In Chromium the late-script load watches its own context as well,
and what it reports joins what the page load found, each refusal once; every other detector keeps
its repeats.

**Only the measured page's own documents are kept, in every run**, with or without an extension:
the reports of the pages the pass opened itself, from a document on the origin that page's main
frame is at as it reports. That is the measured origin, which is not always the base URL's: a
base URL that redirects (`http` to `https`, a bare host to `www`) lands the page on another, and
the page's violations are still its own. What else is dropped, and why, is under "Measuring with
an extension".

**Scripts off is `not measured`, never zero.** The collector's listener is an init script, and an
engine runs none with the page's scripts off, so nothing could report a refusal there. The
detector table says `not measured` for an engine with no scripts-on rendering, as it does for a
late shift, unless `--require-csp` found a page without a policy there, which it counts. The
refusal is not lost, though: every engine also logs each violation as a console error, scripts on
and off, so `console_error` reports it either way, without the directive, in the engine's own
words. With scripts on, a refused attribute, inline style or script is therefore reported twice,
once by each detector, and with scripts off only the console error remains. Measured on
`samples/site/csp.html` in all three pinned builds. A refused load is reported once more in
Chromium: the engine fails the request as `csp` before any route sees it, so `http_error` records
`failed <path> (csp)`, scripts on and off.

**`--require-csp`** makes a page as it loads whose markup declares no Content-Security-Policy meta
a finding of this detector, `page carries no Content-Security-Policy meta`, scripts on and off:
the meta is read through the driver, which an engine runs whatever the page's scripting. A state
is not held to it, since it is the same page reached another way. The option is off by default
and the report's facts say whether it was on. A policy delivered in an HTTP header is not a
meta, so a server that sends one should not be measured with this option.

**The plant is made last.** With `--prove` it adds a policy of its own and breaks it with a style
attribute on the root element, and it is caught when the collector reports that attribute's text.
The planted policy stays in force for the rest of the document and refuses every style attribute
and inline `<style>` added after it. No other plant is drawn with either (see below), so none
depends on the order any longer; the plant still waits until every other plant has been read, as
cheap insurance that the instrument's own policy refuses nothing the page restyles meanwhile.
With scripts off it is `n/a` and is never made.

**A page with a policy of its own still takes every other plant.** The in-page probe writes each
plant's declarations through the CSSOM (`style.setProperty`, with `!important`), never as a style
attribute, and Content Security Policy does not govern the CSSOM. So a page whose policy refuses
style attributes refuses none of those plants, and `--prove` proves on it: measured on
`samples/site/csp.html` in all three engines, scripts on and off, every plant that is made is
caught, Chromium's late-shift plant among them, and the rest read `n/a` or `not measured` as on
the main sample. The CSP plant alone is a style attribute, because being refused is what makes it
a violation.

## The late-script load

Whether a page visibly moves when its scripts upgrade it is a race. On a fast connection the
scripts usually arrive before the first paint and nothing moves; on a slow one the reader sees
the page, then sees it jump. An ordinary load therefore reads zero on the very page that jumps,
and throttling the network only makes one outcome likelier. This load decides the race instead,
in a browser context of its own after the page-load detectors have finished, so they are neither
slowed nor perturbed:

1. **Hold** every script request the page makes; everything else goes straight through. Only a
   _request_ can be held, so an inline script is never one, which is honest, since an inline
   script does not race the network and a reader never sees it arrive late either.
2. **Release** the scripts once the page has painted and its web fonts have loaded. Waiting for
   the fonts keeps a face that swaps in late from being counted as the scripts' doing; a page
   whose fonts never arrive is refused rather than measured against that risk. When a held
   script is one the page cannot paint without (`blocking="render"`, or a classic script in
   `<head>` without `async` or `defer`), release at once: no paint can come until it runs.
3. **Wait** until the page is _presentable_: painted, then its fonts loaded, then two frames.
   The page marks that moment from its frames, and on a loaded machine frames can trail its
   main thread, so this waits for it rather than assuming `load` brought it (see "Waiting for
   the frames"). A page refused whatever comes is not waited for.
4. **Settle**, stilling only the motion it can prove moves no layout before each poll, where
   the page-load settle stills all of it (see "Stilling motion"), then **read** what a buffered
   layout-shift observer recorded. The observer is installed before any of the page's own code,
   and counting starts at the later of the release and the presentable moment. Both moments are
   marked inside the page.
5. **Score** those shifts in session windows the way Cumulative Layout Shift groups them (a gap
   of a second starts a new window; none spans five). Each window above the threshold
   (`THRESHOLD` in `src/pass_shift.py`, which the report prints) is a finding naming its score
   and what moved. A page that blocks its first paint on its scripts has nothing on screen to
   move, which is what makes it the fix for a late shift.

**What moved is the engine's own attribution**, as the Layout Instability API reports it: up
to five nodes per shift, and often a container or a text node's element rather than the
element a person would name. When several siblings move together, Chromium can name their
parent, `<body>` included.

**Counting from "presentable" rather than from the release is not a refinement: it is what keeps
the fonts protection working on the page that needed it most.** Usually the two moments are the
same instant, because the release waits for exactly that. They come apart on the early-release
path above: a page whose script blocks its rendering is released _before_ it has painted, so
counting from the release would charge it with a web face swapping in later, on precisely
the pages that adopted `blocking="render"` to stop a late shift. Found that way, in fact: making a
site's entry point render-blocking gave every home-page rendering a fresh window at 73-77ms
naming its header and heading text, while the two pages whose entry points were untouched scored
identically to the run before.

Two details make the moment exact. `document.fonts.ready` is awaited _after_ the first paint,
never before: with no layout yet there is no text to match a face to, so it resolves at once on
an empty document. And it resolves when the faces are _in_ rather than when the frame showing
them has been drawn, so the swap's own shift can still be timestamped after it; hence two
animation frames more, the same wait the late-shift plant makes for the same reason. Without them
a font swap scored 0.0027 against a script on one run out of two. **A page that does not become
presentable within `--timeout` is refused**, the same choice the fonts half of the hold already
makes on the other path.

**Waiting for the frames.** The presentable moment is marked by frames: the first paint is only
in the timeline once a frame has been presented, and the two frames after the fonts are frames.
The layout settle watches the main thread instead, so the two can come apart. Measured with
frame production stalled at `load` (CDP's `Emulation.setVirtualTimePolicy`): the layout was
loaded and still while the timeline held no paint at all, and once frames resumed the page
painted and became presentable. A record read straight after the settle refused that page, ten
times in ten, as one that "never painted in its own fonts". Unstalled, the moment came 19-74ms
after `load`, against a settle window of a second (0.3s in the live suites), which is why it
surfaced only on a loaded machine. So the load waits for that moment, within `--timeout`, before
it settles. CDP's CPU throttling cannot stand in for this: it slows the main thread, not the
frames, and an animation loop still drew about 53 frames a second at 100x.

**Only Chromium implements the Layout Instability API.** Firefox and WebKit report `not
measured`, never a clean zero, and the load still reads the page's own answer: an engine build
that stops reporting shifts is refused rather than read as a page that did not move. With
scripts off there is no script to be late, so the detector is `n/a`.

**It costs one more load** per Chromium rendering with scripts on, and every request on it goes
through the driver so the scripts can be held; each rendering's watchdog bound covers both loads.

## What makes a zero mean something

Each guard below either stops a rendering (exit `2`) or proves a detector can fire:

| Guard                                          | How                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Every emulated state is read back              | Before measuring, the page reports its own viewport size, `clientWidth`, `(scripting: none)`, `prefers-color-scheme`, `orientation` and `prefers-reduced-motion`. Any disagreement with what was asked for is an instrument fault, and that rendering is not measured.                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Scripts off is the engine's switch             | Scripting is turned off on the browser context, never with a CSP, and the read-back above confirms the page's own `(scripting: none)` matches.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| No classic scrollbar                           | Firefox launches with overlay scrollbars, and the read-back refuses any rendering whose `clientWidth` is narrower than its viewport.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| The page has settled                           | After `load`, every animation still running on the document's timeline, in the document or in any open shadow root, is brought to rest before each poll (finished if it ends, paused at an iteration start if it never does), and the layout, every box in the document and in any open shadow root, is polled until it holds still for a second. A page still moving when `--timeout` runs out is refused, not measured mid-change. See "Stilling motion".                                                                                                                                                                                                                                         |
| Plants are checked, then caught                | `--prove` plants each fault after load, through the DOM and past any author rule. It confirms each plant took before reading the detectors. A plant that did not take is an instrument fault (`2`); a detector that missed one that did is a failure (`1`). The page-error plant needs a script to throw it, so with scripts off it is `n/a`, not a pass. The late-shift plant is a box drawn across the viewport and moved down by half of it two frames later; it must be reported in a window above the threshold. The CSP plant comes last and has no take check: the collector's report is its only signal, so it reads `caught` or `missed`, never `not planted`, and `n/a` with scripts off. |
| Scripts are held, not raced                    | The late-script load holds every script until the page has painted in its own fonts, so a page that jumps on a slow connection cannot read zero on a fast one. A page released early because a held script blocks its rendering is counted from the moment it became presentable instead, so its fonts are never charged to its scripts.                                                                                                                                                                                                                                                                                                                                                            |
| A late shift needs a first paint               | A page that neither painted before its scripts ran nor has a held script blocking its rendering cannot tell a late shift from none, and is refused. So is a page whose fonts never finished, since the release would have been cut short and a later swap charged to its scripts, and so is one whose engine does not report layout shifts, whatever the engine claims to be.                                                                                                                                                                                                                                                                                                                       |
| A state is reached, or refused                 | A state's detectors read the page only after every action has completed and the page has settled again. An action that does not complete within `--timeout` is an instrument fault (`2`) naming the state and the action, never a page measured as it loaded under the state's name.                                                                                                                                                                                                                                                                                                                                                                                                                |
| The CSP collector speaks only for its listener | A violation the collector is sent in any shape but the one its listener sends is an instrument fault (`2`) naming the rendering, never a finding: the page can call the binding itself.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| The tree measured is the tree named            | `--serve` goes through `loopback_site`, which confirms every page's bytes against disk before anything renders, a state's page among them. With `not_found`, a page with no file is confirmed instead to answer 404 with exactly the not-found file's bytes. A page that is a meta refresh is measured at its destination only when `redirects` declares it, and that destination is confirmed the same way.                                                                                                                                                                                                                                                                                        |
| Nothing hangs                                  | Playwright's `evaluate` and every `close` take no timeout, so each rendering and each browser close runs inside a watchdog's bound (one `--timeout` for each step of both loads, plus a margin, for a rendering; a state's rendering adds one for each action and for its second settle, and a `wait_ms` its own length). An overrun ends the whole run with exit `2`, naming the step, rather than stalling with no result at all.                                                                                                                                                                                                                                                                 |

**Deliberately not measured:** resize behavior. Viewport emulation sets the size before the page
loads and never dispatches a `resize` event, so a resize detector built on it would pass
vacuously. Composited paint, glyph rasterization and a real phone's browser bars are also out of
reach of a headless engine; those need a person on a real device.

**Emulation is the same in every engine:** a plain viewport size, without the mobile flag.
Firefox has no mobile emulation, and using it in two engines but not the third would make the
cross-engine comparison compare two different things. A page that declares
`width=device-width` gets a layout viewport of the width asked for either way.

## Stilling motion

A stillness window alone measures a page with _paced_ motion (steps separated by pauses longer
than the window) in its first pause, silently and with a passing verdict, and a page with
_endless_ motion never at rest at all. A longer window is no cure: an endless animation never
holds still, so a page carrying one would time out instead. So before every poll of the
page-load settle, and of the late-script load's with the one restriction below, the probe
brings each animation the page can reach to rest: those `document.getAnimations()` lists, and
those of every _open_ shadow root, found recursively (the limit below says why, and what stays
out of reach). Each is handled according to its state:

| Animation                                                                                                                      | What happens                                                                                  | Counted as |
| ------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- | ---------- |
| not running: paused by the page, finished, idle                                                                                | untouched: it is what a visitor sees                                                          | nothing    |
| running on another timeline, or at a zero rate                                                                                 | untouched: it does not advance with time (a scroll-driven timeline), so waiting cannot end it | `running`  |
| running, in the late-script load, and not provably layout-inert: it moves another property, names none, or its rule is unknown | untouched: a seek of it could be a layout shift, charged to the scripts                       | `running`  |
| running, and it ends                                                                                                           | `finish()`: it is measured at its end state                                                   | `finished` |
| running, and it never ends                                                                                                     | `pause()`, then a seek to its first iteration start at or after local time 0                  | `paused`   |

A paused animation is therefore measured at one frame, its iteration start: its rest pose when
that iteration plays forward from keyframes that start at rest. A reversed iteration starts at
its _last_ keyframe instead (every iteration of `reverse`, the odd ones, counting from 0, of
`alternate` and the even ones of `alternate-reverse`), so a negative delay that skips an odd
number of `alternate` iterations is measured at the far end of its swing. Stilling inside every
poll catches an animation that begins mid-settle, and a seek that moves a box changes the layout
signature, so the quiet window restarts from the stilled layout. This reaches animations with the
page's scripts _off_ as well, through the driver's own evaluation. `finished` and `paused` are
summed over the polls, since each animation is stilled once; `running` is what still ran when the
settle ended. The counts can differ between engines: an animation with no fill that ended before
the first poll is already gone from `getAnimations()`, so it is neither stilled nor counted. The
motion is reported and never changes the exit code.

**The late-script load stills only provably layout-inert motion.** It counts layout shifts, and
a seek that moves layout is itself a layout shift, which it would charge to the scripts. So it
stills an animation only when it names at least one animated property and every one moves or
fades a box without laying it out again (the class `LAYOUT_INERT_PROPERTIES` in
`src/page_probe_motion.js` lists), and leaves any other running, counted `running`. A seek of a
layout-inert animation moves no layout, so the Layout Instability API reports nothing for it:
measured in the pinned Chromium, seeking such an endless animation far from where it stood
recorded no shift, where a `margin-top` control recorded 0.134. Stilling nothing cost real pages
their measurement: an endless transform loop, a wagging icon, changes its element's bounding box
each frame, and those of everything inside it, so the layout signature never held still and the
page was refused as never settling, although no layout shift was recorded and no box moved.
Endless motion that _does_ move layout is still left running, so a page carrying it is still
refused there as never settling, which is correct: a reader would feel it move. This load's
counts are kept and reported apart from the page load's, since both loads still the same
animations and a sum would count each twice.

**Where an animation's properties are read from.** A transition, or an animation a script made,
is read from its effect's `getKeyframes()`. A CSS animation is read as well from the
`@keyframes` rule the cascade _applies_ to it, because Chromium leaves custom properties out of a
CSS animation's `getKeyframes()`, registered or not: measured in the pinned Chromium, a rule
animating `--m` alone reports no property there, so a `--m` driving `margin-top` read as moving
nothing, was stilled, and the late load charged its seek to the scripts. Which rule applies, only
the engine's style resolver knows. A page script searching the sheets it can reach takes any
rule of the name as proof (a decoy moving `transform` alone, say), and cannot reach every rule
that can apply: one in an `@import`ed sheet, in the target's own shadow root (`:host`), in the
root it is slotted into (`::slotted`), and above all in a closed shadow root. So the late load
asks Chromium, over a DevTools session (`src/pass_inert.py`). The probe lists the running
animations into one array the page keeps, and beside it which of them to ask about: a CSS
animation that advances with time and whose `getKeyframes()` names nothing but layout-inert
properties, or nothing. One naming any other property is left running whatever its rule adds,
so its rule could decide nothing. The driver resolves each asked animation's target, or the
pseudo-element of it, to a node and reads `CSS.getMatchedStylesForNode`'s rule of the
animation's name, every keyframe's declared names; and the probe stills that _same_ array,
judging each animation by the names handed back beside it, with its `getKeyframes()` still
counted, since a script may have replaced the keyframes its rule gave it. Stilling the array the
rules were read for is what binds each verdict to its animation: scripts run between the calls,
so one started since the listing waits for the next poll rather than being judged by another's
rule. Anything unknown leaves an animation running: no rule of its name, a target or
pseudo-element that cannot be placed, a call the protocol refuses, and every CSS animation in an
engine with no DevTools session. So does naming no property: an empty list cannot be told from
one an engine emptied, and one that truly moves nothing never changes the layout signature, so
leaving it costs the settle nothing. The rule is read anew on every poll, since a script can
insert one mid-load, so a poll's protocol calls are a fixed handful plus a handful for each
animation asked about (more for a pseudo-element), and an animation not asked about costs none of
its own, however many run. After the first poll, what is asked about is the motion its rule alone
left running: a custom property moving layout, say. The page-load settle stills everything that
advances, whatever it moves, so it opens no session.

**Motion inside a shadow tree.** In all three pinned engines, `document.getAnimations()` does not
list an animation whose target lives _inside_ a shadow root (one on a shadow host, or on an
element slotted into a root, is listed): only that root's own `getAnimations()` lists it, a root
nested in another is listed by the inner root alone, and an element's
`getAnimations({ subtree: true })`, a host's or `<html>`'s, crosses into none. So the probe reads
the document's list and every _open_ root's, found recursively through each element's
`shadowRoot` by the walker the layout reads share (`src/page_probe_trees.js`), declarative roots
included, in every engine and in both settles. Each animation is in exactly one list, so none is
counted twice, and a root's motion goes through exactly the verdicts above, the late load's
provably-layout-inert rule included.

**A known limit: motion inside a closed shadow root.** A closed root, and any root inside one, is
out of the page's reach in all three engines (measured: its host's `shadowRoot` reads null), so
its motion is neither stilled nor counted. A loop in one that resizes its host is still refused
as never settling, while one that moves only boxes inside the root goes unseen, since the layout
signature reads every open root's boxes but none inside a closed root; the element detectors
find nothing inside one either (see "Inside shadow trees"). Chromium's DevTools could reach it
(`DOM.getDocument` with `pierce: true` reaches closed roots, and each root's own
`getAnimations()` lists its motion), and that was declined. In both Chromium settles, the
page-load settle would open a session it otherwise never opens and read the whole pierced tree on
every poll, and a closed-root layout loop would then settle in Chromium while Firefox and WebKit
refuse it: a verdict split made by the instrument. In the late load alone, it would add only a
count, and the two loads of one page would disagree. Wrapping `attachShadow` from an init script
was declined too: it changes the page under test, and misses a declarative
`<template shadowrootmode="closed">` root, which by the specification the parser attaches
without calling `attachShadow`.

**A known limit: a re-targeted CSS animation.** A CSS animation a script has re-targeted, by
setting its `effect.target` to another element, is judged by the rule the cascade applies under
its name to that _new_ target, or left running where none does: no page script and no protocol
call names the element that declared it, so there is nothing to read the original rule from.

**Motion a script drives is out of reach.** An element moved frame by frame by
`requestAnimationFrame`, or by a timer writing its position, has no animation in
`getAnimations()` (a style write that starts a CSS transition does, and the page-load settle
stills that transition, as the late-script load's does when it is layout-inert), so the quiet
window is its only guard, and a script that paces its motion with pauses longer than the window
is still measured at its first pause. Nothing detects that case.

## The cross-engine comparison

Every rendering records a digest of every element under `<body>`: whether it is displayed, its box,
and its computed font size, keyed by its path from `<body>`. Each engine's digest is compared with
the reference engine's (the first `--engines` value) for the same page, size, mode and scripting.
Two noise classes are dropped, both measured in the Firefox pass:

- a font size differing by less than 0.1px, which is a user-agent font rounding invisibly;
- an element _neither_ engine displays.

Box shifts of half a pixel or less are dropped as rounding too. Horizontal and vertical differences
are reported apart. Each row names two elements: **Largest at**, the one carrying the row's largest
difference, and **First divergence**, the first element in page order where the engines differ, so
a cumulative vertical drift is traced to where it starts. They are usually different elements. The
comparison informs and never changes the exit code; a difference is a person's call.

Two engine behaviors show up in the table without being a layout difference. Both were measured
on a real site's first full run, where they made up the largest rows:

- **An SVG `<text>` element's box is wider in Firefox** by its glyph overhang, so every letter
  reports a horizontal difference of a few pixels. Where each glyph starts
  (`getStartPositionOfChar`) and how far it advances (`getComputedTextLength`) agree to a
  hundredth of a pixel: the letters are drawn in the same place.
- **A `<noscript>` with scripts off is an inline box around block content**, and each engine
  reports its own fragment boxes for that: Chromium one fragment the container's width, Firefox
  one per child at the child's width, WebKit one per child at the container's width. The wrapper
  can differ by hundreds of pixels while every child inside it matches.

## Measuring with an extension

A run can measure what a site's Content Security Policy does to the extensions its visitors
install (a dark-mode restyler, a content blocker, a password manager, a user-style manager), in
Chromium and Firefox. Each engine loads its own: `--chromium-extension` an unpacked folder,
`--firefox-extension` a signed `.xpi`. Give both and one pass covers both engines, so the
cross-engine comparison still runs. A run with one compared against a `--loopback-only` run
without it is the measurement; the extension is the only difference between them.

```bash
python browser_pass/browser_pass.py --targets "<targets file>" --serve "<built site>" \
  --engines chromium --loopback-only
python browser_pass/browser_pass.py --targets "<targets file>" --serve "<built site>" \
  --engines chromium --chromium-extension "<unpacked extension folder>"
```

Each of these is refused with exit `2` and one line naming why, before any browser opens: a
folder holding no `manifest.json`, or one declaring no background service worker (its load could
not be confirmed) or a manifest `key` that is not base64 text, bare or PEM-wrapped; an `.xpi`
that is not a zip archive, holds no manifest, or whose manifest names no gecko id
(`browser_specific_settings.gecko.id`, or the older `applications.gecko.id`); a manifest giving
no name or version; one asking for the `proxy` permission, in `permissions` or
`optional_permissions`, with which it could route requests around the egress guard below (in
Chromium an extension's proxy is handed a request's host unresolved, so the resolver rule never
sees it: measured, an extension setting a proxy on 127.0.0.1 had the browser's own requests sent
there by name); an engine `--engines` names without an extension, `webkit` above all, which
cannot load one; and an extension for an engine `--engines` leaves out. A name written as a
localized message (`__MSG_extName__`) is read from the default locale's messages.
A manifest or messages file larger than any manifest needs (`MANIFEST_LIMIT_BYTES` in
`src/pass_extension.py`) is refused as well, on the bytes read rather than on the size an `.xpi`
declares for it.

**Chromium needs its full build.** Playwright's headless shell, which every other run uses, has
no extension support at all. A run with an extension, or with `--loopback-only`, launches the full
Chromium build in its new headless mode (`channel="chromium"`) instead, so a baseline is measured
in the very build its extension run is. It is a second install beside the shell, into the same
folder:

```bash
python -m playwright install chromium
```

**Firefox loads only a Mozilla-signed `.xpi`, by profile sideload.** Playwright has no supported
API for a Firefox extension. The tool copies the `.xpi` into the profile as
`extensions/<gecko id>.xpi` and enables every sideload scope (`extensions.autoDisableScopes` 0,
`extensions.enabledScopes` and `extensions.startupScanScopes` 15). Playwright writes those prefs
into the profile's `user.js` before Firefox starts. An unsigned `.xpi` is removed from the folder
and never registered, even with `xpinstall.signatures.required` off, so no fixture in this
repository can stand for one: download the add-on's signed release. The load is confirmed in the
profile's `extensions.json` (the add-on listed with its id, `"location": "app-profile"` and
`"active": true`), and an add-on not active after the first launch gets one relaunch on the same
profile.

**Every context is a launch of its own.** An extension lives in a profile, so each context the
pass opens, a rendering's own and its late-script load's, is a persistent context in a fresh
profile under the run's scratch folder, launched with the extension and the rendering's viewport,
color scheme, motion, scripting and scale. Then, before the page is opened:

- the extension is confirmed loaded within `--timeout`: in Chromium, its manifest's service
  worker running at a `chrome-extension://` URL on the extension's own id, which Chromium derives
  from the manifest's `key` where it gives one (SHA-256 of the key's base64-decoded bytes), and
  otherwise from the folder's absolute path (SHA-256 of the path); either way the digest's first
  32 hex digits, written `a` to `p` for `0` to `f`. A published extension's unpacked copy usually
  carries its key, so it keeps its store id wherever it is unpacked. A key may be PEM-wrapped,
  as Chromium also loads it: the text between the `BEGIN` line and the `END` footer, its
  whitespace dropped. A `key` that is not base64 text either way is refused, exit `2`, since
  Chromium would not load it. In Firefox, `extensions.json` as above. One that is not is an
  instrument fault naming the engine and the extension, never a clean measurement:
  `chromium: the extension <name> <version> did not load within 30s`;
- a state's stored values are set with Playwright's `set_storage_state`, since a persistent
  context takes no `storage_state` at launch. Playwright writes each origin's `localStorage` from
  a page of its own whose requests it answers itself, so the values are in place before the
  page's first script runs and nothing is injected into the page measured;
- every tab already open is closed: the launch's own and any the extension opened on install, a
  welcome page say; and from then on each tab the extension opens is closed as it arrives. A page
  the pass opens arrives blank, before it navigates, and an extension's at its own URL.

Closing is not what keeps another tab out of the findings, since a tab can report before its
close lands. The page-level detectors (console and page errors, HTTP errors, failed requests)
listen on the pass's own page alone. The CSP collector is the one installed on the whole context,
since its listener has to run in each document before the document's own scripts, so every page
of the context reports to it; it keeps only the reports of the pages the pass opened itself
(`csp_watch`'s `admit`), and of those only the ones from a document on the origin the page's
main frame is at as it reports, wherever a redirect of the base URL landed it (this holds in
every run, extension or not). A frame's page is its top-level page, so an iframe an extension
injects into the measured page reports from that very page, under the extension's policy: its
document is on the extension's origin, and is dropped. So is a frame of the page's own on
another origin, an embed, which is held to its own origin's policy rather than the site's. An
`about:srcdoc` or `about:blank` frame and a `blob:` URL on the measured origin take their origin
and policy from the page, and are kept; a `data:` frame is not, though it inherits the page's
policy. The live fixture shows the tab case: once the first page it marks is loading, it opens a
page of its own whose inline script breaks the extension's own policy, and that violation is
never the measured page's finding.

Whatever fails in getting a context is that rendering's instrument fault, and the run goes on: a
launch, a confirmation, a close, a Playwright call erroring other than by timing out, or the disk
refusing a profile's files (`chromium: the context could not be prepared: <first line>`).

Each launch is slower than a context: a rendering takes two, and its watchdog bound grows by as
much. Closing a persistent context closes its browser, inside that bound.

**The egress guard.** `--loopback-only`, implied by an extension, fails the requests an engine
makes off the served origin, `127.0.0.1` at the port the pages are served on, the extension's
own included. Off 127.0.0.1 that holds in every engine; to another port on it, in Chromium and
Firefox, where a page's script could otherwise reach whatever else listens on the machine's
loopback, a development server or a local API. What each engine is launched with, and what that
guards:

- **Chromium** is launched with `--proxy-server=http://127.0.0.1:9` and
  `--proxy-bypass-list=<-loopback>;127.0.0.1:<served port>`, so every request but the served
  origin's, a WebSocket's included, goes to a proxy nothing listens on. `<-loopback>` removes
  Chromium's implicit loopback bypass, and comes first because Chromium reads the list last rule
  first. Also `--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1`, so no name resolves
  but `127.0.0.1`, an IP literal included;
  `--webrtc-ip-handling-policy=disable_non_proxied_udp`, so WebRTC sends no UDP, since an HTTP
  proxy carries none; and `--disable-quic`.
- **Firefox** sends every request but the served origin's to a manual proxy on `127.0.0.1:9`,
  loopback's included (`network.proxy.allow_hijacking_localhost` on, and
  `network.proxy.no_proxies_on` naming the served `127.0.0.1:<port>` alone), which no add-on may
  bypass (`network.proxy.allow_bypass` off). The served origin stays a secure context
  (`network.proxy.testing_localhost_is_secure_when_hijacked`), as loopback is. It has no WebRTC,
  WebTransport or HTTP/3 at all (`media.peerconnection.enabled`, `network.webtransport.enabled`
  and `network.http.http3.enable` off) and prefetches no name lookups
  (`network.dns.disablePrefetch`).
- **WebKit** sends its page's requests but loopback's to `http://127.0.0.1:9` through
  Playwright's `proxy` option, and that is all. Its bypass names the loopback host, so every
  port on it stays reachable: a page's script in WebKit can reach anything listening on the
  machine's loopback. Playwright gives WebKit no switch for its UDP either, so WebRTC in it is
  not guarded.

**WebKit is not held to the served port, and why.** Each of these was measured on macOS with
the pinned build, and refused. A bypass naming the port, `127.0.0.1:<port>`, is not read as a
port by WebKit's network stack: the served origin itself went to the proxy, and no page loaded.
No bypass at all did the same. Playwright's request and WebSocket routes on the context stopped
the page's own requests and WebSockets to another port, but not a WebSocket a worker opens, nor
the connection a `preconnect` link makes, so they were not added: a guard that holds most
channels would read as one that holds them all. Holding WebKit would need a forwarding proxy of
the tool's own in front of its every request, which it does not have.

Measured in the live suite, with nothing listening on port 9: an image off loopback fails as
`net::ERR_PROXY_CONNECTION_FAILED`, `NS_ERROR_PROXY_CONNECTION_REFUSED` and, in WebKit, in its
platform network stack's words, `Could not connect to the server.` on macOS and `Could not
connect to proxy server 127.0.0.1: Connection refused` on Linux, while loopback still loads. An
image, a fetch and a WebSocket to another loopback port reach nothing in Chromium and Firefox,
and reach it in WebKit. And a WebRTC connection to a STUN server on a loopback UDP port sends it
nothing in Chromium once its ICE candidates are gathered, finds no `RTCPeerConnection` in
Firefox, and reaches it in WebKit on Linux. That last is a Linux-measured claim: macOS WebKit
never finishes gathering within a rendering, so its count of no datagrams proves nothing, and the
suite says so in its output rather than passing it as open or closed. The WebTransport, HTTP/3,
QUIC and prefetch switches are set and pinned by the unit suite, not measured: nothing here
serves those protocols to try them on. The `--force-webrtc-ip-handling-policy` spelling older
Chromium documentation gives was measured to stop nothing in this build; the switch it reads is
`--webrtc-ip-handling-policy`. So an image, a font or a script a page takes from another host is
an `http_error` in every guarded rendering, and so is anything an extension fetches. In Chromium
and Firefox a request to another loopback port is one too, naming its URL and so its port, and a
refused WebSocket is a `console_error` naming its `ws://` URL. Compare an extension run with a
`--loopback-only` baseline, never with an unguarded run, so that only the extension differs. A
run without `--serve` whose `base_url` is not on 127.0.0.1, or names no valid port, is refused
under the guard. The full Chromium build asks for `/favicon.ico` as a browser does, so a site
with none logs a 404 console error in both runs alike.

**What the policy reports.** The CSP collector is installed on each persistent context before its
page, as on any context. An extension's insertion reaches it as the page's own would: Firefox
reports an inserted `<style>` or `<script>` as `style-src-elem` or `script-src-elem` blocked
`inline`, from a `moz-extension` source (measured with signed add-ons). In Chromium 153 the live
fixture's inserted `<style>` raised no violation under a policy that refused the page's own
inline style.

The report's facts record whether the guard was on (`loopback_only`), and each engine's extension
by its manifest's name and version, with the gecko id for Firefox (`extensions`).

## Exit codes

| Code | Meaning                                                                                                                                                                                                                                                                                                                          |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0`  | Every rendering measured, every detector silent (and, with `--prove`, every plant caught).                                                                                                                                                                                                                                       |
| `1`  | A detector found something, or missed a fault planted for it.                                                                                                                                                                                                                                                                    |
| `2`  | The run could not measure honestly: the targets file, a missing engine build or Playwright, a page the server does not hold, a meta-refresh page `redirects` does not declare or a declared one that cannot be followed, a state whose action did not complete, or any rendering refused by a guard above. A `2` outranks a `1`. |

## What it writes

A new folder per run, named for its start time in UTC, under `--out` (by default a
`browser_pass_runs` folder beside the targets file, which this repository's `.gitignore`
ignores):

- `report.md`, for reading: the verdict, the facts (commit, served tree, engine versions,
  whether `--require-csp` was on, whether off-loopback requests were refused and each engine's
  extension, and each redirect followed, as
  ``- Redirects followed: `/archive/` to `/archive/2026/` ``), a detector-by-engine table, each
  finding once with the renderings it recurred in, any instrument faults, the proof table with
  `--prove`, the late-script shifts summarized per engine (how many renderings were measured, how
  many moved above the threshold, the largest score and where), and the cross-engine differences
  grouped by page, engine and axis. When some rendering's settle stilled motion or left some
  running, a Motion section lists each such page: how many of its renderings, how many animations
  were finished, how many paused, and how many were still running when the settle ended, naming
  those renderings. The late-script load's settle has a part of its own below the page load's,
  counted apart. A rendering of a state is named with its state after its page,
  `chromium / [after-start] 390x844 light scripts-on`, and a state's cross-engine differences are
  a row of their own, `/ [after-start]`, beside its page's. The Motion section counts a state's
  renderings under its page.
- `report.json`, everything: every rendering's faults, findings and verdicts, its late-script
  score with every session window and what moved in it, the motion its settle finished, paused
  and left running (and, as `late_motion`, what the late-script load's settle did), and every
  cross-engine difference. Its facts record `require_csp`; `loopback_only`; `extensions`, one
  `{"engine", "name", "version", "gecko_id"}` object per engine with an extension (`gecko_id`
  null for Chromium); and `redirects`: one `{"page", "destination"}` object per redirect
  followed, empty when none was. Each rendering, and each cross-engine comparison, records its
  `state`: the state's name, or `null` for a page as it loads. A scripts-off rendering has no
  `csp_violation` key in its findings, as a clean one has none; `report.md`'s detector table is
  where `not measured` is stated.

Text a page chooses, such as an element's id, a console message, or a URL it failed to load or
its policy refused, reaches `report.md` written so that it cannot break the line or the table it
sits in. Each finding, and each element named in a table row, is a code span fenced by more
backticks than any run inside it; a line break in it reads as a space, and a pipe in a table cell
is escaped. An instrument fault's text has its inline markup escaped, as do an extension's name,
version and id, and the page in each rendering's name, which a followed redirect lets the page
choose. `report.json` keeps every such text exactly as it came.

The console names each page whose motion was stilled or left running on one line per load, the
late-script load's prefixed `late-script load:`, just before the verdict.

The commit, and whether the measured tree had uncommitted edits (`tree_dirty`), are read
before anything is served, through the shared [`commit_facts`](../commit_facts/commit_facts.md).
Both are null when git cannot say: when it is absent, the tree is outside any repository, or
git fails, which is also printed, and never read as a clean tree.

A built site is measured by naming its folder with `--serve`. One built outside any repository,
in a scratch folder say, records its commit and `tree_dirty` as null, and that is correct rather
than an error: no repository holds that folder. The commit recorded here describes the checkout
that holds the served folder, if any, not necessarily the commit the site was built from.

Never commit a report. It holds local URLs and the `--serve` folder as given, a local path, which
names your machine's layout when typed as an absolute one, and it is regenerated in minutes.

## Installing the engines

Two installs, each _pinned_, and each run once per machine, from the root of this repository.

1. **Playwright and its closure**, as the repository's `pyproject.toml` pins them in its
   `browser` extra. Install the extra rather than playwright alone: it pins `greenlet`, `pyee`
   and `typing-extensions` as well, and installing only playwright lets pip resolve those
   three freely.

   ```bash
   python -m pip install ".[browser]"
   ```

   A live test fails if the installed playwright and its pin ever disagree, and another fails
   if any entry in the extra stops being exact.

2. **The engine builds** that release pins. Each Playwright release fetches its own engine
   revisions, about 800 MB unzipped for all three:

   ```bash
   python -m playwright install --only-shell chromium firefox webkit
   ```

   `--only-shell` fetches Chromium's headless shell alone, which is all a headless run uses.
   A run with an extension or `--loopback-only` needs Chromium's full build as well: see
   "Measuring with an extension".

With `PLAYWRIGHT_BROWSERS_PATH` unset, Playwright puts the builds in its own folder in your
user cache (`~/Library/Caches/ms-playwright` on macOS, `~/.cache/ms-playwright` on Linux, and
`ms-playwright` under `%LOCALAPPDATA%` on Windows). `--browsers` defaults to
`$PLAYWRIGHT_BROWSERS_PATH` when that is set, and otherwise to that same folder, so after a
plain install there is nothing to set. To keep the builds elsewhere, set the variable for both
the install and the run, or pass `--browsers` to the run.

## Privacy

Every engine runs with a throwaway profile, so no browser's own profile store is read. Firefox
needs one step more. It reads its per-user data folder at startup even when handed a profile,
and on macOS that folder is the user's real Firefox profile store. So the tool launches Firefox
with `MOZ_APP_DATA` and `MOZ_LOCAL_APP_DATA` pointed into the run's scratch folder. Where that
real folder cannot be read, as under a sandbox that denies it, Firefox would otherwise fail to
start with "Could not find profile folder".

A run with an extension is no exception: each persistent context's profile is a fresh folder made
under the run's scratch folder, removed once its context closes, and never a browser's own profile.
The extension is read from where the option names and copied into that profile (an `.xpi`) or loaded
in place (an unpacked folder), never installed into a real browser. Under the egress guard, no
request an extension or a page makes off the served origin leaves the browser in Chromium or
Firefox, by any channel listed under "The egress guard" above, another port on loopback included; in
WebKit its page's requests to another host do not, while a request to another loopback port and its
WebRTC's UDP still can.

## Tests

From the root of this repository, after the installs in the [root README](../README.md):

```bash
python browser_pass/test_browser_pass.py
```

`test_browser_pass.py` runs the grouped suites under `tests/`, every one in one process, and
holds each grouped file to a floor of tests, so a suite that quietly loses tests fails. It also
declares `SPLIT_SUITE_BY_FILE`, through which a runner that splits a suite may run each grouped
file as a task of its own (`suite_select`).

**The live suites need the engine builds.** They launch the real Chromium, Firefox and WebKit
builds against `samples/site` or pages of their own, served on loopback. Where a build they need
is not installed, each live module skips, with one line naming where it looked and the command
that installs what is missing; everything else still runs. A skip is a passing run, so a run that
must measure the engines sets `PAGE_PROOFS_REQUIRE_BROWSERS=1`, and a missing build then fails
the module instead:

```bash
PAGE_PROOFS_REQUIRE_BROWSERS=1 python browser_pass/test_browser_pass.py
```

The extension suite needs Chromium's full build as well, and its Firefox test runs only when
`BROWSER_PASS_SIGNED_XPI` names a signed `.xpi`, since no fixture can be signed.

The suites, by what they cover:

- **Pure suites** cover the targets file, the verdicts (the late-shift scoring among them), the
  digest, the motion record and its tally, the report and the CLI. The CLI is driven through a
  fake engine that returns scripted results (`tests/_cli_fixtures.py`), and the settle's
  stilling through a page that answers the probe with scripted records. `test_pass_probe.py`
  pins the probe's source as the driver composes it from its three files. `test_pass_inert.py`
  drives the late-script load's settle through a scripted DevTools session
  (`tests/_inert_fixtures.py`).
- **States**: `test_pass_targets_states.py` covers the `[[state]]` tables and each refusal;
  `test_pass_states.py` the step runner over a fake page; `test_pass_engine_states.py` the
  engine's order, through scripted probes. `test_pass_states_live.py` reaches a state in all
  three engines on `samples/site/popover.html`.
- **The CSP detector**: `test_pass_csp.py` drives the wiring through a browser stand-in
  (`tests/_csp_fixtures.py`), and `test_pass_csp_live.py` shows the same in all three engines on
  `samples/site/csp.html`.
- **An extension and the egress guard**: `test_pass_extension.py` reads extensions written fresh
  into a scratch folder; `test_pass_extension_guard.py` pins each engine's guard, argument by
  argument and pref by pref, for the served origin's port; `test_pass_extension_launch.py` and
  `test_pass_extension_tabs.py` drive a persistent launch through a stand-in browser type
  (`tests/_launch_fakes.py`);
  `test_pass_engine_isolation.py` covers the seam, and `test_pass_cli_extension.py` the CLI.
  `test_pass_extension_live.py` loads the committed MV3 fixture (`tests/fixture_extension/`)
  into the full Chromium build, and `test_pass_loopback_live.py` measures the same page under
  `--loopback-only` alone in Firefox and WebKit. Each asks another loopback port than the served
  one for an image, a fetch and a WebSocket: Chromium and Firefox reach nothing there and name
  each refusal, and WebKit reaches it, as the guard's table says. WebKit's wording and its UDP
  check are chosen per platform by `tests/_guard_fixtures.py`, which `test_guard_expectations.py`
  tests without an engine.
- **A page with no file**: `test_pass_not_found.py`, `test_pass_cli_not_found.py` and the live
  `test_pass_not_found_live.py`.
- **A page that is a meta refresh**: `test_pass_redirects.py`, `test_pass_cli_redirects.py` and
  the live `test_pass_redirects_live.py`, which runs the whole CLI in real Chromium over a
  scratch site whose `/archive/` is a year-archive stub.
- **Page text in the report**: `test_pass_markdown.py` hands the report hostile ids, messages
  and URLs in each place `report.md` shows them, with a property test that a code span stays
  one line, closes only at its own fence and reads back as the text.
- **The live boundary**: `test_pass_live.py` proves every detector catches its plant in all
  three engines, reads landscape back, triggers each refusal for real, and measures the sample's
  paced and endless motion at rest. `test_pass_late_live.py` drives the late-script load,
  `test_pass_late_motion_live.py` its stilling of endless motion, and
  `test_pass_shadow_motion_live.py` and `test_pass_shadow_boxes_live.py` both settles inside
  shadow roots.
- **What a real engine will not do on demand**: `test_pass_late_load.py` and
  `test_pass_late_faults.py` drive the late-script load's refusals through a scripted page
  (`tests/_late_fixtures.py`), and `test_pass_engine_launch.py` every launch path through a
  stand-in for the started Playwright, so a run where the live suites skip still reaches them.

The live suites share `tests/_live_fixtures.py`, and `tests/test_live_fixtures.py` checks the
skip and the opt-in failure on any machine. They share browsers the way the tool does:
`SharedEngines` keeps one browser per engine for a module or a class and gives each test a fresh
context, inside the same watchdog bounds `run_matrix` uses. Each module declares a launch budget,
`_LAUNCH_BUDGET`, and `LiveSite` fails the module past it, so a new test that quietly launches
its own browser shows up as a failure rather than as a slower run. They settle over a shorter
window than the tool does (`TEST_SETTLE_QUIET_SECONDS`), since the pages they serve stop moving
at once.

### The in-page probe's own tests

The in-page probe is JavaScript in three files: `src/page_probe.js`, written as a function of the
other two; `src/page_probe_motion.js`, the stilling both settles run; and
`src/page_probe_trees.js`, the one walk over the page's elements, each open shadow root's
included, that every read makes. `src/pass_probe.py` applies the first to the other two, so an
engine receives one function, which the late-script load's DevTools session calls as well. The
repository's `npm test` runs `tests/test_page_probe_*.mjs` under `node --test` in jsdom, and
holds the probe's files to 100% lines, branches and functions:

```bash
npm ci --ignore-scripts
npm test
```

`test_page_probe_layout.mjs` covers the four layout detectors, the fault plants and the reads
the driver polls; `test_page_probe_late.mjs` the late-script load's log, hold, plant and record;
`test_page_probe_motion.mjs`, `test_page_probe_motion_rules.mjs` and
`test_page_probe_motion_shadow.mjs` which animations each settle stills, what it does to each,
and where it finds them, over stand-in animations, since jsdom has no Web Animations API; and
`test_page_probe_layout_shadow.mjs` the page-load reads over shadow trees.
`page_probe_test_helpers.mjs` loads the probe's three files into jsdom as their own source text
and applies the first to the other two, as the driver does. jsdom does no layout, so each test
states the boxes it assumes, while the styles the detectors decide on come from jsdom's own
cascade. These suites pin the probe's decisions over those numbers; whether an engine reports
the numbers they assume is the live suites' question.

`samples/` is the hand-verification sample: six small pages, two small scripts, a targets file
holding one state and a second for the policy page, with `samples/README.md` saying what each
rendering should report.
