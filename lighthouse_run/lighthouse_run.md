# lighthouse_run

## Why it exists

Lighthouse scores move between identical runs, so one run proves little, and two runs compare
only when they used the same Lighthouse, the same flags, the same order and the same arithmetic.
Written down as a recipe, those drift. This tool holds them fixed, so a later run repeats a
baseline instead of merely resembling it.

## Usage

From the root of this repository, install the pinned Lighthouse into `node_modules/.bin`, then
run the tool against the folder of pages to measure. Keep the machine otherwise idle: a build
running alongside is measured too. Any Python 3.12 or later runs it, since it needs nothing
beyond the standard library; the commands here write `python`, which is the venv's
interpreter once the README's "Install" has run.

```bash
npm ci --ignore-scripts
python lighthouse_run/lighthouse_run.py --root . --serve "<built site>"
```

`--root` supplies the pinned Lighthouse, and `--serve` names the folder of pages to serve on
loopback and measure. From this repository `--serve` is required, because it has no `src/`
folder of pages at its root, and a run without `--serve` is refused. In a web project
that keeps its pages in `src/` and has run `npm ci` itself, `--root` alone is enough:

```bash
python lighthouse_run/lighthouse_run.py --root "<web project>"
```

By default that measures the home page on the mobile and desktop presets, three rounds each,
for 6 Lighthouse runs. A wider run names its pages:

```bash
python lighthouse_run/lighthouse_run.py --root . --serve "<built site>" \
  --pages / /archive/ /404.html --runs 5
```

A folder outside any repository, such as a build in a scratch folder, records its commit as
unknown (`commit` and `src_dirty` both null). That is correct, not an error: git has nothing to
say about a folder no repository holds.

### Measuring a hosted copy

To measure a site as a real host serves it, give the copy's base url with `--url`, and with
`--expect` the folder holding the bytes the host must serve (the build deployed there). Nothing
is served on loopback, and `--root` still supplies the pinned Lighthouse:

```bash
python lighthouse_run/lighthouse_run.py --root . --url "https://<host>" \
  --expect "<built site>" --out "<private folder>"
```

Every flag, default, the interleaving and the arithmetic are the loopback run's, so the two
compare on everything but the network. Before any run, each page is fetched once, uncompressed,
and confirmed byte for byte against `--expect`. A host still answering with an older deploy
(a caching host can, for a while) is refused rather than measured; run again once its cache has
turned over. A redirect is refused too, wherever it points, since every run would otherwise
measure through a hop no summary records: give the address the host actually serves, such as
`https://` for a host that forces it. When the address must stay private, see Privacy below.

## Options

The tool's own help lists every option with its live default.

| Option               | Default                                | Meaning                                                                                                                                                       |
| -------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--root PATH`        | required                               | Web project root holding the installed `node_modules`, and the `src/` measured unless `--serve` or `--expect` names another folder.                           |
| `--serve PATH`       | `src/` under `--root`                  | Folder to serve on loopback and measure, such as a built site.                                                                                                |
| `--url URL`          | none (serve on loopback)               | Base url of a hosted copy to measure, `http` or `https`; the tool's own help lists the forms it refuses. Needs `--expect`; excludes `--serve`.                |
| `--expect PATH`      | none                                   | With `--url` only: the folder holding the bytes the host must serve. Every page is confirmed against it.                                                      |
| `--pages PATH ...`   | `/`                                    | Server paths to measure, each starting with `/`, in printable ASCII without spaces (rename a file whose name is not). A page named twice is measured once.    |
| `--presets NAME ...` | `mobile` `desktop`                     | Lighthouse presets. Mobile is Lighthouse's default (simulated throttling: 150ms round trip, about 1.6Mbps, 4x CPU slowdown); desktop adds `--preset=desktop`. |
| `--runs N`           | `3`                                    | Rounds per page and preset. Fewer than 3 is refused: a score can swing widely between identical runs, so a single run is not evidence.                        |
| `--out PATH`         | a `lighthouse_runs` folder in `--root` | Folder receiving one new timestamped results folder per invocation. The default is ignored by this repository's `.gitignore`.                                 |
| `--timeout SECONDS`  | `300.0`                                | Bound on one Lighthouse run. A hung run is killed with its whole process tree and counted as failed.                                                          |

## What one invocation does

1. **Refuses what it could not measure honestly** (exit 2): a missing folder to measure, no
   pinned Lighthouse, no Chrome that Lighthouse would launch, or a page not answered with its
   file. A malformed `--url`, or target options that name no single tree, are refused at parse
   time.
2. **Reads the commit** of the tree it will measure, before anything is served, so the tree
   measured is the tree at the start. A git that fails or hangs costs only its own time bound.
3. **Serves that tree on 127.0.0.1**, on a port the operating system picks, and fetches every
   page once to confirm its bytes match the file. So it never measures a stale server left on a
   fixed port, and is never visible to the local network. With `--url`, nothing is served: each
   page the host answers is confirmed the same way, and any mismatch, redirect, error, dropped
   connection or timeout refuses the run, naming the page.
4. **Interleaves the runs**: each round measures every page on every preset once, so a slow
   moment on the machine lands across all of them rather than on one page.
5. **Records** every raw report and a summary, then prints the medians and the audits behind
   them as Markdown tables, ready to paste into a record.

## What it records

Each invocation writes one folder named by its UTC start time (`YYYYMMDDTHHMMSSZ`) holding:

- a `reports` folder with every raw Lighthouse report, named `<page>-<preset>-run<round>.json`
  (the home page's name is `home`);
- `summary.json`, with the `commit` measured and `src_dirty` (whether that tree had
  uncommitted edits), both null when git cannot say; `served`, the folder measured as given
  (null for the default `src/`); `url`, the base url of a hosted run with its scheme and host
  lowercased and no trailing slash (null on loopback); the `lighthouse_versions` read from the
  reports, the full `chrome_version`, the `flags` each preset ran with, each run's numbers and
  `audits` or its error, and the `medians` and `audit_medians`.

| Metric                 | Read from                                                                                     |
| ---------------------- | --------------------------------------------------------------------------------------------- |
| perf                   | `categories.performance.score`, shown as 0-100                                                |
| FCP ms, LCP ms, TBT ms | the audits' `numericValue`; on the mobile preset these are Lighthouse's _simulated_ values    |
| CLS                    | the `cumulative-layout-shift` audit                                                           |
| bytes                  | the `total-byte-weight` audit: every request, including any the page fetches from other hosts |
| requests               | the rows of the `network-requests` audit                                                      |
| font bytes by FCP      | the join below                                                                                |

**Font bytes by first paint is a join, not a Lighthouse field.** A font request counts when its
bytes had finished by first contentful paint, comparing each request's end time with the paint's
timestamp. Both clocks are the _observed_ ones on purpose: the simulated FCP models a slower
network, and no request finished at a simulated time.

**Chrome is located before any run** the way Lighthouse locates it, then asked for `--version`,
and that path is handed to every run. So the recorded version is the browser actually used. The
report's user agent cannot stand in for it, because Chrome reduces that to the major version.

Medians are taken per metric, not as the median run: the run with the median score need not
hold the median CLS. The audits each run records, and how they are reduced, are described in
`src/lighthouse_audits.py` and `src/audit_summary.py`.

## Privacy

- Lighthouse launches Chrome with its own fresh temporary profile, so no personal browser
  profile is read.
- Error reporting to Lighthouse's maintainers is switched off explicitly on every run.
- The server binds loopback only. A hosted run starts no server, fetches nothing but the pages
  from the host it names, and never follows a redirect.
- The raw reports embed the urls measured and the user agent, and `summary.json` records the
  folder measured as it was typed, which can name the machine's layout. That is why the default
  results folder is ignored by this repository's `.gitignore`. Copy the numbers worth keeping
  into a record by hand; do not commit a report or a summary.
- A hosted run's reports and `summary.json` embed the measured address. For an address that must
  stay private, pass `--out` a folder outside the repository, and outside anything backed up off
  the machine.
- The LCP element is recorded by its CSS selector only, never by the text Lighthouse records
  beside it.
- A run loads the pages as a visitor would, so anything a page fetches from another host is
  fetched during it.

## Reading the numbers

**Local timings are noise.** A loopback server has no network to speak of, so the score and
FCP, LCP and TBT move between identical runs, and a real host will move them again. CLS, bytes,
request count and font bytes by FCP are the numbers that carry between runs and machines.

**A hosted run compares with hosted runs.** Its timings include the real network, and its byte
counts are what crossed the wire, so a host that compresses reports fewer bytes than a loopback
run of the same build. Compare a hosted run's `bytes` only with another hosted run's.

Before comparing two summaries, check that `lighthouse_versions` match (the pin in
`package.json` guarantees this within one clone) and note any change in `chrome_version`, which
updates on its own.

## Exit codes

| Code | Meaning                                                                                                                                                                      |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0    | Every run measured.                                                                                                                                                          |
| 1    | At least one run failed. The summary is still written, with medians over the runs that measured, and each failure printed.                                                   |
| 2    | The measurement could not start: a refused option, nothing to measure, no pinned Lighthouse, no Chrome, a page not confirmed, or a results folder that could not be created. |

## Tests

From the root of this repository, after the install in the [root README](../README.md):

```bash
python lighthouse_run/test_lighthouse_run.py
```

The tests never touch the host's Chrome or Lighthouse. Lighthouse, `node`, Chrome and git are
replaced by real processes (Python scripts run by the suite's own interpreter), so the
subprocess boundary is crossed for real while everything past it is scripted. Two tests run the
real `node`, when one is installed, against a fake chrome-launcher package, to prove the script
that locates Chrome resolves through Lighthouse's install. The audit tests read real
Lighthouse 13.4.1 reports under `tests/fixtures/`, cut down to the audits read and sanitized:
a fixed loopback port, neutral demo names for every asset, third-party urls under
`example.invalid`, and no field carrying page text.
