# print_pass sample

One page, `site/index.html`, printed in four states by `targets.toml`, to check the tool by hand
against a report committed beside them, `expected_report.json`. The page has no print styles of
its own: it tests the instrument, not a site. It holds:

- two short paragraphs, each with one link: the first back to the page itself by a
  same-origin path, so its address in the PDF names the loopback port the page was served on,
  and the second to `https://example.com/`. A PDF carries each as a link annotation, and
  nothing ever fetches either;
- an inline script that restores a `mode` from `localStorage` into `<html data-mode>`, as a
  site's theme toggle does;
- a dark scheme for a reader whose color scheme is dark;
- an element spinning forever (`animation: spin 4s linear infinite`). It paints nothing, so
  the moment a print catches it at cannot change a pixel;
- a 1400-pixel spacer, then a lazy image (`loading="lazy"`), below the fold of the 1280x800
  window each state is laid out in.

The image is `pixel.png`, a 64 x 32 gray PNG that is not committed: no binary is. It is
written beside the page in a copy of the site, which is what gets served. The live suite writes
the same bytes, and checks that the command below does too.

## Running it

From the repository root, with `$P` the `python` of the virtual environment the README's
"Install" makes, with the repository's `print` extra installed, and poppler's readers on
`PATH`:

```bash
SITE="$(mktemp -d)/site"
OUT="$(mktemp -d)/out"
cp -R print_pass/samples/site "$SITE"
"$P" -c 'import struct, sys, zlib; chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data)); sys.stdout.buffer.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 32, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x80" * 64) * 32)) + chunk(b"IEND", b""))' > "$SITE/pixel.png"
"$P" print_pass/print_pass.py --mode make --serve "$SITE" \
  --targets print_pass/samples/targets.toml --out "$OUT"
```

`--out` must be a new folder outside every git work tree, which is why it is a temp folder.
The run prints its summary and the path of its report, `$OUT/report.json`.

## What the report shows

The run exits `0`: every state printed and every PDF read, with no finding. It prints each
state twice from one load, backgrounds off and on, so there are 8 PDFs, and 5 warnings:

| State           | What it sets                            | What the report shows                                                                                                      |
| --------------- | --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `default`       | nothing                                 | reads `data-mode` back as `light`; warns of the running `spin` animation                                                   |
| `stored-dark`   | `mode = "dark"` in `localStorage`       | reads `data-mode` back as `dark`, and the stored `mode` as `dark`; prints as `default` does, since nothing styles the mode |
| `emulated-dark` | Playwright's emulated dark color scheme | reads the scheme back as `dark`, and warns that it is EMULATED: with backgrounds on it prints dark, unlike any real print  |
| `reduced`       | the reduced-motion preference           | reads it back as `reduce`; the animation still runs, since the page does not ask for it                                    |

Every state reads the lazy image back complete, at its natural width of 64 pixels. Each PDF is
two Letter pages, because the spacer is taller than the 980 CSS pixels a page holds inside its
1 cm margins: the text and both links on the first, the image on the second, at 96 ppi, since
the page draws the 64-pixel image 64 CSS pixels wide. Every PDF is tagged, its dates are UTC,
and no privacy flag is raised. Backgrounds off and on print identically in every state but
`emulated-dark`.

## Running it in Firefox

The same make run, with `--engine firefox` added and a new `--out`, prints the sample through
Firefox's own print path. It runs on macOS only, and needs geckodriver and Firefox itself
installed, as `../print_pass.md` says in "The Firefox engine"; it does not need Playwright.

```bash
"$P" print_pass/print_pass.py --mode make --engine firefox --serve "$SITE" \
  --targets print_pass/samples/targets.toml --out "$(mktemp -d)/firefox"
```

It exits `0` too, with no finding, and its only warnings are for the running `spin` animation,
one in every state. Every state reads the lazy image back complete, since each session widens
Firefox's lazy-load margin. Backgrounds off and on print identically in every state,
`emulated-dark` included, where the default engine's two differ (why they do not differ under
Firefox was not measured). Its dark scheme is Firefox's own preference, so the report marks it
not emulated and gives no EMULATED warning, though the page reads it back as `dark`.

What each PDF holds is the Firefox build's to decide, since the tool pins no Firefox. Measured
with Firefox 159 on macOS: each PDF is two Letter pages, tagged, with the page's title as its
`Title`, and its one font, `Times-Roman`, is embedded with a ToUnicode map. Each of the two
links is listed twice, at the same place on the page. The image's ink is on the second page,
but `pdfimages` finds no image object in the PDF, so the report's `images` list is empty.
`expected_report.json` is the default engine's report, not Firefox's.

## The expected report

`expected_report.json` is the run's `report.json` normalized by `normalize_report`, in
`src/print_report_output.py`. That function, and the tuples of keys beside it, are the one
statement of what the normalization takes out: what varies from run to run, from machine to
machine, with the fonts installed, or with the engine's build. One of those is the port in the
first link's address, which reads `127.0.0.1:<port>`. The file is written with sorted keys, a
two-space indent and one closing newline, and holds no path outside the out folder, port,
date, hostname or login.

What stays still comes from the pinned engine laying the page out in the host's default serif:
the page count, for one, and whether two prints are identical. A Playwright pin bump, or a
change to the sample or to what the normalization drops, regenerates it. To write it from the
out folder `$OUT` of the run above, from the repository root:

```bash
"$P" -c 'import json, sys; sys.path.insert(0, "print_pass/src"); from print_report_output import normalize_report; report = json.load(open(sys.argv[1], encoding="utf-8")); sys.stdout.buffer.write((json.dumps(normalize_report(report), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))' "$OUT/report.json" > print_pass/samples/expected_report.json
```

The program writes bytes, so the file is the same on every platform: no newline is translated
and no locale picks the encoding. The live suite runs this same command on its own run's
report and compares the output with the committed file, character for character.

## Why no PDF is committed

A PDF records when it was made, in its CreationDate and ModDate, so a committed PDF would
differ from every new run and date the repository. For the same reason the tool refuses an
`--out` inside any git work tree by construction, whatever that tree's ignore rules say; the
report keeps only each date's class (`utc`), never its value.
