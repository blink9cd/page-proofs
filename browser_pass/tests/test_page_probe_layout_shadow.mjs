// The in-page probe's page-load reads over shadow trees: the layout signature, the digest and the
// three element detectors read a box inside an OPEN shadow root exactly as they read the
// document's, however deeply the root is nested, and read nothing inside a closed one.
//
// WHAT THIS SUITE COVERS. Where the probe finds the elements it reads. A selector query on the
// document never crosses into a shadow root, so every read walks the document and each open root
// it finds through a host's `shadowRoot`, recursively, through the one walker
// page_probe_trees.js holds. It pins the names such an element is given (through its host, so
// the cross-engine comparison still matches rows), and the two places the detectors climb from
// an element toward <body> (the overflow exemption's scroller-or-clip search, and the inert
// check on a target), which follow the route the box is laid out by: from a root's top to its
// host, and from a slotted element to the slot it renders in. A CLOSED root's host reads a null
// `shadowRoot` and a slotted element a null `assignedSlot` there, so nothing inside one is read:
// the documented limit, pinned here so the doc cannot go stale.
//
// WHAT IS FAKED is page_probe_test_helpers.mjs's header: each test states the boxes it assumes;
// the roots, slots and the styles the detectors read are jsdom's own.
import assert from "node:assert/strict";
import test from "node:test";

import { loadProbe } from "./page_probe_test_helpers.mjs";

/**
 * A page whose <body> holds `body`, with a shadow root of `mode` attached to the element whose
 * id is `hostId` and filled with `markup`.
 * @param {string} body
 * @param {string} hostId
 * @param {"open" | "closed"} mode
 * @param {string} markup
 */
const rootedPage = (body, hostId, mode, markup) => {
  const page = loadProbe({ body });
  const root = page.document.getElementById(hostId).attachShadow({ mode });
  root.innerHTML = markup;
  return { page, root };
};

/** The name each element the digest reads is given, in the order it reads them. */
const namesOf = (page) => page.run("digest").map((row) => row[0]);

// --- Names, the digest and the signature ---

test("an element inside an open root is named through its host, after the host", () => {
  const { page } = rootedPage(
    `<section id="host"><i>light</i></section><p>after</p>`,
    "host",
    "open",
    "<div><span></span></div>",
  );
  assert.deepEqual(namesOf(page), [
    "body>section:1#host",
    "body>section:1#host>#shadow-root>div:1",
    "body>section:1#host>#shadow-root>div:1>span:1",
    "body>section:1#host>i:1",
    "body>p:1",
  ]);
});

test("one inside an open root nested in an open root is named through both hosts", () => {
  const { page, root } = rootedPage(
    `<section id="host"></section>`,
    "host",
    "open",
    "<div></div>",
  );
  root.querySelector("div").attachShadow({ mode: "open" }).innerHTML =
    "<b></b>";
  assert.deepEqual(namesOf(page), [
    "body>section:1#host",
    "body>section:1#host>#shadow-root>div:1",
    "body>section:1#host>#shadow-root>div:1>#shadow-root>b:1",
  ]);
});

test("nothing inside a closed root is read: its host alone is named", () => {
  const { page } = rootedPage(
    `<section id="host"></section>`,
    "host",
    "closed",
    "<div><span></span></div>",
  );
  assert.deepEqual(namesOf(page), ["body>section:1#host"]);
});

test("the signature counts an open root's elements and moves when a box inside one moves", () => {
  const { page, root } = rootedPage(
    `<section id="host"></section>`,
    "host",
    "open",
    `<div data-box="0 0 10 10"></div>`,
  );
  const before = page.run("signature");
  assert.equal(before[2], 2);
  root.querySelector("div").setAttribute("data-box", "4 0 10 10");
  assert.notDeepEqual(page.run("signature"), before);
});

test("the signature reads nothing inside a closed root", () => {
  const { page, root } = rootedPage(
    `<section id="host"></section>`,
    "host",
    "closed",
    `<div data-box="0 0 10 10"></div>`,
  );
  const before = page.run("signature");
  assert.equal(before[2], 1);
  root.querySelector("div").setAttribute("data-box", "4 0 10 10");
  assert.deepEqual(page.run("signature"), before);
});

// --- past_right_edge ---

test("a box inside an open root past the right edge is found, named through its host", () => {
  const { page } = rootedPage(
    `<section id="host"></section>`,
    "host",
    "open",
    `<div data-box="380 0 50 10"></div>`,
  );
  assert.deepEqual(page.run("layout").pastRightEdge, [
    "body>section:1#host>#shadow-root>div:1 right edge at 430px",
  ]);
});

test("one inside a scroller of its own root is exempt, as in the document", () => {
  const { page } = rootedPage(
    `<section id="host"></section>`,
    "host",
    "open",
    `<div style="overflow-x: auto"><p data-box="380 0 50 10"></p></div>`,
  );
  assert.deepEqual(page.run("layout").pastRightEdge, []);
});

test("one at the top of a root whose host clips is exempt: the climb goes on to the host", () => {
  const { page } = rootedPage(
    `<main style="overflow-x: hidden"><section id="host"></section></main>`,
    "host",
    "open",
    `<div data-box="380 0 50 10"></div>`,
  );
  assert.deepEqual(page.run("layout").pastRightEdge, []);
});

test("a slotted element inside the root's scroller is exempt: it climbs through its slot", () => {
  // Its light-tree parents scroll nothing, yet the box it renders in is the root's scroller.
  const { page } = rootedPage(
    `<section id="host"><p data-box="380 0 50 10"></p></section>`,
    "host",
    "open",
    `<div style="overflow-x: auto"><slot></slot></div>`,
  );
  assert.deepEqual(page.run("layout").pastRightEdge, []);
});

test("a slotted element under a closed root's scroller is still found: the limit", () => {
  // A closed root hides the slot too (`assignedSlot` reads null), so the climb stays light.
  const { page } = rootedPage(
    `<section id="host"><p data-box="380 0 50 10"></p></section>`,
    "host",
    "closed",
    `<div style="overflow-x: auto"><slot></slot></div>`,
  );
  assert.deepEqual(page.run("layout").pastRightEdge, [
    "body>section:1#host>p:1 right edge at 430px",
  ]);
});

test("a host already found stands for the boxes at the top of its root", () => {
  const { page } = rootedPage(
    `<section id="host" data-box="380 0 50 10"></section>`,
    "host",
    "open",
    `<div data-box="380 0 50 10"></div>`,
  );
  assert.deepEqual(page.run("layout").pastRightEdge, [
    "body>section:1#host right edge at 430px",
  ]);
});

// --- hidden_rendered ---

test("a hidden element inside an open root that still renders is found", () => {
  const { page } = rootedPage(
    `<section id="host"></section>`,
    "host",
    "open",
    `<p hidden style="display: block" data-box="0 0 10 10">leak</p>`,
  );
  assert.deepEqual(page.run("layout").hiddenRendered, [
    "body>section:1#host>#shadow-root>p:1",
  ]);
});

test("one inside a closed root is not: the limit", () => {
  const { page } = rootedPage(
    `<section id="host"></section>`,
    "host",
    "closed",
    `<p hidden style="display: block" data-box="0 0 10 10">leak</p>`,
  );
  assert.deepEqual(page.run("layout").hiddenRendered, []);
});

// --- small_target ---

test("a small target inside an open root is found, named through its host", () => {
  const { page } = rootedPage(
    `<section id="host"></section>`,
    "host",
    "open",
    `<button data-box="0 0 10 10">x</button>`,
  );
  assert.deepEqual(page.run("layout").smallTargets, [
    "body>section:1#host>#shadow-root>button:1 10x10",
  ]);
});

test("one inside a root whose host is inert is exempt: inertness reaches into the root", () => {
  const { page } = rootedPage(
    `<div inert><section id="host"></section></div>`,
    "host",
    "open",
    `<button data-box="0 0 10 10">x</button>`,
  );
  assert.deepEqual(page.run("layout").smallTargets, []);
});

test("a slotted target in an inert part of the root is exempt as well", () => {
  const { page } = rootedPage(
    `<section id="host"><button data-box="0 0 10 10">x</button></section>`,
    "host",
    "open",
    `<div inert><slot></slot></div>`,
  );
  assert.deepEqual(page.run("layout").smallTargets, []);
});

test("one inside a closed root is not found: the limit", () => {
  const { page } = rootedPage(
    `<section id="host"></section>`,
    "host",
    "closed",
    `<button data-box="0 0 10 10">x</button>`,
  );
  assert.deepEqual(page.run("layout").smallTargets, []);
});

// --- A root on <body>, and a slot's children under a host already found ---

test("an open root on <body> itself is read by every read, ahead of body's light children", () => {
  // The reads that start at <body> list its descendants, which leave <body> out, so the walker
  // must enter a tree's own root as well, or those reads and the ones that start at the
  // document would see different elements.
  const page = loadProbe({ body: `<p>light</p>` });
  page.document.body.attachShadow({ mode: "open" }).innerHTML =
    `<button data-box="380 0 20 10">x</button><slot></slot>`;
  assert.deepEqual(namesOf(page), [
    "body>#shadow-root>button:1",
    "body>#shadow-root>slot:1",
    "body>p:1",
  ]);
  assert.equal(page.run("signature")[2], 3);
  const layout = page.run("layout");
  assert.deepEqual(layout.pastRightEdge, [
    "body>#shadow-root>button:1 right edge at 400px",
  ]);
  assert.deepEqual(layout.smallTargets, ["body>#shadow-root>button:1 20x10"]);
});

test("a host already found stands for what is slotted into it, through its box-less slot", () => {
  // The slotted element's flat parent is the <slot>, which has no box and so is never found
  // itself: the climb passes it on to the host.
  const { page } = rootedPage(
    `<section id="host" data-box="380 0 50 10"><p data-box="380 0 50 10"></p></section>`,
    "host",
    "open",
    `<slot></slot>`,
  );
  assert.deepEqual(page.run("layout").pastRightEdge, [
    "body>section:1#host right edge at 430px",
  ]);
});

test("a found box stands for the children of a box-less wrapper between them, as well", () => {
  const { page } = rootedPage(
    `<section id="host" data-box="380 0 50 10"></section>`,
    "host",
    "open",
    `<div style="display: contents"><p data-box="380 0 50 10"></p></div>`,
  );
  assert.deepEqual(page.run("layout").pastRightEdge, [
    "body>section:1#host right edge at 430px",
  ]);
});
