# browser_pass sample

Six small pages, two small scripts and two targets files, to check the tool by hand. From the
repository root:

```bash
python3.12 browser_pass/browser_pass.py --targets browser_pass/samples/targets.toml \
  --serve browser_pass/samples/site --prove
```

That is 5 pages x 2 orientations of one phone (390x844 and 844x390) x 2 modes x 2 scripting settings
x 3 engines, 120 renderings, and the one state in the targets file x 2 orientations x 2 modes x 3
engines with scripts on only, 12 more: 132 renderings. The run exits `1`, because the faulty page is
faulty, and writes its report into a gitignored `browser_pass_runs` folder beside the sample's
targets file.

## What each page should report

`site/clean.html` reports **nothing** in any rendering. It holds what a careless detector would
flag:

- a link inside a sentence, exempt from the target size;
- a correctly hidden paragraph;
- a hidden paragraph held in the layout as `visibility: hidden`, the way a page reserves space
  for something a script reveals later (`site/reveal.js` does reveal it, after the page has
  painted, without moving anything);
- a shelf that scrolls sideways, whose off-screen items sit inside a scroller.

`site/faulty.html` holds one fault per detector, but for the CSP detector's, which needs a page
declaring a policy of its own (see below):

| Detector          | The fault                                                                      | Reported                                                                                        |
| ----------------- | ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `page_overflow`   | a 600px-wide block                                                             | portrait only: 844px of landscape holds it                                                      |
| `past_right_edge` | the same block, and a fixed badge hung 40px past the right edge                | the block in portrait, the badge in both                                                        |
| `hidden_rendered` | a `hidden` paragraph a class rule shows                                        | always                                                                                          |
| `small_target`    | a 16x16 button                                                                 | always                                                                                          |
| `console_error`   | the page's script logs one; its plain `console.log` line is not one            | scripts on; Chromium and WebKit also log the 404 below as a console error, and Firefox does not |
| `page_error`      | the page's script throws                                                       | scripts on only                                                                                 |
| `http_error`      | an image that does not exist                                                   | always                                                                                          |
| `csp_violation`   | `site/csp.html`: a policy, and one style attribute it refuses                  | scripts on; `not measured` with scripts off, where `console_error` still reports the refusal    |
| `late_shift`      | `site/late.js`, a module that adds a banner above everything already on screen | Chromium with scripts on; `not measured` in Firefox and WebKit, `n/a` with scripts off          |

`site/blocked.html` loads the **same** `late.js` with `blocking="render"`, so the banner is in
place before anything is painted: it reports nothing. That is the fix the faulty page's late
shift calls for, and the late-script load releases the held script at once rather than waiting
for a paint that cannot come.

`site/motion.html` moves, but never while a stillness window is watching, which is the case the
settle's stilling of motion exists for. It holds three boxes:

- one that holds still for fifteen seconds, far past any settle window, then slides 120px down
  and stays there;
- one that bobs forever once its own fifteen-second delay is over, starting and ending each
  bob at its rest pose;
- one the page pauses itself with `animation-play-state: paused`, showing its first frame.

A settle that only waited for stillness would measure this page in its first pause, with the
slide not yet moved. Every rendering reports **nothing** from any detector, and measures the
slide at the END of its move, the bob at its iteration start and the paused box untouched, at
the frame the page shows. The report's Motion section lists the page with one animation
finished and one paused in each of its 24 renderings, and none still running. Its late-script
load part lists the page again, apart, with one finished and one paused in each of its 4
Chromium renderings with scripts on: that load stills only motion that moves no layout, and both
moving boxes move only their `transform`. The console names the page on two lines before the
verdict, one per load.

`site/popover.html` opens a panel from its button with no script, through the browser's own
popover, and the panel holds a 16x16 button. Loaded, the page reports **nothing**: the panel is
closed, so the button is not rendered. The targets file's state `opened` reaches the same page
(its address carrying a query, which the server answers with the same file), clicks the button
and waits for the panel to show, and only then measures: each of its 12 renderings reports one
`small_target`, `body>div:1#panel>button:1 16x16`, named in the report as
`/popover.html?from=state [opened]`. Its late shift is `n/a`: the late-script load measures a
page's load, and a state is reached after it.

With `--prove`, every plant should be `caught` in every rendering, except that the page-error plant
is `n/a` with scripts off, because no script runs there to throw it, the CSP plant is `n/a` with
scripts off, because no listener runs there to report it, and the late-shift plant is `n/a` with
scripts off and in a state, and otherwise `not measured` outside Chromium. The proof table in
`report.md` shows that at a glance.

The cross-engine section shows small differences (text wraps differently between engines), and
that section never changes the exit code.

## The policy page

`site/csp.html` declares `default-src 'self'` and carries one style attribute, which that policy
refuses. It has a targets file of its own, beside the clean page, because it is run with
`--require-csp`, which would make a finding of every page in the main targets file: none of them
declares a policy. Its policy refuses none of the probe's plants, since each is drawn through the
CSSOM, which a policy does not govern, so it is run with `--prove` as well:

```bash
python3.12 browser_pass/browser_pass.py \
  --targets browser_pass/samples/csp_targets.toml \
  --serve browser_pass/samples/site --require-csp --prove
```

That is 2 pages x 2 orientations x 2 modes x 2 scripting settings x 3 engines, 48 renderings, and
the run exits `1`:

- `site/csp.html` with scripts on reports `csp_violation` once, as
  `style-src-attr blocked inline on /csp.html`. Chromium's late-script load reports it too, and the
  two loads merge into that one finding. It also reports the same refusal as a `console_error`, in
  each engine's own words: one refusal, two detectors.
- `site/csp.html` with scripts off reports the `console_error` alone. No listener runs to report
  the violation itself, which is why the detector is `not measured` there rather than clean.
- `site/clean.html` reports `csp_violation`, `page carries no Content-Security-Policy meta`, in
  every rendering, scripts on and off, and nothing else: it declares no policy, and
  `--require-csp` makes that a finding. The policy page declares one, so it never reports it.
- With `--prove`, every plant is `caught` on both pages, policy or none, with the same `n/a` and
  `not measured` plants as the main sample above.
