# loopback_site

Serve one directory on `127.0.0.1` and confirm which tree answered.

## Why it exists

`python3 -m http.server` listens on every interface unless it is given `--bind`, so a "local"
review server publishes the working tree to the local network. A fixed port fails a second way:
when another server already holds it, the new server cannot start, and a browser pointed at that
port silently shows the other server's tree.

This server closes both. It binds loopback only, by default on a port the operating system
picks, and it fetches every page it is asked about and compares the bytes with the file on disk
before it reports a url -- so the url it prints is evidence of which tree answers, not an
assumption. A caller that needs a fixed port names one with `--port`; one already held is refused
with exit `2`, never shared.

## The `Host` check

A loopback bind keeps other machines off the port. It does not keep off a public page open in the
viewer's own browser, which can aim a request at `127.0.0.1` and, through DNS rebinding, make that
request look same-origin. So the server answers only a `Host` of `127.0.0.1:<port>` or
`localhost:<port>`. Anything else -- another name, a loopback name without the port, no `Host` at
all -- gets `403` before a file is opened, and no response carries a CORS header.

## The listen queue

`ThreadingHTTPServer` threads the handling of a request, not the accepting of a connection: one
loop accepts them all, and whatever arrives faster waits in the socket's listen queue. The server
inherits `socketserver`'s queue of 5, and one browser load of a site page -- its stylesheets and
module graph -- opens more connections than that in parallel. On macOS a connection that finds the
queue full is reset rather than retried, so the browser reports `ERR_CONNECTION_RESET` on a random
subresource: a failed module import aborts the whole module graph, and a failed stylesheet leaves
the page part-styled. That shows a reviewer a broken page the change under review did not cause.

So the server asks for a far deeper queue, `_REQUEST_QUEUE_DEPTH` in the source, and the kernel
caps the request at its own maximum.

## Stopping

`shutdown()` does not interrupt the serving loop. It sets a flag and blocks until the loop's
current `select` times out, so the loop's poll interval is what every exit from `serve_directory`
costs. `socketserver`'s own is 0.5 s, and an exit that follows a request waits nearly all of it,
because the loop starts a fresh `select` after each request it answers. A caller serving one tree
per test case paid that on every case. The loop is handed `_SHUTDOWN_POLL_SECONDS` instead, which
takes an exit to a few hundredths of a second for a handful of idle wake-ups.

## Client hang-ups

A browser that closes a page, or abandons a connection it opened, resets that connection
mid-request. The handler's next write then raises `BrokenPipeError` or `ConnectionResetError`,
depending on timing -- both seen on macOS from the same send -- and its next read raises
`ConnectionResetError`. `socketserver`'s default
`handle_error` prints a full traceback for either. So a live test whose browser closed pages
mid-request passed with tracebacks on stderr, and a real failure in the same run read as one
more of them.

The server overrides `handle_error` to drop exactly those two, matched on the exception being
handled, and hands every other failure to the default report. The match is the two types, not
their `ConnectionError` base, so `ConnectionAbortedError` still reports: on POSIX it means this
end's own stack aborted the connection, not that the client hung up.

## A request path no file can have

`open()` refuses two kinds of name with `ValueError` rather than the `OSError` the standard
library answers `404` for: one holding NUL, which `%00` decodes to, and one the filesystem
encoding cannot encode -- a lone surrogate such as `%ED%A0%80`, which the standard library's path
translation decodes with `surrogatepass`. Left to the standard library, the handler died
mid-request: a traceback on stderr and a connection reset at the client, which reads like a crash
rather than a miss. The handler answers such a path `404` before any file is opened -- after the
`Host` check, and with the `--not-found` file when there is one -- because no file can have that
name, so it is a miss like any other. Every other name the OS cannot open fails with `OSError` and
gets the same `404` from the standard library.

## A site's own 404 page (`--not-found`)

GitHub Pages answers a path with no file using a site's own `404.html`, with status `404`. A
site whose 404 page does something -- reacts to the address it was reached at, say -- can only be
reviewed or measured locally if the local server answers a miss the same way. Name that file with
`--not-found PATH` (library: `not_found=`), a server path such as `/404.html`:

- A request with no file is answered `404`, `Content-Type: text/html; charset=utf-8`, and the
  file's exact bytes. That covers a missing file, a missing directory, and a file asked for with a
  trailing slash: every case where the standard library would have sent its own 404.
- A directory with no `index.html` gets the same answer instead of a listing, because Pages lists
  no directory.
- A directory asked for without its trailing slash still gets the `301` that adds it, an existing
  file is served exactly as before, and a foreign `Host` is still refused with `403` first.
- The file is read on every miss, like every other file, so an edit shows on the next load. If it
  disappears while serving, a miss falls back to Python's own error page, still `404`.
- It must name a file inside the tree, or the server refuses to start. Its `..` are resolved
  lexically, as the request handler resolves a request path's, and the containment check runs on
  that one path, which is also the path served and confirmed against. So the file it names is the
  file served, and it sits inside the tree: a `..` that climbs out is refused, `/x/../404.html`
  is `/404.html` whether or not an `x/` exists, and a `..` after a symlinked directory never
  climbs from the link's target.

It is **opt-in**, and the default must not change: without it a miss is Python's own error page,
which runs no script. A caller may rely on that: one that loads a reserved missing path and
refuses to continue unless the page it lands on holds no script needs exactly that page, which a
site's own 404 page need not satisfy. A test pins the default against the standard library's own
answer.

## Usage

```text
python loopback_site.py --directory PATH [--pages PATH ...] [--not-found PATH] [--port PORT]
```

| Argument | Required | Default | Description |
| --- | --- | --- | --- |
| `--directory PATH` | yes | — | The directory to serve. |
| `--pages PATH ...` | no | `['/']` | Server paths confirmed against disk before the url is printed; each must start with `/` and hold only printable ASCII without spaces. A page whose file is named otherwise cannot be confirmed, percent-encoded or not: rename the file. With `--not-found`, a page that has no file must answer `404` with that file's bytes. |
| `--not-found PATH` | no | `None` | Server path of the file every request with no file is answered with, as `404`; must start with `/` and name a file in the tree. Never sent in a request itself, so any file name will do. Unset, a miss is Python's own error page. |
| `--port PORT` | no | `0` | The loopback port to listen on, `0` to `65535`; `0` lets the operating system pick a free one. A port already held exits `2`. |

There is deliberately no `--bind`: the interface is always loopback, and serving on any other
interface is not an option this tool will grow. `--port` exists for a caller that must name its
port in advance, such as a launch configuration a preview is pointed at. Left at `0`, the
operating system picks, which is what keeps two servers from colliding; a fixed port gives that
up, so a collision is
refused loudly. On macOS a listener on the wildcard address does not stop a loopback bind of the
same port, which then answers every loopback request: the url line still names the tree that
answers.

Once every page is confirmed it prints exactly one line, flushed, then serves until `SIGINT` or
`SIGTERM`:

```text
serving <absolute directory> at http://127.0.0.1:<port>
```

It needs only the standard library and two sibling folders, `stdio_utf8` and `help_formatter`.

## Serving a tree to review

Run it in the background from the checkout whose files you mean to see, read the url off its one
line, and open that url:

```bash
python loopback_site/loopback_site.py --directory "<folder of pages>" --pages / /archive/
```

The port changes on every run. That is the price of never colliding with a server someone else
left running, and it is why the url is read from the output rather than remembered. Stop it with
`SIGINT` or `SIGTERM` (`kill <pid>`); either exits `0`.

Both signals get a handler installed explicitly. A process launched in the background can inherit
`SIGINT` as ignored -- a non-interactive shell hands exactly that to an `&` child -- and an ignored
signal never becomes Python's `KeyboardInterrupt`, so relying on the default would leave an
interrupt doing nothing at all.

## As a library

- `serve_directory(directory, not_found=None, port=0)` is a context manager yielding the base
  url; the server stops when the block ends. `port` 0 lets the operating system pick; a port
  that cannot be bound raises `OSError` before the block starts. `not_found` is the keyword-only server path of a site's own
  404 page (see above); naming no file in the tree raises `SiteServeError` before the server
  starts.
- `confirm_serves(base_url, directory, page, timeout=..., not_found=None,
  follow_redirects=True)` fetches one page. It raises `OSError` when the file is missing, the
  server cannot be reached or it answers an error status, and `SiteServeError` when the bytes
  differ -- the server is answering from another tree. With `not_found`, a page that has no file
  must instead answer `404` with exactly the bytes of the file `not_found` names, else
  `SiteServeError`; a page that has a file is confirmed as without it. That `404` is fetched
  without following a redirect, so a directory named without its slash is refused as the `301` it
  answers: followed, the page reached would sit at another address than the one named.
- `follow_redirects` is keyword-only, and its default follows a redirect as a browser does, so
  every caller that does not pass it behaves as before. Passed `False`, a page that has its file
  is fetched the same way the `404` is: a redirect raises `SiteServeError` naming the page and the
  status -- never the address it points at -- even when that address answers the very same bytes.
  A caller measuring a host it does not run passes `False`, so the address confirmed is the
  address measured, not one an unreported hop away. Unfollowed, a redirect's address is never
  even read. `urllib`'s own redirect handler parses `Location` before it asks whether to follow,
  and a malformed address makes that parse raise a `ValueError` quoting part of it; declining
  every redirect status before that parse leaves a malformed redirect refused, or compared as
  the status it is, like any other.
- Every fetch `confirm_serves` makes, followed or not, goes through no proxy: urllib's default
  opener honors `http_proxy` and the system proxy settings, and would send a request for
  `127.0.0.1` to the proxy host instead.
- `page_path(text)` parses one page to confirm, as an `argparse` type: a server path starting
  with `/` and holding only printable ASCII without spaces (`PAGE_CHARACTERS`). `http.client`
  cannot send anything else, and fails with an error that is no `OSError`, so a CLI that
  confirms pages through `confirm_serves` parses them with this, refusing such a page before
  anything is served, and the rule lives once. The advice is to rename the file, never to
  percent-encode the page: `page_file` maps a page to its file undecoded, while a server decodes
  the request, so an encoded page names no file and can never be confirmed.
- `page_file(directory, page)` is the file a static server answers `page` with: a path ending in
  a slash, `/` included, is that directory's `index.html`, and any other path is the file it names.
  The standard library's handler also serves a directory's `index.htm`, which `page_file` does not
  know, so a page served from one cannot be confirmed.
  A page whose `..` segments climb out of `directory` is refused with `SiteServeError`, by the
  same lexical check `not_found` gets, before any caller checks or reads the file it would name:
  the request handler drops every `..` past the root, so no request could reach that file.

The page mapping and the confirmation live in the private `src/loopbacksite_confirm.py`, and
the meta-refresh reader below in `src/loopbacksite_refresh.py`, apart from the serving and
re-exported here; a caller names them through `loopback_site` only.

## Reading a page's meta refresh

A page that is a `<meta http-equiv="refresh" content="0; url=2026/">` navigates away as it loads.
A tool that loads it in a browser and then evaluates script races that navigation, and loses:
two browser-driving tools both crashed on a year-archive stub of exactly this shape. So a tool
reads the page over loopback first, and decides before any browser opens it whether the page is
a redirect, and whether its own list of pages declared it as one.

- `read_meta_refresh(page_url, timeout=...)` fetches `page_url` and returns its `MetaRefresh`, or
  `None` when the page declares none. It fetches only plain `http` to `127.0.0.1` or `localhost`:
  any other url raises `ValueError` before a request is made. It follows no HTTP redirect and
  goes through no proxy, whatever `http_proxy` or the system settings say, so nothing off
  loopback is ever fetched; a redirect or error status is raised as the `HTTPError` it is, and
  serving problems stay `confirm_serves`'s to report. The page is decoded in its response's
  charset, or UTF-8 when there is none or Python has no text codec by that name.
- `parse_meta_refresh(html, page_url)` is the pure half. It returns the first `<meta>` whose
  `http-equiv` is `refresh` in any case and whose content the HTML spec's "shared declarative
  refresh steps" accept -- the first that parses, not the first written, because a browser sets
  its "will declaratively refresh" flag only on one it accepts. The content is read as those
  steps read it, including the quirk that a remainder merely starting like `url=` is the URL
  whole, and a refresh with no URL names the page itself.
- `MetaRefresh` is frozen: `delay`, the whole seconds the spec reads (the digits before any dot),
  and `destination`, the URL resolved against the page with `urljoin`, after the URL parser's own
  clean-up of surrounding spaces and controls and embedded tabs and newlines. A URL `urljoin`
  cannot parse is no refresh, as a browser would not follow it. `destination` may name any
  scheme and any host: the parse only resolves it, so a caller must check it is the page's own
  origin before following it.
- The parse reads markup, not a scripting browser's DOM: a refresh `<meta>` inside `<noscript>`
  or `<template>` is found here, though a browser running script would not act on it.

`parse_meta_refresh` reads untrusted input, so a Hypothesis property asserts it never raises and
that any destination it returns is absolute.

A caller puts this folder on `sys.path` by its fixed path beside it, and names both the folder
and its `src/` in its project's `mypy_path`, because mypy cannot see a runtime `sys.path`
insert.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Stopped cleanly by `SIGINT` or `SIGTERM`. |
| `2` | Could not serve honestly: `--directory` is not a directory, `--not-found` names no file in it, `--port` is already held, or a page was not confirmed (missing on disk, unreachable, or answered with other bytes or another status). A parse error -- no `--directory`, or a page without a leading `/` or holding a space, a control or a non-ASCII character -- is also `2`, from argparse. |

## Tests

`test_loopback_site.py` runs bare. It is the aggregator for the grouped suites under `tests/`,
split between the library, the CLI, the not-found page, the meta-refresh reader and the proxy
check, and it floors each file's test count. Every server a test starts binds loopback on an
OS-picked port over a scratch tree the test writes, so nothing reaches the network.

- The `Host` check is exercised with `http.client`, which sends exactly the `Host` header a test
  names, or none.
- A `HEAD` miss is read off a bare socket instead: `http.client` returns an empty body for any
  `HEAD`, so it could not see a body sent by mistake after the headers.
- `--port` is tested on a free port, which the url line must name, and on one a listening socket
  already holds, which must exit `2` naming it; out-of-range and non-numeric values are parse
  errors.
- `follow_redirects` is tested against a real `301`: the served tree holds a directory, named
  without its slash, whose index is byte-identical to the file the expected tree holds at that
  path. So a followed redirect confirms, and only the refusal can tell the two apart. An error
  status whose body equals the expected bytes pins that, unfollowed, a `404` is still refused
  rather than compared. A tiny server of the suite's own answers every page with a `301` to a
  malformed bracketed address, which must be refused, or compared as a `301` where a `404` is
  due, without the address appearing in the message.
- The meta-refresh reader is tested pure against the refresh steps' cases, and live against a
  served stub, an error status, a `301` it must not follow, and a small server of the suite's own
  that labels a page's charset. Its refusal of a url off loopback is tested with the opener
  patched out, so any request at all would fail the test.
- The proxy check sets `http_proxy` and `HTTP_PROXY` to an RFC 5737 address nothing answers at,
  then reads a served tree through `read_meta_refresh` and every `confirm_serves` path in a fresh
  interpreter: urllib reads the proxy settings when an opener is built, so only a fresh import
  sees them. A proxied read would time out against that address instead of reaching loopback.
- A page `http.client` cannot send is refused at parse time, through `main`, with its file
  present on disk, so only the request could have failed; the refusal must advise renaming the
  file, never percent-encoding the page.
- Leaving a `serve_directory` block is timed over five exits against a bound between a short poll
  and `socketserver`'s default, by the median so one scheduling stall cannot decide it. A
  deterministic twin binds the call the loop received against the stdlib signature and checks the
  interval it was handed.
- Client hang-ups are tested twice. A patched body write raises each hang-up, then each of two
  other failures, `ConnectionAbortedError` among them: stderr must stay empty for the first and
  carry the traceback for the second. A real one, on POSIX, resets the connection with an RST while
  the server is writing a sparse body larger than the socket buffers. That test records what the
  request raised, so it cannot pass on a reset that never reached the server.
- Neither reads stderr on the strength of leaving `serve_directory`: `ThreadingHTTPServer` handles
  on daemon threads, and `server_close()` joins only non-daemon ones, so a report can still be in
  progress after the block ends. The patched tests need nothing more, because the server reports
  before it closes the socket and the client's cut-short read waits on that close. The reset
  client never sees that close, so the live test waits instead on a signal set when the handler
  thread returns, inside the stderr capture.
- The default a miss gets is pinned by comparison, not restated: a stock
  `SimpleHTTPRequestHandler` serves the same tree beside it, and a miss and a directory listing
  must match it in status, `Content-Type` and body. So
  the pin holds whatever the standard library's page says, and fails the moment the two differ.
- The signal test sets each disposition to ignored BEFORE installing the handlers and asserts it
  changed. That assertion, not a stop-timing bar, is what catches a missing handler.
- Three live tests launch the real CLI under the suite's own interpreter, on POSIX only. Two stop it
  with `SIGTERM`, and with `SIGINT` inherited as ignored through `exec`, and require exit `0`, a
  silent stderr and a released port.
- The third freezes the CLI with `SIGSTOP`, and confirms the freeze from the kernel's wait status.
  It then opens a burst of connections (`_BURST_CONNECTIONS`, far more than 5) that each send a
  GET, resumes the CLI, and requires every one to be answered with the page. Frozen, the server
  accepts nothing, so every connection waits in the listen queue at the same moment, and the
  verdict turns on the queue's depth alone rather than on a race with the accept loop. Against the
  inherited queue of 5 it failed in each of three runs: exactly 5 were answered and the rest reset.

No `samples` folder: a server turns no input into an output a human could diff.
