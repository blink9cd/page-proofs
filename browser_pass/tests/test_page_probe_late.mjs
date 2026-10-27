// The in-page probe's late-script half (pass_late_load.py): the layout-shift log an init script
// installs before the page's own code, the hold on the page's scripts, the late-shift plant, and
// the record the driver reads back.
//
// WHAT THIS SUITE COVERS. What the log records and from when: whether the engine reports shifts
// at all (one that does not must read as not measured, never as a page that did not move); the
// moment the page became presentable -- painted, THEN in its own fonts, THEN two frames on, each
// step load-bearing for the reasons page_probe.js gives; which scripts the page cannot paint
// without; and what each shift's sources are called. Where the order of events is the point,
// the test drives the events in that order and asserts between them.
//
// WHAT IS FAKED is page_probe_test_helpers.mjs's header. Here it is mostly the platform: the
// paint and layout-shift entries an engine would deliver and the fonts' arrival, which the test
// hands over one at a time.
import assert from "node:assert/strict";
import test from "node:test";

import {
  CONFIG,
  frames,
  loadProbe,
  PAGE_URL,
  rect,
  refuseStyleAttributes,
} from "./page_probe_test_helpers.mjs";

/** The observer the probe made for one entry type. */
const observerOf = (page, type) =>
  page.observers.find((observer) => observer.options.type === type);

// --- The log ---

test("the log is installed under the configured name, and says shifts are reported", () => {
  const page = loadProbe();
  assert.equal(page.run("watchShifts"), true);
  assert.deepEqual(page.shiftLog(), {
    supported: true,
    released: null,
    presentableAt: null,
    entries: [],
  });
  // Buffered, so a shift or a paint from before the observer existed is still delivered.
  assert.deepEqual(
    page.observers.map((observer) => observer.options),
    [
      { type: "layout-shift", buffered: true },
      { type: "paint", buffered: true },
    ],
  );
});

test("an engine that reports no shifts reads as not measured, and none are observed", () => {
  const page = loadProbe({ layoutShifts: false });
  assert.equal(page.run("watchShifts"), false);
  assert.equal(page.shiftLog().supported, false);
  assert.deepEqual(
    page.observers.map((observer) => observer.options.type),
    ["paint"],
  );
});

// --- When the page became presentable ---

test("the page is presentable two frames after it has painted and its fonts are in", async () => {
  const page = loadProbe();
  page.run("watchShifts");
  page.paint(5);
  page.loadFonts();
  await frames(page.window, 1);
  assert.equal(
    page.shiftLog().presentableAt,
    null,
    "one frame on, the frame showing the fonts has not been drawn",
  );
  await frames(page.window, 1);
  assert.equal(typeof page.shiftLog().presentableAt, "number");
});

test("fonts in before any paint do not make the page presentable: the paint comes first", async () => {
  const page = loadProbe();
  page.run("watchShifts");
  page.loadFonts();
  await frames(page.window, 3);
  assert.equal(page.shiftLog().presentableAt, null);
  page.paint(5);
  await frames(page.window, 2);
  assert.equal(typeof page.shiftLog().presentableAt, "number");
});

test("a paint without its fonts is not presentable until they arrive", async () => {
  const page = loadProbe();
  page.run("watchShifts");
  page.paint(5);
  assert.equal(observerOf(page, "paint").disconnected, true);
  await frames(page.window, 3);
  assert.equal(page.shiftLog().presentableAt, null);
  page.loadFonts();
  await frames(page.window, 2);
  assert.equal(typeof page.shiftLog().presentableAt, "number");
});

test("a paint delivery with no entries in it is not a paint", async () => {
  const page = loadProbe();
  page.run("watchShifts");
  page.loadFonts();
  page.deliver("paint", []);
  await frames(page.window, 3);
  assert.equal(page.shiftLog().presentableAt, null);
  assert.equal(observerOf(page, "paint").disconnected, false);
});

// --- The record the driver reads back ---

test("the record carries the log, the paints and every shift, each source named", () => {
  const page = loadProbe({ body: `<main id="m"><p>text</p></main>` });
  page.run("watchShifts");
  page.paint(12.5);
  const main = page.document.querySelector("main");
  const text = page.document.querySelector("p").firstChild;
  const removed = page.document.createElement("aside");
  page.document.body.append(removed);
  removed.remove();
  const orphan = page.document.createTextNode("never placed");
  page.deliver("layout-shift", [
    {
      startTime: 40,
      value: 0.25,
      hadRecentInput: false,
      sources: [{ node: main }, { node: text }],
    },
  ]);
  page.deliver("layout-shift", [
    {
      startTime: 50,
      value: 0.5,
      hadRecentInput: true,
      sources: [{ node: removed }, { node: orphan }, { node: null }],
    },
  ]);
  assert.equal(page.run("markRelease"), true);
  const record = page.run("lateShifts", { heldUrls: [] });
  assert.equal(record.supported, true);
  assert.equal(typeof record.released, "number");
  assert.equal(record.presentableAt, null);
  assert.deepEqual(record.paints, [12.5]);
  assert.equal(record.blockingHeld, false);
  assert.deepEqual(record.entries, [
    {
      start: 40,
      value: 0.25,
      recentInput: false,
      sources: ["body>main:1#m", "body>main:1#m>p:1"],
    },
    {
      start: 50,
      value: 0.5,
      recentInput: true,
      sources: [
        "body>aside:1 (since removed)",
        "(a node no longer in the page)",
        "(a node no longer in the page)",
      ],
    },
  ]);
});

test("a shifted text node atop an open shadow root is named through its host", () => {
  // Its parentElement is null -- a root is not an element -- yet it is still in the page.
  const page = loadProbe({ body: `<section id="host"></section>` });
  page.run("watchShifts");
  const root = page.document
    .getElementById("host")
    .attachShadow({ mode: "open" });
  root.innerHTML = "moved<p>below</p>";
  page.deliver("layout-shift", [
    {
      startTime: 40,
      value: 0.25,
      hadRecentInput: false,
      sources: [{ node: root.firstChild }, { node: root.lastChild.firstChild }],
    },
  ]);
  const record = page.run("lateShifts", { heldUrls: [] });
  assert.deepEqual(record.entries[0].sources, [
    "body>section:1#host",
    "body>section:1#host>#shadow-root>p:1",
  ]);
});

test("a held script counts as blocking only when the engine says it blocked rendering", () => {
  const held = `${PAGE_URL}held.js`;
  const blockingHeld = (pairs) => {
    const page = loadProbe();
    page.run("watchShifts");
    page.resources(pairs);
    return page.run("lateShifts", { heldUrls: [held] }).blockingHeld;
  };
  assert.equal(blockingHeld([[held, "blocking"]]), true);
  assert.equal(blockingHeld([[held, "non-blocking"]]), false);
  assert.equal(blockingHeld([[`${PAGE_URL}other.js`, "blocking"]]), false);
});

// --- The hold on the page's scripts ---

test("the hold reads the paint and the fonts as the engine has them", () => {
  const page = loadProbe();
  assert.deepEqual(page.run("holdState"), {
    painted: false,
    fontsLoaded: false,
    blocked: false,
  });
  page.paint(3);
  page.loadFonts();
  assert.deepEqual(page.run("holdState"), {
    painted: true,
    fontsLoaded: true,
    blocked: false,
  });
});

// Each page holds one script; whether the page can paint without it.
const SCRIPTS = [
  [
    "blocking=render",
    `<script type="module" blocking="render" src="a.js"></script>`,
    "",
    true,
  ],
  [
    "render among blocking's tokens, even async",
    `<script async blocking="style render" src="a.js"></script>`,
    "",
    true,
  ],
  ["a classic <head> script", `<script src="a.js"></script>`, "", true],
  ["an async <head> script", `<script async src="a.js"></script>`, "", false],
  ["a deferred <head> script", `<script defer src="a.js"></script>`, "", false],
  [
    "a module <head> script",
    `<script type="module" src="a.js"></script>`,
    "",
    false,
  ],
  ["a classic <body> script", "", `<script src="a.js"></script>`, false],
  ["an inline <head> script", `<script>void 0;</script>`, "", false],
];

for (const [name, head, body, blocked] of SCRIPTS) {
  test(`the hold: ${name} ${blocked ? "blocks" : "does not block"} the first paint`, () => {
    assert.equal(loadProbe({ head, body }).run("holdState").blocked, blocked);
  });
}

// --- The late-shift plant ---

/** The plant laid out as it declares itself: its geometry is forced with !important. */
function plantedBox(element) {
  if (!element.hasAttribute(CONFIG.plantAttribute)) return null;
  const { left, top, width, height } = element.style;
  return rect(
    parseFloat(left),
    parseFloat(top),
    parseFloat(width),
    parseFloat(height),
  );
}

test("the plant covers the top half of the viewport, and moves down by half two frames on", async () => {
  const page = loadProbe({
    boxOf: plantedBox,
    viewport: { width: 390, height: 845 },
    scroll: { x: 10, y: 300 },
  });
  assert.equal(page.run("plantMoved"), false, "before there is a plant");
  assert.equal(page.run("plantShift"), true);
  const plant = page.document.querySelector(
    `[${CONFIG.plantAttribute}="late_shift"]`,
  );
  assert.deepEqual(
    [plant.style.left, plant.style.top, plant.style.width, plant.style.height],
    ["10px", "300px", "390px", "423px"],
  );
  await frames(page.window, 1);
  assert.equal(
    page.run("plantMoved"),
    false,
    "one frame on, it has not yet been painted where it started",
  );
  assert.equal(plant.style.top, "300px");
  await frames(page.window, 1);
  assert.equal(page.run("plantMoved"), true);
  assert.equal(plant.style.top, "723px");
  assert.equal(plant.style.getPropertyPriority("top"), "important");
});

test("a page whose policy refuses style attributes still takes the plant, and moves it", async () => {
  const page = loadProbe({ boxOf: plantedBox });
  const refused = refuseStyleAttributes(page.window);
  assert.equal(page.run("plantShift"), true);
  const plant = page.document.querySelector(
    `[${CONFIG.plantAttribute}="late_shift"]`,
  );
  assert.deepEqual(
    [plant.style.left, plant.style.top, plant.style.width, plant.style.height],
    ["0px", "0px", "390px", "422px"],
  );
  await frames(page.window, 2);
  assert.equal(page.run("plantMoved"), true);
  assert.equal(plant.style.top, "422px");
  assert.deepEqual(
    refused,
    [],
    "the plant is not drawn with a style attribute",
  );
});

test("a plant the layout gives no box reports that it did not take", () => {
  assert.equal(loadProbe({ boxOf: () => null }).run("plantShift"), false);
});
