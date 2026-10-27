// The node suite for src/csp_listener.js, the listener csp_watch installs as an init script.
//
// The listener runs here the way an engine runs the init script install() builds: its file's
// text evaluated as a classic script in the page, then one call naming the page's binding. It
// runs through `vm` under its own path, which is what lands node's coverage on csp_listener.js
// itself rather than on an anonymous script.
//
// jsdom implements no Content Security Policy, so nothing here is ever blocked: each test
// dispatches a plain `securitypolicyviolation` Event carrying the three properties the
// listener reads, as an engine's SecurityPolicyViolationEvent carries them. The binding is a
// stand-in that records what it is handed, copied out of the page's realm through JSON as
// Playwright's serialization copies it. What an engine really reports is the live suite's job
// (test_csp_watch_live.py), which runs the same source in Chromium, Firefox and WebKit.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

import { JSDOM, VirtualConsole } from "jsdom";

const LISTENER_PATH = fileURLToPath(
  new URL("../src/csp_listener.js", import.meta.url),
);
const LISTENER_SOURCE = readFileSync(LISTENER_PATH, "utf8");

/** The binding's name, as csp_watch.BINDING spells it. */
const BINDING = "__cspWatch";

/** The page the listener is installed in: a path and a query, as the 404 page is reached. */
const PAGE_URL = "http://page.test/404.html?x=1";

/** The longest sample the listener hands on. */
const SAMPLE_LIMIT = 40;

/** One violation's three fields, as an engine fills them for an inline style attribute. */
const STYLE_ATTRIBUTE = Object.freeze({
  effectiveDirective: "style-src-attr",
  blockedURI: "inline",
  sample: "",
});

/**
 * A fresh page with the listener installed, and the handles a test reads it by.
 * @param {object} [options]
 * @param {boolean} [options.bound] - Whether the binding is exposed before the listener runs.
 */
function loadListener({ bound = true } = {}) {
  const errors = [];
  const virtualConsole = new VirtualConsole();
  // A listener that throws does not throw out of dispatchEvent: jsdom reports it here, as an
  // engine reports it to the page's error handlers, so this list is what "no throw" reads.
  virtualConsole.on("jsdomError", (error) => errors.push(error.message));
  const dom = new JSDOM(
    "<!doctype html><html><head></head><body><p>text</p></body></html>",
    { runScripts: "outside-only", url: PAGE_URL, virtualConsole },
  );
  const { window } = dom;
  const calls = [];
  const bind = () => {
    window[BINDING] = (payload) => {
      calls.push(JSON.parse(JSON.stringify(payload)));
    };
  };
  if (bound) bind();
  const context = dom.getInternalVMContext();
  vm.runInContext(LISTENER_SOURCE, context, { filename: LISTENER_PATH });
  vm.runInContext(`cspWatch(window, ${JSON.stringify(BINDING)});`, context);
  return { window, calls, errors, bind };
}

/**
 * Dispatch one violation at `target`, the document unless a test names an element. Like an
 * engine's, the event does not bubble unless `bubbles` says so.
 * @param {Window} window
 * @param {object} fields - The event's effectiveDirective, blockedURI and sample.
 * @param {object} [options]
 * @param {EventTarget} [options.target]
 * @param {boolean} [options.bubbles]
 */
function violate(window, fields, { target, bubbles = false } = {}) {
  const event = new window.Event("securitypolicyviolation", { bubbles });
  Object.assign(event, fields);
  (target ?? window.document).dispatchEvent(event);
}

test("the binding receives the document, the directive, what was blocked and the sample", () => {
  const page = loadListener();
  violate(page.window, STYLE_ATTRIBUTE);
  assert.deepEqual(page.calls, [
    {
      document: "/404.html?x=1",
      directive: "style-src-attr",
      blocked: "inline",
      sample: "",
    },
  ]);
  assert.deepEqual(page.errors, []);
});

test("an event at an element that does not bubble still reaches the capturing listener", () => {
  const page = loadListener();
  const paragraph = page.window.document.querySelector("p");
  violate(page.window, STYLE_ATTRIBUTE, { target: paragraph });
  assert.equal(page.calls.length, 1);
  assert.equal(page.calls[0].directive, "style-src-attr");
});

test("a sample longer than the limit arrives cut to it", () => {
  const page = loadListener();
  const long = "--a-custom-property: 1; color: red; ".repeat(2).slice(0, 60);
  assert.equal(long.length, 60);
  violate(page.window, { ...STYLE_ATTRIBUTE, sample: long });
  assert.equal(page.calls[0].sample, long.slice(0, SAMPLE_LIMIT));
});

test("a sample is cut between characters, never inside a surrogate pair", () => {
  const page = loadListener();
  // Each of these is one character and two UTF-16 code units, so a cut by code units at the
  // limit would keep twenty of them; a cut by characters keeps the limit's worth, whole.
  const astral = "\u{1F600}".repeat(SAMPLE_LIMIT + 1);
  violate(page.window, { ...STYLE_ATTRIBUTE, sample: astral });
  assert.equal(page.calls[0].sample, "\u{1F600}".repeat(SAMPLE_LIMIT));
});

test("a sample that is not a string is handed on as it came, for the driver to refuse", () => {
  const page = loadListener();
  violate(page.window, { ...STYLE_ATTRIBUTE, sample: 7 });
  violate(page.window, { effectiveDirective: "img-src", blockedURI: "" });
  assert.deepEqual(page.errors, []);
  assert.equal(page.calls.length, 2);
  assert.equal(page.calls[0].sample, 7);
  // An event with no sample at all still makes its call. The JSON copy here drops the undefined
  // value; whichever way the driver carries one, csp_watch refuses the payload.
  assert.equal(page.calls[1].directive, "img-src");
  assert.equal(page.calls[1].sample, undefined);
});

test("a page where the binding was never exposed does not throw", () => {
  const page = loadListener({ bound: false });
  violate(page.window, STYLE_ATTRIBUTE);
  assert.deepEqual(page.errors, []);
  assert.deepEqual(page.calls, []);
});

test("a binding exposed after the listener is installed is the one called", () => {
  const page = loadListener({ bound: false });
  page.bind();
  violate(page.window, STYLE_ATTRIBUTE);
  assert.equal(page.calls.length, 1);
});

test("a value at the binding's name that is not a function is not called", () => {
  const page = loadListener({ bound: false });
  page.window[BINDING] = "not a function";
  violate(page.window, STYLE_ATTRIBUTE);
  assert.deepEqual(page.errors, []);
});

test("two events make two calls, in the order they were dispatched", () => {
  const page = loadListener();
  violate(page.window, STYLE_ATTRIBUTE);
  violate(page.window, {
    effectiveDirective: "img-src",
    blockedURI: "https://images.example/cover.jpg",
    sample: "",
  });
  assert.deepEqual(
    page.calls.map((call) => [call.directive, call.blocked]),
    [
      ["style-src-attr", "inline"],
      ["img-src", "https://images.example/cover.jpg"],
    ],
  );
});
