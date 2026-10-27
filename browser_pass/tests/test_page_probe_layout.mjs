// The in-page probe's page-load half: the four layout detectors, the fault plants that prove
// them, and the reads the driver polls (state, signature, digest).
//
// WHAT THIS SUITE COVERS. The decisions each detector makes over a layout: what counts as past
// the edge (a half-pixel tolerance, empty boxes, scrollers, <body> not counting as one, a
// flagged parent standing for its descendants), what counts as a visible leak (until-found
// and a visibility-hidden reserve do not), and what counts as a target too small to tap
// (inline links, visually-hidden boxes, disabled and inert controls do not). It also pins the
// names the probe gives elements, since the cross-engine comparison matches rows by them.
//
// WHAT IS FAKED is page_probe_test_helpers.mjs's header: each test states the boxes it
// assumes, while the styles those decisions read come from jsdom's real cascade. Whether an
// engine reports the boxes assumed here is the live suites' question, not this one's.
import assert from "node:assert/strict";
import test from "node:test";

import {
  CONFIG,
  loadProbe,
  rect,
  refuseStyleAttributes,
  VIEWPORT,
} from "./page_probe_test_helpers.mjs";

/** The detectors' findings over a page. */
const layoutOf = (options) => loadProbe(options).run("layout");

/** The name each element in the page is given, in document order. */
const namesIn = (options) =>
  loadProbe(options)
    .run("digest")
    .map((row) => row[0]);

// --- Names and the digest ---

test("an element is named by tag, same-tag position and id, from <body> down", () => {
  assert.deepEqual(
    namesIn({ body: `<main id="top"><p>a</p><div>b</div><p>c</p></main>` }),
    [
      "body>main:1#top",
      "body>main:1#top>p:1",
      "body>main:1#top>div:1",
      "body>main:1#top>p:2",
    ],
  );
});

test("a planted element's name carries the plant mark and its detector", () => {
  assert.deepEqual(
    namesIn({ body: `<div ${CONFIG.plantAttribute}="small_target"></div>` }),
    [`body>div:1${CONFIG.plantMark}small_target`],
  );
});

test("the digest records box, document position, size to the hundredth and font size", () => {
  const page = loadProbe({
    head: "<style>p { font-size: 12.5px }</style>",
    body: `<p data-box="10.126 20.444 30.5549 40">laid out</p><span>no box</span>`,
    scroll: { x: 5, y: 100 },
  });
  assert.deepEqual(page.run("digest"), [
    ["body>p:1", 1, 15.13, 120.44, 30.55, 40, 12.5],
    ["body>span:1", 0, 5, 100, 0, 0, 16],
  ]);
});

test("a font size that is not a number digests as 0, never as NaN", () => {
  // NaN would reach the driver as null. No engine has been seen reporting one, so the engine's
  // answer is stated here rather than produced by any CSS.
  const page = loadProbe({ body: "<p>text</p>" });
  const cascade = page.window.getComputedStyle.bind(page.window);
  page.window.getComputedStyle = (element) =>
    new Proxy(cascade(element), {
      get: (style, name) => (name === "fontSize" ? "" : style[name]),
    });
  assert.equal(page.run("digest")[0][6], 0);
});

// --- page_overflow ---

test("overflow is how far the page is wider than its viewport, never negative", () => {
  assert.equal(
    layoutOf({ root: { scrollWidth: 450, clientWidth: 390 } }).overflow,
    60,
  );
  assert.equal(
    layoutOf({ root: { scrollWidth: 380, clientWidth: 390 } }).overflow,
    0,
  );
});

// --- past_right_edge ---

test("a box past the viewport's right edge is named, its edge rounded to the pixel", () => {
  const { pastRightEdge } = layoutOf({
    body: `<div data-box="0 0 420.4 10"></div>`,
  });
  assert.deepEqual(pastRightEdge, ["body>div:1 right edge at 420px"]);
});

test("an edge within half a pixel of the viewport's is rounding, not overflow", () => {
  const { pastRightEdge } = layoutOf({
    body: `<div data-box="0 0 390.5 10"></div><p data-box="0 0 390.6 10"></p>`,
  });
  assert.deepEqual(pastRightEdge, ["body>p:1 right edge at 391px"]);
});

test("an empty box past the edge is not overflow", () => {
  const { pastRightEdge } = layoutOf({
    body: `<div data-box="400 0 0 10"></div><div data-box="400 0 10 0"></div>`,
  });
  assert.deepEqual(pastRightEdge, []);
});

test("a box nobody can see is not overflow", () => {
  const { pastRightEdge } = layoutOf({
    head: "<style>.ghost { visibility: hidden }</style>",
    body: `<div class="ghost" data-box="0 0 500 10"></div><div hidden data-box="0 0 500 10"></div>`,
  });
  assert.deepEqual(pastRightEdge, []);
});

test("content past the edge inside a scroller or a clip is there by design", () => {
  const { pastRightEdge } = layoutOf({
    head: "<style>.shelf { overflow-x: auto } .clip { overflow-x: hidden }</style>",
    body:
      `<div class="shelf" data-box="0 0 390 100"><div><p data-box="400 0 100 10">slide</p></div></div>` +
      `<div class="clip" data-box="0 0 390 10"><p data-box="400 0 100 10">cut</p></div>`,
  });
  assert.deepEqual(pastRightEdge, []);
});

test("<body> clipping content does not excuse it: nobody can reach what it clips", () => {
  const { pastRightEdge } = layoutOf({
    head: "<style>body { overflow-x: hidden }</style>",
    body: `<div data-box="0 0 500 10"></div>`,
  });
  assert.deepEqual(pastRightEdge, ["body>div:1 right edge at 500px"]);
});

test("a flagged box stands for its descendants, but not for a box under an unflagged one", () => {
  const { pastRightEdge } = layoutOf({
    body:
      `<div data-box="0 0 500 10"><p data-box="0 0 500 10"><span data-box="0 0 500 10"></span></p></div>` +
      `<section data-box="0 0 100 10"><p data-box="0 0 450 10"></p></section>`,
  });
  assert.deepEqual(pastRightEdge, [
    "body>div:1 right edge at 500px",
    "body>section:1>p:1 right edge at 450px",
  ]);
});

// --- hidden_rendered ---
//
// An engine ranks every page rule above its UA sheet's `[hidden]:not([hidden=until-found i])
// :not(embed) { display: none }` by origin. jsdom ranks the two together by specificity, where
// that rule's 0,2,1 beats a class rule, so the page rules below that show a hidden element are
// id rules: the one kind that outranks it in jsdom, as any page rule does in an engine.

test("a hidden element a page rule shows is a visible leak", () => {
  const { hiddenRendered } = layoutOf({
    head: "<style>#leak { display: block }</style>",
    body: `<p hidden id="leak" data-box="0 0 100 10">leak</p><p hidden data-box="0 0 100 10">hidden</p>`,
  });
  assert.deepEqual(hiddenRendered, ["body>p:1#leak"]);
});

test("until-found and a visibility-hidden reserve render a box by design", () => {
  const { hiddenRendered } = layoutOf({
    head: "<style>#reserve { display: block; visibility: hidden }</style>",
    body:
      `<p hidden="until-found" data-box="0 0 100 10">found</p>` +
      `<p hidden id="reserve" data-box="0 0 100 10">reserved</p>`,
  });
  assert.deepEqual(hiddenRendered, []);
});

// --- small_target ---

test("a target under 24px on its short side is named with its size to a tenth", () => {
  const { smallTargets } = layoutOf({
    body: `<button data-box="0 0 12.34 40">x</button><button data-box="0 0 24 24">y</button>`,
  });
  assert.deepEqual(smallTargets, ["body>button:1 12.3x40"]);
});

test("every kind of target is measured, and nothing else is", () => {
  // jsdom's UA sheet lays <select> and <textarea> out inline; an engine's, inline-block.
  const small = `data-box="0 0 10 10"`;
  const { smallTargets } = layoutOf({
    head: "<style>select, textarea { display: inline-block } .block { display: block }</style>",
    body:
      `<a href="#" class="block" ${small}>a</a><button ${small}>b</button><input ${small}>` +
      `<select ${small}></select><textarea ${small}></textarea>` +
      `<details><summary ${small}>s</summary></details>` +
      `<div role="button" ${small}></div><div role="link" ${small}></div><div tabindex="0" ${small}></div>` +
      `<a class="block" ${small}>no href</a><input type="hidden" class="block" ${small}>` +
      `<div tabindex="-1" ${small}></div><div ${small}></div>`,
  });
  assert.deepEqual(
    smallTargets.map((finding) => finding.split(" ")[0]),
    [
      "body>a:1",
      "body>button:1",
      "body>input:1",
      "body>select:1",
      "body>textarea:1",
      "body>details:1>summary:1",
      "body>div:1",
      "body>div:2",
      "body>div:3",
    ],
  );
});

test("a link inside a sentence is exempt, as WCAG exempts it", () => {
  const { smallTargets } = layoutOf({
    body: `<p data-box="0 0 300 20">Read <a href="#" data-box="40 0 30 16">this</a>.</p>`,
  });
  assert.deepEqual(smallTargets, []);
});

test("a visually-hidden box is not a target anyone taps", () => {
  const { smallTargets } = layoutOf({
    body:
      `<button data-box="0 0 1 1">a</button><button data-box="0 0 1 20">b</button>` +
      `<button data-box="0 0 20 1">c</button>`,
  });
  assert.deepEqual(smallTargets, []);
});

test("a disabled, inert or unseen target is not measured", () => {
  const small = `data-box="0 0 10 10"`;
  const { smallTargets } = layoutOf({
    head: "<style>.ghost { visibility: hidden }</style>",
    body:
      `<button disabled ${small}>a</button><div inert><button ${small}>b</button></div>` +
      `<button class="ghost" ${small}>c</button>`,
  });
  assert.deepEqual(smallTargets, []);
});

// --- The plants ---

/**
 * The planted boxes as an engine lays them out. Each plant declares its own geometry and forces
 * it with !important, so its box is what it declares; the hidden leak declares no size, and as
 * a block of one line of text it spans the viewport.
 */
function plantedBox(element) {
  if (!element.hasAttribute(CONFIG.plantAttribute)) return null;
  const { left, top, width, height } = element.style;
  const px = (value, fallback) => (value === "" ? fallback : parseFloat(value));
  return rect(
    px(left, 0),
    px(top, 0),
    px(width, VIEWPORT.width),
    px(height, 20),
  );
}

test("each plant takes, and says so independently of the detector meant to catch it", async () => {
  const page = loadProbe({ boxOf: plantedBox, root: { scrollWidth: 450 } });
  assert.deepEqual(page.run("plant"), {
    page_overflow: true,
    past_right_edge: true,
    hidden_rendered: true,
    small_target: true,
    console_error: true,
    page_error: true,
    http_error: true,
  });
  assert.equal(
    page.document.querySelector("img").getAttribute("src"),
    CONFIG.missingPath,
  );
  assert.deepEqual(page.errors.console, [CONFIG.consoleText]);
  await new Promise((resolve) => page.window.setTimeout(resolve, 10));
  assert.equal(page.errors.page.length, 1);
  assert.match(page.errors.page[0], new RegExp(CONFIG.pageErrorText));
});

test("the plants sit past the page and the viewport, forced past any author rule", () => {
  const page = loadProbe({ boxOf: plantedBox, root: { scrollWidth: 450 } });
  page.run("plant");
  const planted = [
    ...page.document.querySelectorAll(`[${CONFIG.plantAttribute}]`),
  ];
  assert.deepEqual(
    planted.map((element) => element.getAttribute(CONFIG.plantAttribute)),
    ["page_overflow", "past_right_edge", "hidden_rendered", "small_target"],
  );
  for (const element of planted) {
    // Read back as the page's CSSOM serializes them, however they were written.
    const declarations = element.style.cssText
      .split(";")
      .map((declaration) => declaration.trim())
      .filter(Boolean);
    assert.notEqual(
      declarations.length,
      0,
      "the plant declares how it is drawn",
    );
    for (const declaration of declarations) {
      assert.match(declaration, / !important$/);
    }
  }
  assert.equal(planted[0].style.left, "500px", "the page's width plus 50");
  assert.equal(planted[1].style.left, "400px", "the viewport's width plus 10");
  assert.equal(planted[2].hidden, true);
});

test("a page whose policy refuses style attributes still takes every plant", () => {
  const page = loadProbe({ boxOf: plantedBox, root: { scrollWidth: 450 } });
  const refused = refuseStyleAttributes(page.window);
  assert.deepEqual(page.run("plant"), {
    page_overflow: true,
    past_right_edge: true,
    hidden_rendered: true,
    small_target: true,
    console_error: true,
    page_error: true,
    http_error: true,
  });
  assert.deepEqual(refused, [], "no plant is drawn with a style attribute");
});

test("a plant the layout did not honor reports that it did not take", () => {
  // Every plant laid out as a line across the top of the viewport, its declarations ignored.
  const page = loadProbe({
    boxOf: (element) =>
      element.hasAttribute(CONFIG.plantAttribute)
        ? rect(0, 0, VIEWPORT.width, 20)
        : null,
  });
  assert.deepEqual(page.run("plant"), {
    page_overflow: false,
    past_right_edge: false,
    hidden_rendered: true,
    small_target: false,
    console_error: true,
    page_error: true,
    http_error: true,
  });
});

test("a plant with no box at all reports that it did not take", () => {
  const taken = loadProbe({ boxOf: () => null }).run("plant");
  assert.equal(taken.page_overflow, false);
  assert.equal(taken.hidden_rendered, false);
  assert.equal(taken.small_target, false);
});

// --- The reads the driver polls ---

test("state reports the viewport and each media feature the emulation sets", () => {
  const page = loadProbe({
    viewport: { width: 844, height: 390 },
    root: { clientWidth: 829 },
    matching: ["(prefers-color-scheme: dark)", "(orientation: landscape)"],
  });
  assert.deepEqual(page.run("state"), {
    innerWidth: 844,
    innerHeight: 390,
    clientWidth: 829,
    scriptingNone: false,
    dark: true,
    landscape: true,
    reducedMotion: false,
  });
  const other = loadProbe({
    matching: ["(scripting: none)", "(prefers-reduced-motion: reduce)"],
  }).run("state");
  assert.equal(other.scriptingNone, true);
  assert.equal(other.reducedMotion, true);
  assert.equal(other.dark, false);
});

test("the signature moves with any box, the page's size, its element count and its fonts", () => {
  const page = loadProbe({
    body: `<p data-box="1.6 2 3 4"></p><p data-box="10 20 30 40"></p>`,
    root: { scrollWidth: 400, scrollHeight: 900 },
  });
  // x + 3y + 5w + 7h, each rounded, summed over the boxes: (2+6+15+28) + (10+60+150+280).
  assert.deepEqual(page.run("signature"), [400, 900, 2, "loading", 551]);
  page.loadFonts();
  assert.equal(page.run("signature")[3], "loaded");
});

test("the signature reads a page that has no <body> yet", () => {
  const page = loadProbe();
  page.document.body.remove();
  assert.deepEqual(page.run("signature"), [
    VIEWPORT.width,
    VIEWPORT.height,
    0,
    "loading",
    0,
  ]);
});
