// The in-page probe's motion half over shadow trees: an animation whose target lives INSIDE a
// shadow root is stilled and counted exactly as the document's is, wherever the page can reach
// that root -- and nowhere it cannot.
//
// WHAT THIS SUITE COVERS. Where the probe finds its animations. An engine's document lists only
// its own tree's animations: one targeting an element inside a shadow root is listed by THAT
// root's getAnimations() alone, and a root nested in another by the inner root alone. So both
// settles read the document's list and every OPEN root's, found recursively through each host's
// `shadowRoot`; each animation is in exactly one list, so nothing is counted twice. A CLOSED
// root's host reads a null `shadowRoot`, so its motion, and that of any root inside it, is
// neither stilled nor counted: the documented limit, pinned here so the doc cannot go stale.
// The late-script load lists a root's animation and asks about its rule by its target inside
// the root, then stills it by the verdict beside it, as it does the document's. What each
// animation's state and properties decide is test_page_probe_motion.mjs's and
// test_page_probe_motion_rules.mjs's: a root's animation goes through those same verdicts.
//
// WHAT IS FAKED. The animations, the document's and each root's, as
// page_probe_motion_helpers.mjs's header says; the roots themselves are jsdom's own.
import assert from "node:assert/strict";
import test from "node:test";

import {
  ENDS,
  INERT_ONLY,
  endless,
  keyframesOf,
  pageWith,
  rootWith,
} from "./page_probe_motion_helpers.mjs";

/** What a stilled endless animation's log reads, sent to its first iteration start at 0. */
const PAUSED_AT_0 = [["pause"], ["seek", 0]];

/** An endless animation from local time 0, moving `transform` alone. */
const ENDLESS = Object.freeze({ timing: endless(0, 1000) });

/**
 * A page whose <body> holds the hosts `ids` name, one <section> each, with `specs` animating
 * the document itself.
 * @param {string[]} ids
 * @param {object[]} [specs]
 */
const pageHosting = (ids, specs = []) =>
  pageWith(specs, {
    body: ids.map((id) => `<section id="${id}"></section>`).join(""),
  });

test("an animation inside an open shadow root is stilled and counted beside the document's", () => {
  const { page, animations } = pageHosting(["host"], [ENDLESS]);
  const host = page.document.getElementById("host");
  const { animations: rooted } = rootWith(host, "open", [
    ENDLESS,
    { timing: ENDS },
  ]);
  assert.deepEqual(page.run("stillMotion"), {
    finished: 1,
    paused: 2,
    running: 0,
  });
  assert.deepEqual(animations[0].log, PAUSED_AT_0);
  assert.deepEqual(rooted[0].log, PAUSED_AT_0);
  assert.deepEqual(rooted[1].log, [["finish"]]);
});

test("one in an open root nested in an open root is stilled too: each root lists its own alone", () => {
  // The outer root lists nothing; only the walk into its <div>'s own root finds the motion.
  const { page } = pageHosting(["host"]);
  const outer = rootWith(page.document.getElementById("host"), "open", []);
  const inner = rootWith(outer.target, "open", [ENDLESS]);
  assert.deepEqual(page.run("stillMotion"), {
    finished: 0,
    paused: 1,
    running: 0,
  });
  assert.deepEqual(inner.animations[0].log, PAUSED_AT_0);
});

test("a root's animation goes through the verdicts the document's does", () => {
  // Not running, not advancing with time: left alone, or left running and counted so.
  const { page } = pageHosting(["host"]);
  const { animations } = rootWith(
    page.document.getElementById("host"),
    "open",
    [
      { playState: "paused", timing: endless(0, 1000) },
      { timeline: { kind: "a scroll timeline" }, timing: endless(0, 1000) },
    ],
  );
  assert.deepEqual(page.run("stillMotion"), {
    finished: 0,
    paused: 0,
    running: 1,
  });
  for (const animation of animations) {
    assert.deepEqual(animation.log, []);
  }
});

test("motion inside a CLOSED root, or a root nested in one, is neither stilled nor counted", () => {
  // THE DOCUMENTED LIMIT. An engine's closed root lists its own animations, but its host's
  // `shadowRoot` reads null to the page, so the probe cannot reach it, nor any root inside it.
  // An open root beside it is still reached.
  const { page } = pageHosting(["closed", "open"]);
  const closed = rootWith(page.document.getElementById("closed"), "closed", [
    ENDLESS,
  ]);
  const hidden = rootWith(closed.target, "open", [ENDLESS]);
  const open = rootWith(page.document.getElementById("open"), "open", [
    ENDLESS,
  ]);
  assert.equal(page.document.getElementById("closed").shadowRoot, null);
  assert.deepEqual(page.run("stillMotion"), {
    finished: 0,
    paused: 1,
    running: 0,
  });
  assert.deepEqual(closed.animations[0].log, []);
  assert.deepEqual(hidden.animations[0].log, []);
  assert.deepEqual(open.animations[0].log, PAUSED_AT_0);
});

// --- The late-script load: the listing, and the stilling of that same list. ---

test("the listing holds an open root's running animation, and asks about it by its target inside the root", () => {
  const { page, animations } = pageHosting(
    ["host"],
    [{ animationName: "slide" }],
  );
  const { target, animations: rooted } = rootWith(
    page.document.getElementById("host"),
    "open",
    [
      { animationName: "wag", timing: endless(0, 1000) },
      { animationName: "rest", playState: "paused" },
      { keyframes: keyframesOf("opacity") },
    ],
  );
  const { listed, asks } = page.hold("listMotion");
  // The document's first, then each root's, in tree order; the paused one not at all.
  assert.deepEqual([...listed], [animations[0], rooted[0], rooted[2]]);
  assert.deepEqual(
    [...asks].map((ask) => ask && { ...ask }),
    [
      { name: "slide", target: page.document.body, pseudo: null },
      // The element inside the root: what the driver resolves to a node to read its rule.
      { name: "wag", target, pseudo: null },
      // A script's animation has no rule to read.
      null,
    ],
  );
});

test("the late load stills a listed root's animation by the verdict beside it, and no other", () => {
  // 203's rule holds inside a root: only PROVABLY layout-inert motion is stilled, and a root's
  // animation whose rule moves layout is left running and counted, as a document one is.
  const { page } = pageHosting(["host"]);
  const { animations } = rootWith(
    page.document.getElementById("host"),
    "open",
    [
      { animationName: "wag", timing: endless(0, 1000) },
      {
        animationName: "drift",
        timing: endless(0, 1000),
        keyframes: keyframesOf(),
      },
    ],
  );
  const { listed } = page.hold("listMotion");
  const declared = [["transform"], ["--m"]];
  assert.deepEqual(
    page.run("stillMotion", { ...INERT_ONLY, declared }, listed),
    { finished: 0, paused: 1, running: 1 },
  );
  assert.deepEqual(animations[0].log, PAUSED_AT_0);
  assert.deepEqual(animations[1].log, []);
});

test("with no list handed back, a root's motion is still judged by its keyframes alone", () => {
  // An engine with no DevTools session asks for inert motion with no list: the probe finds a
  // root's animations itself, as it finds the document's.
  const { page } = pageHosting(["host"]);
  const { animations } = rootWith(
    page.document.getElementById("host"),
    "open",
    [
      { timing: ENDS, keyframes: keyframesOf("transform") },
      { timing: endless(0, 1000), keyframes: keyframesOf("height") },
    ],
  );
  assert.deepEqual(page.run("stillMotion", INERT_ONLY), {
    finished: 1,
    paused: 0,
    running: 1,
  });
  assert.deepEqual(animations[0].log, [["finish"]]);
  assert.deepEqual(animations[1].log, []);
});
