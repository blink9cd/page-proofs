// The stand-ins the probe's motion suites share: animations as the probe reads them, a page
// whose document holds them, and shadow roots holding their own.
//
// WHAT IS FAKED, AND WHY. jsdom implements no Web Animations API, so a page stands in
// `document.getAnimations` and `document.timeline` with animation objects built here, each
// recording the calls the probe makes on it in order. A CSS animation is one carrying an
// `animationName`, as an engine's CSSAnimation does; its effect's target is the page's <body>,
// and it targets no pseudo-element, unless a test says otherwise. A shadow root stands in its
// own `getAnimations` the same way (`rootWith`): an engine's root lists the animations of the
// elements inside it, which its document's list leaves out, so each tree's list is its own. What
// the rule applied to a CSS animation declares is never read in the page: the late-script load's
// driver reads it from the engine's style resolver and hands it back, so a test hands it back
// itself.
//
// WHAT IS REAL. The shadow roots themselves (jsdom attaches them, open and closed, and a
// closed one's host reads a null `shadowRoot`, as an engine's does), and the rest, as
// page_probe_test_helpers.mjs's header says.
import { loadProbe } from "./page_probe_test_helpers.mjs";

/** The computed timing of an animation that ends: one second, no delay. */
export const ENDS = Object.freeze({ delay: 0, duration: 1000, endTime: 1000 });

/**
 * The computed timing of an animation that never ends: iterations of `duration` ms from `delay`.
 * @param {number} delay
 * @param {number} duration
 */
export const endless = (delay, duration) => ({
  delay,
  duration,
  endTime: Infinity,
});

/**
 * Keyframes as an engine's getKeyframes() reports them: a start and an end, each carrying the
 * keys that say when and how it applies beside a value for every property it animates.
 * @param {...string} properties - Each animated property, spelled as getKeyframes spells it.
 */
export const keyframesOf = (...properties) =>
  [0, 1].map((offset) => ({
    offset,
    computedOffset: offset,
    easing: "linear",
    composite: "auto",
    ...Object.fromEntries(properties.map((name) => [name, "initial"])),
  }));

/** What the late-script load asks of `stillMotion`. */
export const INERT_ONLY = Object.freeze({ layoutInertOnly: true });

/**
 * An animation as the probe reads one. Every call the probe makes on it (a finish, a pause, a
 * seek) is recorded in `log`, in order, and a finish or a pause changes its play state as an
 * engine's would.
 * @param {object} options
 * @param {object} options.timeline - The timeline it runs on.
 * @param {Element | null} [options.target] - Its effect's target.
 * @param {string | null} [options.pseudoElement] - The pseudo-element of it the effect targets.
 * @param {string} [options.animationName] - Set for a CSS animation alone, as an engine sets it.
 * @param {string} [options.playState]
 * @param {number} [options.playbackRate]
 * @param {object | null} [options.timing] - Its computed timing; null for no effect at all.
 * @param {object[]} [options.keyframes] - What its effect's getKeyframes() reports.
 */
export function fakeAnimation({
  timeline,
  target = null,
  pseudoElement = null,
  animationName,
  playState = "running",
  playbackRate = 1,
  timing = ENDS,
  keyframes = keyframesOf("transform"),
}) {
  const log = [];
  let currentTime = 250;
  return {
    log,
    timeline,
    animationName,
    playState,
    playbackRate,
    effect: timing && {
      target,
      pseudoElement,
      getComputedTiming: () => ({ ...timing }),
      getKeyframes: () => keyframes.map((keyframe) => ({ ...keyframe })),
    },
    get currentTime() {
      return currentTime;
    },
    set currentTime(value) {
      log.push(["seek", value]);
      currentTime = value;
    },
    finish() {
      log.push(["finish"]);
      this.playState = "finished";
    },
    pause() {
      log.push(["pause"]);
      this.playState = "paused";
    },
  };
}

/**
 * A page whose document holds the animations `specs` describe, each on the document's own
 * timeline and targeting its <body> unless its spec names another.
 * @param {object[]} specs - `fakeAnimation` options, the timeline and target among them optional.
 * @param {object} [page] - `loadProbe` options: a `head` holding the page's style sheets, say.
 */
export function pageWith(specs, page = {}) {
  const loaded = loadProbe(page);
  const timeline = { kind: "the document timeline" };
  Object.defineProperty(loaded.document, "timeline", {
    configurable: true,
    value: timeline,
  });
  const target = loaded.document.body;
  const animations = specs.map((spec) =>
    fakeAnimation({ timeline, target, ...spec }),
  );
  loaded.document.getAnimations = () => animations;
  return { page: loaded, animations };
}

/**
 * A shadow root of `mode` attached to `host`, holding one <div> its animations target unless a
 * spec names another, and listing those animations from its own getAnimations(), each on the
 * document's timeline, as an engine's are. Its <div> can host a root of its own in turn.
 * @param {Element} host - An element of a page `pageWith` made, or of a root this made.
 * @param {"open" | "closed"} mode
 * @param {object[]} specs - `fakeAnimation` options, the timeline and target among them optional.
 */
export function rootWith(host, mode, specs) {
  const root = host.attachShadow({ mode });
  const target = host.ownerDocument.createElement("div");
  root.append(target);
  const timeline = host.ownerDocument.timeline;
  const animations = specs.map((spec) =>
    fakeAnimation({ timeline, target, ...spec }),
  );
  root.getAnimations = () => animations;
  return { root, target, animations };
}
