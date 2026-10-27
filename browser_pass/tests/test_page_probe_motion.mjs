// The in-page probe's motion half (both loads' settles): every animation that moves with time
// is stilled before each poll, so paced or endless motion is measured at rest rather than in its
// first pause -- and in the late-script load, only motion that moves no layout.
//
// WHAT THIS SUITE COVERS. Which animations `stillMotion` touches, what it does to each, and how
// it counts them. One the page paused, one already finished and one never started are what a
// visitor sees: left alone, and not counted. One on another timeline or at a zero rate does not
// advance with time: left running, and counted as running. One that ends is finished. One that
// never ends is paused, then sent to its first iteration start at or after local time 0. Asked
// for layout-inert motion alone (`layoutInertOnly`, the late-script load), one moving any other
// property, or naming none, is left running too, and counted as running. What a CSS animation
// moves -- the names its applied `@keyframes` rule declares, which the late-script load's driver
// hands back -- is test_page_probe_motion_rules.mjs's.
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

test("a page with no animations stills nothing and counts nothing", () => {
  const { page } = pageWith([]);
  assert.deepEqual(page.run("stillMotion"), {
    finished: 0,
    paused: 0,
    running: 0,
  });
});

test("an animation the page paused, one finished and one never started are left alone", () => {
  const { page, animations } = pageWith([
    { playState: "paused", timing: endless(0, 1000) },
    { playState: "finished" },
    { playState: "idle" },
  ]);
  assert.deepEqual(page.run("stillMotion"), {
    finished: 0,
    paused: 0,
    running: 0,
  });
  for (const animation of animations) {
    assert.deepEqual(animation.log, [], animation.playState);
    assert.equal(animation.currentTime, 250);
  }
});

test("one on another timeline is left running, and counted as running", () => {
  // A scroll-driven timeline advances with scrolling, not with time: waiting cannot end it.
  const { page, animations } = pageWith([
    { timeline: { kind: "a scroll timeline" }, timing: endless(0, 1000) },
  ]);
  assert.deepEqual(page.run("stillMotion"), {
    finished: 0,
    paused: 0,
    running: 1,
  });
  assert.deepEqual(animations[0].log, []);
  assert.equal(animations[0].playState, "running");
});

test("one at a zero rate is left running, and counted as running", () => {
  const { page, animations } = pageWith([{ playbackRate: 0 }]);
  assert.deepEqual(page.run("stillMotion"), {
    finished: 0,
    paused: 0,
    running: 1,
  });
  assert.deepEqual(animations[0].log, []);
});

test("one that ends is finished, so its end state is what is measured", () => {
  const { page, animations } = pageWith([
    { timing: { delay: 6000, duration: 6000, endTime: 12000 } },
  ]);
  assert.deepEqual(page.run("stillMotion"), {
    finished: 1,
    paused: 0,
    running: 0,
  });
  assert.deepEqual(animations[0].log, [["finish"]]);
});

test("one with no effect ends at once, and is finished like any that ends", () => {
  const { page, animations } = pageWith([{ timing: null }]);
  assert.deepEqual(page.run("stillMotion"), {
    finished: 1,
    paused: 0,
    running: 0,
  });
  assert.deepEqual(animations[0].log, [["finish"]]);
});

test("an endless one with a delay is paused, then sent to the delay: its first iteration start", () => {
  const { page, animations } = pageWith([{ timing: endless(6000, 1200) }]);
  assert.deepEqual(page.run("stillMotion"), {
    finished: 0,
    paused: 1,
    running: 0,
  });
  assert.deepEqual(animations[0].log, [["pause"], ["seek", 6000]]);
});

test("an endless one begun before time 0 is sent to its next iteration start after it", () => {
  const { page, animations } = pageWith([
    { timing: endless(-2500, 1000) },
    { timing: endless(-2000, 1000) },
  ]);
  assert.deepEqual(page.run("stillMotion"), {
    finished: 0,
    paused: 2,
    running: 0,
  });
  // Three iterations began before 0, so the next starts at 500ms; exactly two began before 0
  // in the second, whose third starts AT 0 -- at or after, so that one.
  assert.deepEqual(animations[0].log, [["pause"], ["seek", 500]]);
  assert.deepEqual(animations[1].log, [["pause"], ["seek", 0]]);
});

test("one endless iteration a script made is sent to its delay or to 0, never to a NaN time", () => {
  // Its one iteration never ends, so none is skipped, and 0 * Infinity -- NaN, which an engine
  // refuses as a time -- must not be what the seek is handed. Begun before 0, it has no
  // iteration start at or after 0; at 0 it shows its first frame, as it does at every time.
  const { page, animations } = pageWith([
    { timing: endless(300, Infinity) },
    { timing: endless(-300, Infinity) },
  ]);
  assert.equal(page.run("stillMotion").paused, 2);
  assert.deepEqual(animations[0].log, [["pause"], ["seek", 300]]);
  assert.deepEqual(animations[1].log, [["pause"], ["seek", 0]]);
});

test("every class counts at once, and a second call finds nothing left to still", () => {
  const { page } = pageWith([
    {},
    { timing: endless(0, 800) },
    { playbackRate: 0 },
    { playState: "paused" },
    {},
  ]);
  assert.deepEqual(page.run("stillMotion"), {
    finished: 2,
    paused: 1,
    running: 1,
  });
  // What was stilled is no longer running; what was left running still is.
  assert.deepEqual(page.run("stillMotion"), {
    finished: 0,
    paused: 0,
    running: 1,
  });
});

// --- Layout-inert motion alone: the late-script load's settle. ---

test("asked for inert motion alone, an endless transform is still paused at its iteration start", () => {
  const { page, animations } = pageWith([
    { timing: endless(-2500, 1000), keyframes: keyframesOf("transform") },
  ]);
  assert.deepEqual(page.run("stillMotion", INERT_ONLY), {
    finished: 0,
    paused: 1,
    running: 0,
  });
  assert.deepEqual(animations[0].log, [["pause"], ["seek", 500]]);
});

test("asked for inert motion alone, a finite opacity one is still finished", () => {
  const { page, animations } = pageWith([
    { timing: ENDS, keyframes: keyframesOf("opacity") },
  ]);
  assert.deepEqual(page.run("stillMotion", INERT_ONLY), {
    finished: 1,
    paused: 0,
    running: 0,
  });
  assert.deepEqual(animations[0].log, [["finish"]]);
});

test("every layout-inert property is stilled, one at a time and all together", () => {
  // Each moves or fades a box over its laid-out place, never the place itself.
  const inert = ["transform", "translate", "rotate", "scale", "opacity"];
  const { page, animations } = pageWith([
    ...inert.map((name) => ({
      timing: endless(0, 1000),
      keyframes: keyframesOf(name),
    })),
    { timing: endless(0, 1000), keyframes: keyframesOf(...inert) },
  ]);
  assert.deepEqual(page.run("stillMotion", INERT_ONLY), {
    finished: 0,
    paused: inert.length + 1,
    running: 0,
  });
  for (const animation of animations) {
    assert.deepEqual(animation.log, [["pause"], ["seek", 0]]);
  }
});

test("asked for inert motion alone, an endless margin-top one is untouched and counted running", () => {
  // Seeking it would move the layout: a layout shift the late-script load would charge to the
  // scripts. Left to run, it keeps that settle from settling, and the page is refused.
  const { page, animations } = pageWith([
    { timing: endless(0, 1000), keyframes: keyframesOf("marginTop") },
  ]);
  assert.deepEqual(page.run("stillMotion", INERT_ONLY), {
    finished: 0,
    paused: 0,
    running: 1,
  });
  assert.deepEqual(animations[0].log, []);
  assert.equal(animations[0].playState, "running");
  assert.equal(animations[0].currentTime, 250);
});

test("asked for inert motion alone, one moving a transform AND a margin is untouched", () => {
  // One layout-moving property is enough: the seek would move that property with the rest.
  const { page, animations } = pageWith([
    { timing: ENDS, keyframes: keyframesOf("transform", "marginTop") },
    { timing: endless(0, 1000), keyframes: keyframesOf("opacity", "width") },
  ]);
  assert.deepEqual(page.run("stillMotion", INERT_ONLY), {
    finished: 0,
    paused: 0,
    running: 2,
  });
  for (const animation of animations) {
    assert.deepEqual(animation.log, []);
  }
});

test("asked for inert motion alone, one naming no property is left running, and counted", () => {
  // An empty list cannot be told from one an engine emptied: Chromium leaves custom properties
  // out of a CSS animation's getKeyframes(). And one that truly moves nothing never changes the
  // layout signature, so leaving it running costs the settle nothing.
  const { page, animations } = pageWith([
    { timing: null },
    { timing: ENDS, keyframes: keyframesOf() },
    { timing: endless(6000, 1200), keyframes: keyframesOf() },
  ]);
  assert.deepEqual(page.run("stillMotion", INERT_ONLY), {
    finished: 0,
    paused: 0,
    running: 3,
  });
  for (const animation of animations) {
    assert.deepEqual(animation.log, []);
  }
});

test("asked for inert motion alone, what does not advance with time is still left, and counted", () => {
  const { page, animations } = pageWith([
    { timeline: { kind: "a scroll timeline" }, timing: endless(0, 1000) },
    { playbackRate: 0 },
    { playState: "paused", keyframes: keyframesOf("marginTop") },
  ]);
  assert.deepEqual(page.run("stillMotion", INERT_ONLY), {
    finished: 0,
    paused: 0,
    running: 2,
  });
  for (const animation of animations) {
    assert.deepEqual(animation.log, []);
  }
});

test("without the option, layout-moving motion is stilled like any other", () => {
  // The page-load settle counts no layout shift, so it stills everything that advances.
  const { page, animations } = pageWith([
    { timing: endless(0, 1000), keyframes: keyframesOf("marginTop") },
    { timing: ENDS, keyframes: keyframesOf("transform", "width") },
  ]);
  assert.deepEqual(page.run("stillMotion"), {
    finished: 1,
    paused: 1,
    running: 0,
  });
  assert.deepEqual(animations[0].log, [["pause"], ["seek", 0]]);
  assert.deepEqual(animations[1].log, [["finish"]]);
});

test("asked for inert motion alone, a second call stills nothing more and still counts the rest", () => {
  const { page } = pageWith([
    { timing: endless(0, 800), keyframes: keyframesOf("rotate") },
    { timing: endless(0, 800), keyframes: keyframesOf("top") },
  ]);
  assert.deepEqual(page.run("stillMotion", INERT_ONLY), {
    finished: 0,
    paused: 1,
    running: 1,
  });
  assert.deepEqual(page.run("stillMotion", INERT_ONLY), {
    finished: 0,
    paused: 0,
    running: 1,
  });
});
