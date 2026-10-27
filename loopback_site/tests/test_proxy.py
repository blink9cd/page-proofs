"""Grouped suite: the library -- a loopback read never goes through a configured proxy.

urllib's default opener holds a `ProxyHandler` that reads `http_proxy` / `HTTP_PROXY` (and, on
some platforms, the system proxy settings), and a proxied request for `127.0.0.1` is sent to the
proxy host rather than to loopback. Both `read_meta_refresh` and `confirm_serves` promise a
loopback-only read, so each must reach the real loopback server with a proxy configured.

The handler reads the environment once, when it is built -- at import for a module-level opener,
and on the first `urlopen` for urllib's global one. Patching the environment of this process
would arrive after both, so the reads run in a fresh interpreter that inherits the patched
environment and imports the tool there.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import loopback_site  # noqa: E402 - import follows the sys.path bootstrap above
from _fixtures import TIMEOUT, scratch_tree  # noqa: E402 - beside this module, via the loader's top_level_dir

#: An RFC 5737 documentation address on the discard port: nothing answers there, so a request
#: sent to it as a proxy fails instead of reaching the loopback server.
_UNREACHABLE_PROXY = "http://192.0.2.1:9"

#: The proxy variables urllib reads, in both spellings.
_PROXY_VARIABLES = ("http_proxy", "HTTP_PROXY")

#: The bypass variables, which would exempt loopback and hide the defect.
_BYPASS_VARIABLES = ("no_proxy", "NO_PROXY")

#: Bound on each request in the child: long enough for loopback, short against the proxy.
_CHILD_TIMEOUT = 3.0

#: The reads the child performs: every opener the tool fetches a loopback page through.
_CHILD_SCRIPT = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import loopback_site
base_url, tree, timeout = sys.argv[2], Path(sys.argv[3]), float(sys.argv[4])
refresh = loopback_site.read_meta_refresh(base_url + "/archive/", timeout=timeout)
assert refresh is None, refresh
loopback_site.confirm_serves(base_url, tree, "/archive/", timeout=timeout)
loopback_site.confirm_serves(
    base_url, tree, "/archive/", timeout=timeout, follow_redirects=False
)
loopback_site.confirm_serves(base_url, tree, "/missing/", timeout=timeout, not_found="404.html")
"""


class ProxyTests(unittest.TestCase):
    """A configured proxy is never used for a loopback read."""

    def test_loopback_reads_ignore_a_configured_proxy(self) -> None:
        tree = scratch_tree(self)
        tool_dir = Path(loopback_site.__file__).resolve().parent
        proxied = dict.fromkeys(_PROXY_VARIABLES, _UNREACHABLE_PROXY)
        with (
            loopback_site.serve_directory(tree, not_found="404.html") as base_url,
            mock.patch.dict(os.environ, proxied),
        ):
            for name in _BYPASS_VARIABLES:
                os.environ.pop(name, None)
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    _CHILD_SCRIPT,
                    str(tool_dir),
                    base_url,
                    str(tree),
                    str(_CHILD_TIMEOUT),
                ],
                # Uncaptured: a failing read's traceback goes to this run's own stderr, and
                # with no pipe for a grandchild to hold open the timeout binds (RL005).
                timeout=TIMEOUT * 4,
                check=False,
            )
        self.assertEqual(child.returncode, 0, "a loopback read failed under a proxy")


if __name__ == "__main__":
    unittest.main()
