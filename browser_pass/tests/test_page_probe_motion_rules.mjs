// The in-page probe's motion half as the late-script load drives it: what a CSS animation moves is
// read from the `@keyframes` rule the cascade APPLIES to it, which only the engine's style
// resolver knows, so the driver reads it over its DevTools session (pass_inert.py) and hands the
// names back.
//
// WHAT THIS SUITE COVERS. The seam on the page's side. `listMotion` lists the running animations
// into one array the page keeps, and beside each, in `asks`, what finding its rule takes -- its
// name, its target and the pseudo-element of it -- for a CSS animation whose rule could decide
// its verdict alone: one that advances with time and whose getKeyframes() names layout-inert
// properties only, or none. Every other stands beside a null, which costs the driver nothing.
// `stillMotion` then stills that SAME array, handed back as `listed`, judging each animation by
// the names beside it in `declared` together with its getKeyframes() -- Chromium leaves custom
// properties out of the latter for a CSS animation, registered or not, and a script may have
// replaced the keyframes the rule gave it. Names unknown (anything but a list) leave a CSS animation running,
// and so does having no list at all: the probe never reads a style sheet itself, since a
// same-named rule it could reach need not be the one that applies. Any other animation -- a
// transition, a script's animate() -- is read from getKeyframes() alone, which reports custom
// properties for those. How the driver reads the rule is test_pass_inert.py's.
//
// WHAT IS FAKED. The animations, as page_probe_motion_helpers.mjs's header says.
import assert from "node:assert/strict";
import test from "node:test";

import {
  ENDS,
  INERT_ONLY,
  endless,
  keyframesOf,
  pageWith,
} from "./page_probe_motion_helpers.mjs";

/** What a stilled endless animation's log reads, sent to its first iteration start at 0. */
const PAUSED_AT_0 = [["pause"], ["seek", 0]];

/** What `stillMotion` counts when it stills nothing and leaves `count` animations running. */
const runningOnly = (count) => ({ finished: 0, paused: 0, running: count });

/**
 * An endless CSS animation called `name`, reporting `reported` from getKeyframes().
 * @param {string} name
 * @param {...string} reported
 */
const css = (name, ...reported) => ({
  animationName: name,
  timing: endless(0, 1000),
  keyframes: keyframesOf(...reported),
});

/**
 * What the late-script load's settle hands `stillMotion`: the animations as the page listed
 * them, and beside each the names its applied rule declares.
 * @param {object[]} animations
 * @param {(string[] | null)[]} declared
 */
const handedBack = (animations, declared) => ({
  listed: animations,
  extra: { ...INERT_ONLY, declared },
});

/**
 * Still `animations`, each judged by its entry of `declared`, as the late-script load does.
 * @param {{run: Function}} page
 * @param {object[]} animations
 * @param {(string[] | null)[]} declared
 */
const stillListed = (page, animations, declared) => {
  const { listed, extra } = handedBack(animations, declared);
  return page.run("stillMotion", extra, listed);
};

test("the listing holds each running animation, and asks about a CSS one only where its rule could decide", () => {
  const { page, animations } = pageWith([
    { animationName: "wag", pseudoElement: "::before" },
    { animationName: "slide", keyframes: keyframesOf() },
    { keyframes: keyframesOf("opacity") },
    { animationName: "scrolled", timeline: { kind: "a scroll timeline" } },
    { animationName: "bob", keyframes: keyframesOf("transform", "marginTop") },
    { animationName: "rest", playState: "paused" },
    { animationName: "gone", timing: null },
  ]);
  const [wag, slide, faded, scrolled, bob, , gone] = animations;
  const body = page.document.body;
  const { listed, asks } = page.hold("listMotion");
  // The paused one is not listed at all; every running one is, in its order.
  assert.deepEqual([...listed], [wag, slide, faded, scrolled, bob, gone]);
  // Spread into this realm, so each ask compares as a plain record.
  assert.deepEqual(
    [...asks].map((ask) => ask && { ...ask }),
    [
      // Moving layout-inert properties alone, or naming none: the rule decides.
      { name: "wag", target: body, pseudo: "::before" },
      { name: "slide", target: body, pseudo: null },
      // A transition or a script's animation has no rule; one that does not advance with time
      // is never stilled; one whose getKeyframes() names `marginTop` is left running whatever
      // its rule adds: its rule could decide nothing.
      null,
      null,
      null,
      // A script can take the effect away; the name says what it was made from, the rest is gone.
      { name: "gone", target: null, pseudo: null },
    ],
  );
});

test("a CSS animation whose applied rule moves layout-inert properties alone is stilled", () => {
  // A keyframe may carry `animation-timing-function` and `animation-composition`, which a rule
  // lists among its declarations: when and how the keyframe applies, as getKeyframes()'s
  // `easing` and `composite` are, never what it moves.
  const { page, animations } = pageWith([css("wag", "transform")]);
  const declared = [
    [
      "transform",
      "animation-timing-function",
      "transform",
      "animation-composition",
    ],
  ];
  assert.deepEqual(stillListed(page, animations, declared), {
    finished: 0,
    paused: 1,
    running: 0,
  });
  assert.deepEqual(animations[0].log, PAUSED_AT_0);
});

test("a CSS animation whose rule declares a custom property is left running, though getKeyframes names nothing", () => {
  // THE DEFECT THE RULE IS READ FOR: an empty getKeyframes() read as moving nothing, so a custom
  // property driving `margin-top` was stilled, and its seek charged to the scripts.
  const { page, animations } = pageWith([css("drift")]);
  assert.deepEqual(stillListed(page, animations, [["--m"]]), runningOnly(1));
  assert.deepEqual(animations[0].log, []);
});

test("one name in the rule that is not layout-inert is enough to leave it running", () => {
  const { page, animations } = pageWith([css("mixed", "transform")]);
  const declared = [["transform", "--m", "transform"]];
  assert.deepEqual(stillListed(page, animations, declared), runningOnly(1));
  assert.deepEqual(animations[0].log, []);
});

test("getKeyframes() moving layout counts beside a rule of transform alone, so it is left running", () => {
  // A script can replace a CSS animation's keyframes after its rule gave them, and the rule
  // still reads as before: what getKeyframes() reports is what the animation now moves.
  const { page, animations } = pageWith([css("wag", "transform", "marginTop")]);
  assert.deepEqual(
    stillListed(page, animations, [["transform"]]),
    runningOnly(1),
  );
  assert.deepEqual(animations[0].log, []);
});

test("a CSS animation whose rule is unknown is left running, however that came about", () => {
  // No rule returned, a node that could not be resolved, a refused call: the driver hands back
  // null. A list shorter than the listing leaves the rest unknown as well.
  const { page, animations } = pageWith([
    css("missing", "transform"),
    css("short", "transform"),
  ]);
  assert.deepEqual(stillListed(page, animations, [null]), runningOnly(2));
  for (const animation of animations) {
    assert.deepEqual(animation.log, []);
  }
});

test("each verdict lands on the animation it was read for, by its place in the list", () => {
  const { page, animations } = pageWith([css("drift"), css("wag", "rotate")]);
  assert.deepEqual(stillListed(page, animations, [["--m"], ["rotate"]]), {
    finished: 0,
    paused: 1,
    running: 1,
  });
  assert.deepEqual(animations[0].log, []);
  assert.deepEqual(animations[1].log, PAUSED_AT_0);
});

test("the stilling acts on the listed animations alone: one begun since waits for the next poll", () => {
  // Scripts run between the listing and the stilling. An animation started in between has no
  // verdict beside it, so it is neither touched nor counted; the next poll lists it.
  const { page, animations } = pageWith([
    css("wag", "transform"),
    css("late", "transform"),
  ]);
  assert.deepEqual(stillListed(page, [animations[0]], [["transform"]]), {
    finished: 0,
    paused: 1,
    running: 0,
  });
  assert.deepEqual(animations[1].log, []);
});

test("a listed animation that stopped running since it was listed is left alone, uncounted", () => {
  const { page, animations } = pageWith([css("wag", "transform")]);
  const { listed, extra } = handedBack(animations, [["transform"]]);
  animations[0].playState = "paused";
  assert.deepEqual(page.run("stillMotion", extra, listed), runningOnly(0));
  assert.deepEqual(animations[0].log, []);
});

test("a transition reporting transform is stilled, with no rule to read", () => {
  // No `animationName`: not a CSS animation, so getKeyframes() is the whole answer, and the
  // null beside it says nothing.
  const { page, animations } = pageWith([
    { timing: ENDS, keyframes: keyframesOf("transform") },
  ]);
  assert.deepEqual(stillListed(page, animations, [null]), {
    finished: 1,
    paused: 0,
    running: 0,
  });
  assert.deepEqual(animations[0].log, [["finish"]]);
});

test("a script's animation reporting a custom property is left running", () => {
  // getKeyframes() names custom properties for any animation but a CSS one.
  const { page, animations } = pageWith([
    { timing: endless(0, 1000), keyframes: keyframesOf("--m") },
  ]);
  assert.deepEqual(stillListed(page, animations, [null]), runningOnly(1));
  assert.deepEqual(animations[0].log, []);
});

test("with no list handed back, no rule is read: a CSS animation is left running, a transition judged", () => {
  // An engine with no DevTools session asks for inert motion with no list. The page's own
  // sheet holds a rule of the name moving `transform` alone, and it is still not read: the
  // cascade may apply another, where no page script can reach.
  const { page, animations } = pageWith(
    [
      css("wag", "transform"),
      { timing: ENDS, keyframes: keyframesOf("opacity") },
    ],
    {
      head: "<style>@keyframes wag { to { transform: rotate(5deg); } }</style>",
    },
  );
  assert.deepEqual(page.run("stillMotion", INERT_ONLY), {
    finished: 1,
    paused: 0,
    running: 1,
  });
  assert.deepEqual(animations[0].log, []);
  assert.deepEqual(animations[1].log, [["finish"]]);
});
