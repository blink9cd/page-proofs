// The scaffolding the page_probe suites share: the in-page probe loaded into a jsdom page,
// with the page's LAYOUT supplied by the test and the browser APIs jsdom lacks stood in.
//
// WHAT IS REAL. The DOM, its selectors (`closest`, `matches(":disabled")`), and the computed
// style the probe reads: jsdom's cascade answers `display` from its own UA stylesheet (a <div>
// is block, an <a> inline, a [hidden] element none), `overflow-x` and `visibility` from the
// page's CSS, `position` from a style attribute. So an exemption the probe makes on a style --
// a scroller, a visibility-hidden reserve, a link inside a sentence -- is exercised by real CSS
// in the fixture, not by a stated answer. The probe itself is its three files' text, each trimmed
// as the driver trims it: page_probe.js applied to its motion half, page_probe_motion.js, and its
// tree walker, page_probe_trees.js, as the driver's PROBE_SOURCE applies them, which makes the
// one function an engine receives, called with [op, config]. Each file runs through `vm` under
// its own path before they are applied, so node's coverage counts each against its own file
// without the probe becoming a module.
//
// WHAT IS STOOD IN, AND WHY. jsdom does no layout: every element reports no client rects and a
// zero box, and the viewport, scroll offsets and scroll sizes are fixed or zero. Every detector's
// "found something" path reads exactly those numbers, so a test STATES the layout it assumes: by
// default an element's `data-box` attribute ("x y width height", viewport coordinates), or a
// `boxOf` function the test passes. An element whose own or a flat-tree ancestor's computed
// display is none gets no box whatever it states, because an engine gives it none. Four APIs
// jsdom does not implement at all -- matchMedia, document.fonts, PerformanceObserver and
// performance.getEntriesByType -- are fakes the test drives, and a fifth, a script element's
// `async` property, reflects its attribute as an engine's does for a script the parser inserted.
// The Web Animations API is absent as well; the motion suites stand in `document.getAnimations`
// and `document.timeline`, and each shadow root's `getAnimations`, through
// page_probe_motion_helpers.mjs, with the animations each of their tests needs. jsdom enforces
// no Content Security Policy either, so a test that needs a page refusing style attributes
// stands one in (`refuseStyleAttributes`).
//
// WHAT THIS CANNOT PROVE: that an engine reports what these fakes assume. That is the live
// suites' job (test_pass_live.py, test_pass_late_live.py), which run the same source in
// Chromium, Firefox and WebKit. These suites pin the DECISIONS the probe makes over those
// numbers -- exemptions, thresholds, de-duplication, names, ordering -- on every test run.
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

import { JSDOM, VirtualConsole } from "jsdom";

const HERE = path.dirname(fileURLToPath(import.meta.url));

/** The probe's three files: each is evaluated under its own name, so coverage counts it there. */
const PROBE_PATH = path.join(HERE, "..", "src", "page_probe.js");
const MOTION_PATH = path.join(HERE, "..", "src", "page_probe_motion.js");
const TREES_PATH = path.join(HERE, "..", "src", "page_probe_trees.js");

/**
 * The function expression the probe file at `file` holds, evaluated in `context`: its text
 * trimmed, as the driver's PROBE_SOURCE trims each file before it reaches an engine.
 * @param {string} file
 * @param {vm.Context} context
 */
function evaluateFile(file, context) {
  return vm.runInContext(readFileSync(file, "utf8").trim(), context, {
    filename: file,
  });
}

/** Where the page is served from; a script's `src` resolves against it. */
export const PAGE_URL = "http://page.test/";

/** The shared configuration, under the keys of the driver's PROBE_CONFIG. */
export const CONFIG = Object.freeze({
  plantAttribute: "data-browser-pass-plant",
  plantMark: " planted for ",
  consoleText: "a planted console error",
  pageErrorText: "a planted page error",
  missingPath: "/missing-plant.png",
  shiftLog: "__browserPassShifts",
});

/** The viewport a page gets unless a test states another: a phone, portrait. */
export const VIEWPORT = Object.freeze({ width: 390, height: 844 });

/**
 * A box as an engine reports one from getBoundingClientRect.
 * @typedef {{x: number, y: number, width: number, height: number, left: number, top: number,
 *   right: number, bottom: number}} Box
 */

/**
 * The box at (`x`, `y`) of the given size.
 * @param {number} x
 * @param {number} y
 * @param {number} width
 * @param {number} height
 * @returns {Box}
 */
export function rect(x, y, width, height) {
  return {
    x,
    y,
    width,
    height,
    left: x,
    top: y,
    right: x + width,
    bottom: y + height,
  };
}

/**
 * The default layout: the box an element's `data-box` states, or none.
 * @param {Element} element
 * @returns {Box | null}
 */
export function boxFromAttribute(element) {
  const stated = element.getAttribute("data-box");
  if (stated === null) return null;
  const [x, y, width, height] = stated.trim().split(/\s+/).map(Number);
  return rect(x, y, width, height);
}

/**
 * Whether an engine would lay `element` out at all: nothing up to <html> is display none, up the
 * flat tree an engine lays boxes out by -- a slotted element through its slot, the top of a
 * shadow root through its host.
 */
function isDisplayed(window, element) {
  const flatParent = (node) =>
    node.assignedSlot ??
    node.parentElement ??
    (node.parentNode instanceof window.ShadowRoot
      ? node.parentNode.host
      : null);
  for (let node = element; node; node = flatParent(node)) {
    if (window.getComputedStyle(node).display === "none") return false;
  }
  return true;
}

/** Give `target` fixed values for the named properties, shadowing jsdom's own. */
function define(target, values) {
  for (const [name, value] of Object.entries(values)) {
    Object.defineProperty(target, name, { configurable: true, value });
  }
}

/** Replace jsdom's absent layout with the one the test states. */
function fakeLayout(window, { boxOf, viewport, scroll, root }) {
  const laidOut = (element) =>
    isDisplayed(window, element) ? boxOf(element) : null;
  window.Element.prototype.getClientRects = function clientRects() {
    const box = laidOut(this);
    return box ? [box] : [];
  };
  window.Element.prototype.getBoundingClientRect = function boundingRect() {
    return laidOut(this) ?? rect(0, 0, 0, 0);
  };
  define(window, {
    innerWidth: viewport.width,
    innerHeight: viewport.height,
    scrollX: scroll.x,
    scrollY: scroll.y,
  });
  define(window.document.documentElement, {
    scrollWidth: root.scrollWidth ?? viewport.width,
    scrollHeight: root.scrollHeight ?? viewport.height,
    clientWidth: root.clientWidth ?? viewport.width,
  });
}

/**
 * Stand in the platform APIs jsdom lacks, and return the handles a test drives them by.
 * Paint entries are kept in one list, delivered to paint observers and read back by
 * performance.getEntriesByType alike, so the two views of a paint cannot disagree.
 */
function fakePlatform(window, { matching, layoutShifts }) {
  // jsdom defines no `async` on a script element, so every script would read as not async.
  Object.defineProperty(window.HTMLScriptElement.prototype, "async", {
    configurable: true,
    get() {
      return this.hasAttribute("async");
    },
  });

  window.matchMedia = (query) => ({
    matches: matching.includes(query),
    media: query,
  });

  let resolveFonts = () => {};
  const fonts = {
    status: "loading",
    ready: new Promise((resolve) => {
      resolveFonts = resolve;
    }),
  };
  window.document.fonts = fonts;

  const entries = { paint: [], resource: [] };
  window.performance.getEntriesByType = (type) => entries[type] ?? [];

  const observers = [];
  window.PerformanceObserver = class FakePerformanceObserver {
    static supportedEntryTypes = layoutShifts
      ? ["layout-shift", "paint", "resource"]
      : ["paint", "resource"];
    constructor(callback) {
      this.callback = callback;
      this.disconnected = false;
      observers.push(this);
    }
    observe(options) {
      // Copied out of the page's realm, so a test can compare it with its own objects.
      this.options = { ...options };
    }
    disconnect() {
      this.disconnected = true;
    }
  };

  /** Hand `list` to every connected observer of `type`, as an engine's delivery would. */
  const deliver = (type, list) => {
    for (const observer of observers) {
      if (observer.options?.type !== type || observer.disconnected) continue;
      observer.callback({ getEntries: () => list }, observer);
    }
  };

  return {
    observers,
    deliver,
    /** Record a paint at `startTime` and deliver it. */
    paint: (startTime) => {
      const entry = { entryType: "paint", startTime };
      entries.paint.push(entry);
      deliver("paint", [entry]);
    },
    /** Record resource entries: [url, renderBlockingStatus] pairs. */
    resources: (pairs) => {
      for (const [name, renderBlockingStatus] of pairs) {
        entries.resource.push({ name, renderBlockingStatus });
      }
    },
    /** The page's fonts arrive: the status reads loaded and `ready` resolves. */
    loadFonts: () => {
      fonts.status = "loaded";
      resolveFonts(fonts);
    },
  };
}

/**
 * Load the probe into a fresh page.
 * @param {object} [options]
 * @param {string} [options.head] - Markup for <head>: styles and scripts.
 * @param {string} [options.body] - Markup for <body>.
 * @param {(element: Element) => Box | null} [options.boxOf] - The layout the test assumes.
 * @param {{width: number, height: number}} [options.viewport]
 * @param {{x: number, y: number}} [options.scroll]
 * @param {{scrollWidth?: number, scrollHeight?: number, clientWidth?: number}} [options.root]
 * @param {string[]} [options.matching] - The media queries that match.
 * @param {boolean} [options.layoutShifts] - Whether the engine reports layout shifts.
 */
export function loadProbe({
  head = "",
  body = "",
  boxOf = boxFromAttribute,
  viewport = VIEWPORT,
  scroll = { x: 0, y: 0 },
  root = {},
  matching = [],
  layoutShifts = true,
} = {}) {
  const errors = { console: [], page: [] };
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("error", (message) => errors.console.push(message));
  virtualConsole.on("jsdomError", (error) => errors.page.push(error.message));
  const dom = new JSDOM(
    `<!doctype html><html><head>${head}</head><body>${body}</body></html>`,
    {
      runScripts: "outside-only",
      pretendToBeVisual: true,
      url: PAGE_URL,
      virtualConsole,
    },
  );
  const { window } = dom;
  fakeLayout(window, { boxOf, viewport, scroll, root });
  const platform = fakePlatform(window, { matching, layoutShifts });
  const context = dom.getInternalVMContext();
  const motion = evaluateFile(MOTION_PATH, context);
  const reachableElements = evaluateFile(TREES_PATH, context);
  // The composition PROBE_SOURCE states as text: the probe applied to its motion half and its
  // tree walker.
  const probe = evaluateFile(PROBE_PATH, context)(motion, reachableElements);
  return {
    window,
    document: window.document,
    errors,
    ...platform,
    /**
     * Run one operation, with `extra` joining the shared configuration as the driver does. The
     * driver receives the result serialized, never the page's own objects; a JSON round trip is
     * that crossing here, and it brings the value out of the page's realm into this one. `listed`
     * is the second argument the late-script load's DevTools session hands back: a list `hold`
     * made, which the page kept.
     */
    run: (op, extra = {}, listed = undefined) =>
      JSON.parse(JSON.stringify(probe([op, { ...CONFIG, ...extra }], listed))),
    /**
     * Run one operation and keep its result as the page holds it, never serialized: what the
     * late-script load's DevTools session keeps a handle on, rather than a copy.
     */
    hold: (op, extra = {}) => probe([op, { ...CONFIG, ...extra }]),
    /** The late-script load's log, read from the page global it lives in, as `run` reads. */
    shiftLog: () => JSON.parse(JSON.stringify(window[CONFIG.shiftLog])),
  };
}

/**
 * Stand in a page whose policy refuses style attributes, as a `style-src` without
 * 'unsafe-inline' does: a style attribute written through the attribute API is dropped, as an
 * engine refuses one, and recorded. A CSSOM write never passes through that API, in jsdom or in
 * an engine, and the policy does not govern it, so it still applies.
 * @param {Window} window
 * @returns {string[]} The refused attribute values, in the order they were written.
 */
export function refuseStyleAttributes(window) {
  const refused = [];
  const { prototype } = window.Element;
  const { setAttribute, setAttributeNS } = prototype;
  const isStyle = (name) => name.toLowerCase() === "style";
  prototype.setAttribute = function setUnlessStyle(name, value) {
    if (isStyle(name)) refused.push(value);
    else setAttribute.call(this, name, value);
  };
  prototype.setAttributeNS = function setUnlessStyleNS(namespace, name, value) {
    if (isStyle(name)) refused.push(value);
    else setAttributeNS.call(this, namespace, name, value);
  };
  return refused;
}

/**
 * Wait until every pending microtask has run and then `count` animation frames have passed.
 * A callback registered during a frame runs in the next one, so a chain of N nested frames
 * registered before this call has finished once N frames have.
 * @param {Window} window
 * @param {number} count
 */
export async function frames(window, count) {
  await Promise.resolve();
  for (let frame = 0; frame < count; frame += 1) {
    await new Promise((resolve) => window.requestAnimationFrame(resolve));
  }
}
