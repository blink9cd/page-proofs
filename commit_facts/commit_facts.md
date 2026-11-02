# commit_facts

Which commit a tree is at, and whether the tree differs from it, or that git cannot say.
A library, not a CLI: there is nothing to run.

## What it is for

A measurement compares with a later one only when both name the code they measured. So
a tool that measures a site records, beside its numbers, the commit the measured tree
was at and whether it had uncommitted edits. This module is the one reading of those two
facts.

## Who calls it

| Caller                              | Its scope                                       | Its report's fields   |
| ----------------------------------- | ----------------------------------------------- | --------------------- |
| [`lighthouse_run`](../README.md)    | the `--serve` folder, else the project's `src/` | `commit`, `src_dirty` |

The SCOPE is the caller's to name: the folder whose uncommitted edits count. HEAD is read
from whichever repository holds it. Each measuring caller maps the result's `sha` and
`dirty` onto its own report's field names; a caller that only requires a clean tree records
neither. Those names predate this module and stay as they are, because
recorded baselines and earlier reports are read by them.

The caller reads it before anything is served. The tree measured is the tree at the
start, so a commit made during a long run is not recorded as the one measured. A git that
fails or hangs then costs only its own bound, never the finished measurement.

## Why it exists rather than living in each tool

A copy per tool drifts. Two earlier copies differed only in scope and field name, and both
had the same defect: they ignored `git status`'s exit code. A status that failed printed
nothing, and nothing is exactly what a clean tree prints, so a report could call a tree
clean that nobody had checked. One copy also read the commit after the runs, outside any
error handling, so a git that timed out threw away a finished measurement. Fixing that in
each copy would have left several spellings of the same rule to drift apart.

## Using it

```python
facts = commit_facts.read(scope, git=shutil.which("git"), timeout=30.0)
if facts.problem is not None:
    warn(f"which commit {scope} is at is unknown: {facts.problem}")
record(commit=facts.sha, dirty=facts.dirty)
```

`read` never raises for git's sake. Callers put this folder on `sys.path` by its fixed path
beside them, and it reaches `bounded_run/`, the folder beside it, the same way.

## What it guarantees, and why each part is not optional

- **The pair is whole or absent.** A commit names the code measured only alongside whether
  the tree differed from it. Whenever git cannot answer both halves, both are `None`. A
  commit beside an unknown would print in every report as a bare commit, which reads as a
  clean tree.
- **Three ways to not know, told apart.**

  | Case                                                         | `sha`, `dirty` | `problem`  |
  | ------------------------------------------------------------ | -------------- | ---------- |
  | git is absent                                                | `None`, `None` | `None`     |
  | the scope is outside any repository                          | `None`, `None` | `None`     |
  | `status` exits non-zero, or a call times out or cannot start | `None`, `None` | the reason |

  The first two are ordinary: a machine without git, a built copy in a scratch folder.
  Nothing went wrong, so there is nothing to print. The third is git failing,
  which the caller prints rather than letting it pass silently.

- **Unknown, not an error, not a refusal.** The measurement beside a failed git is still
  worth keeping, and the report says plainly that the commit is unknown. Refusing instead
  would also be inconsistent: a machine with no git gets a report, so one whose git is
  broken should too.
- **Every git call is bounded** by the caller's `timeout`, through `bounded_run`, which
  kills the whole process tree on expiry.

## Not in scope

Anything else read from git: a caller's other questions for git stay that caller's own.
