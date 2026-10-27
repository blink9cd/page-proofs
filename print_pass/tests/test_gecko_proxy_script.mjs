// The node suite for the Firefox engine's proxy auto-config script, parsed and run offline.
//
// Firefox runs a proxy auto-config script it cannot evaluate as no proxy at all, every
// request direct (measured), so a script that does not parse is no isolation, and nothing
// says so: the run goes on with every port and host reachable. The Python suite holds the
// script `gecko_prefs.proxy_script` writes for a served port of 8123 and a dead port of 4242
// byte for byte, in proxy_script.pac beside this file; this suite parses that very text as
// JavaScript and asks it where each URL goes, so a script edited together with its pin into
// one that does not parse, or that sends another origin direct, fails here, with no browser.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const SCRIPT_PATH = fileURLToPath(new URL("proxy_script.pac", import.meta.url));

// The file ends in one line break, which the script itself does not hold.
const SCRIPT = readFileSync(SCRIPT_PATH, "utf8").replace(/\n$/u, "");

const DIRECT = "DIRECT";
const DEAD = "PROXY 127.0.0.1:4242";

// The script's FindProxyForURL, from the script run alone in a fresh context, as Firefox
// runs it apart from every page.
function findProxy() {
  const context = vm.createContext({});
  vm.runInContext(SCRIPT, context, { filename: SCRIPT_PATH });
  return context.FindProxyForURL;
}

// Where the script sends `url`, Firefox handing it the URL and its host, as it does.
function route(url) {
  return findProxy()(url, new URL(url).hostname);
}

test("the script parses as JavaScript, one line defining FindProxyForURL", () => {
  assert.doesNotThrow(() => new vm.Script(SCRIPT));
  assert.equal(typeof findProxy(), "function");
  assert.equal(SCRIPT.includes("\n"), false);
});

test("the served origin's pages and WebSockets go direct", () => {
  for (const url of [
    "http://127.0.0.1:8123/",
    "http://127.0.0.1:8123/print/index.html?state=dark#top",
    "ws://127.0.0.1:8123/livereload",
  ]) {
    assert.equal(route(url), DIRECT, url);
  }
});

test("every other origin goes to the dead proxy", () => {
  for (const url of [
    "http://127.0.0.1:8124/",
    "http://127.0.0.1:18123/",
    "http://127.0.0.1:812/",
    "https://127.0.0.1:8123/",
    "wss://127.0.0.1:8123/",
    "http://localhost:8123/",
    "http://[::1]:8123/",
    "http://user@127.0.0.1:8123/",
    "http://127.0.0.1:8123@example.invalid/",
    "ws://127.0.0.1:4242/",
    "ftp://127.0.0.1:8123/",
    "http://example.invalid/",
    "http://192.0.2.1/",
  ]) {
    assert.equal(route(url), DEAD, url);
  }
});
