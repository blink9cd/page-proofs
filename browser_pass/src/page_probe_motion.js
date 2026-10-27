// The in-page probe's MOTION half: the stilling both loads' settles run before every poll, so a
// page is measured at rest. It is a bare function expression, as page_probe.js is, and
// page_probe.js is a function OF it and of the tree walker, page_probe_trees.js: the driver
// (pass_probe.py) and the JS suites' helper alike apply page_probe.js to the two, so an engine
// still receives ONE function. Given that walker, the probe's configuration and, from the
// late-script load, the list it made, it returns its operations.
//
// `stillMotion` brings every animation the page can reach -- the document's, and every OPEN
// shadow root's, however deeply nested (`reachableAnimations`) -- that is still RUNNING on the
// document's timeline to rest: one that ends is finished, one that never ends is paused at an
// iteration start. A page-paused, finished or idle animation is what a visitor sees, so it is
// neither touched nor counted. One on another timeline (a scroll-driven one) or at a zero rate
// does not advance with time, so waiting could never end it and a seek would invent a state: it
// is left, and counted. With `layoutInertOnly` (the late-script load) so is every animation not
// PROVABLY layout-inert. A CLOSED root is out of the page's reach, so its motion is neither
// stilled nor counted.
//
// What a CSS animation moves is known only to the engine's style resolver, which knows the
// `@keyframes` rule the cascade applies to it, wherever that rule sits. So the late-script load
// drives this half through its DevTools session (pass_inert.py): `listMotion` lists the running
// animations into one array the page keeps, and beside it what the driver must ask the engine;
// the driver reads the rule applied to each CSS animation asked about; and `stillMotion` stills
// that SAME array, handed back as `listed`, judging each animation by the names its rule
// declares, beside it in `declared`.
/**
 * @param {(tree: Document) => Element[]} reachableElements - page_probe_trees.js: every element
 *   the page can reach, each open shadow root's included.
 * @param {{layoutInertOnly?: boolean, declared?: (string[] | null)[]}} config
 * @param {Animation[] | undefined} listed - The late-script load's listing, handed back; absent,
 *   `stillMotion` stills every animation the page can reach, the document's and each open
 *   shadow root's.
 */
(reachableElements, { layoutInertOnly, declared }, listed) => {
  // What moves or fades a box without laying it out again: a seek of it moves no layout, so the
  // Layout Instability API reports nothing (measured in Chromium: seeking an endless transform,
  // rotate or opacity, no shift; margin-top, 0.134). The late-script load stills only these. Each
  // is spelled alike in CSS and in getKeyframes(), so one list serves both readings below.
  const LAYOUT_INERT_PROPERTIES = Object.freeze([
    "transform",
    "translate",
    "rotate",
    "scale",
    "opacity",
  ]);
  // What says when and how a keyframe applies, not what it moves: getKeyframes()'s keys, and the
  // two a `@keyframes` rule's keyframe may declare, which those keys report.
  const TIMING_KEYS = Object.freeze([
    "offset",
    "computedOffset",
    "easing",
    "composite",
    "animation-timing-function",
    "animation-composition",
  ]);

  // Whether `animation` advances with time: on the document's timeline, at a nonzero rate.
  /** @param {Animation} animation */
  const advances = (animation) =>
    animation.timeline === document.timeline && animation.playbackRate !== 0;

  // Every animation the page can reach: the document's own, then each OPEN shadow root's, in
  // tree order. Each tree lists only its own -- the document's getAnimations() leaves out one
  // targeting an element inside a shadow root, a root's leaves out the roots nested in it, and
  // an element's `getAnimations({ subtree: true })` crosses into none -- so every root the
  // probe's walker reaches is asked, and the union counts nothing twice, since each animation is
  // in exactly one tree's list. A CLOSED root's host reads a null `shadowRoot` to the walker, so
  // its motion, and that of any root inside it, is out of reach: the documented limit.
  /** @returns {Animation[]} */
  const reachableAnimations = () =>
    [
      document,
      ...reachableElements(document).flatMap((element) =>
        element.shadowRoot ? [element.shadowRoot] : [],
      ),
    ].flatMap((tree) => tree.getAnimations());

  // The name of the `@keyframes` rule a CSS animation was made from; undefined for any other.
  /** @param {Animation} animation */
  const ruleName = (animation) =>
    /** @type {Partial<CSSAnimation>} */ (animation).animationName;

  // What `animation`'s getKeyframes() says it moves: every key less the timing ones.
  /** @param {Animation} animation */
  const keyframeNames = (animation) => {
    const effect = /** @type {KeyframeEffect | null} */ (animation.effect);
    return (effect?.getKeyframes() ?? [])
      .flatMap((keyframe) => Object.keys(keyframe))
      .filter((key) => !TIMING_KEYS.includes(key));
  };

  // Whether every one of `names` moves or fades a box without laying it out again.
  /** @param {string[]} names */
  const allInert = (names) =>
    names.every((key) => LAYOUT_INERT_PROPERTIES.includes(key));

  // Whether `animation` is PROVABLY layout-inert: it names at least one animated property, and
  // every one is layout-inert. A CSS animation's names include `ruleNames`, what its applied rule
  // declares, because Chromium leaves custom properties out of its getKeyframes(), registered or
  // not -- a `--m` driving `margin-top` would otherwise read as moving nothing. Its getKeyframes()
  // still counts beside them: a script may have replaced the keyframes the rule gave it. Names
  // that are not a list are unknown, so NOT inert, and so is naming none: an empty list cannot be
  // told from one an engine emptied, and an animation that truly moves nothing never changes the
  // layout signature, so leaving it costs the settle nothing.
  /**
   * @param {Animation} animation
   * @param {string[] | null | undefined} ruleNames
   */
  const layoutInert = (animation, ruleNames) => {
    // Only a CSS animation has a rule; any other is read from getKeyframes() alone.
    const ruled = ruleName(animation) === undefined ? [] : ruleNames;
    if (!Array.isArray(ruled)) return false;
    const moved = [
      ...keyframeNames(animation),
      ...ruled.filter((key) => !TIMING_KEYS.includes(key)),
    ];
    return moved.length > 0 && allInert(moved);
  };

  // Whether `animation`'s applied rule could decide its verdict: a CSS animation that advances
  // with time -- one that does not is never stilled -- and whose getKeyframes() names nothing
  // but layout-inert properties, or nothing at all. One whose getKeyframes() names any other
  // property is left running whatever its rule declares, since the rule's names only add to
  // those, so reading its rule could change nothing.
  /** @param {Animation} animation */
  const ruleDecides = (animation) =>
    ruleName(animation) !== undefined &&
    advances(animation) &&
    allInert(keyframeNames(animation));

  /**
   * The first iteration start at or after local time 0: the delay plus every whole iteration
   * begun before 0. An infinite end implies a positive duration -- a zero one makes the active
   * interval zero however many iterations there are -- so the division is safe. When no
   * iteration is skipped the start is the delay, or 0 for one begun before it: the only such
   * case is the one endless iteration a script can make, whose every frame is its first, and
   * `0 * Infinity` there would hand the seek a NaN, which an engine refuses as a time.
   * @param {ComputedEffectTiming} timing
   * @returns {number}
   */
  const firstIterationStart = (timing) => {
    // Computed timing resolves both to milliseconds, whatever the DOM types allow for the
    // specified timing they share a declaration with.
    const delay = /** @type {number} */ (timing.delay);
    const duration = /** @type {number} */ (timing.duration);
    const skipped = Math.max(0, Math.ceil(-delay / duration));
    return skipped === 0 ? Math.max(delay, 0) : delay + skipped * duration;
  };

  // The late-script load's listing, which the driver reads through its handle on it: `listed`,
  // every reachable animation running now, and `asks`, beside each the question for the engine,
  // if any. Only an animation whose rule could decide its verdict is asked about, by its name,
  // the element its effect targets -- inside a shadow root, for one a root listed -- and the
  // pseudo-element of it: what finding that rule takes. Every other stands beside a null, which
  // costs the driver no call of its own, however many run.
  const listMotion = () => {
    const running = reachableAnimations().filter(
      (animation) => animation.playState === "running",
    );
    const asks = running.map((animation) => {
      if (!ruleDecides(animation)) return null;
      const effect = /** @type {KeyframeEffect | null} */ (animation.effect);
      return {
        name: ruleName(animation),
        target: effect?.target ?? null,
        pseudo: effect?.pseudoElement ?? null,
      };
    });
    return { listed: running, asks };
  };

  const stillMotion = () => {
    // Handed back the list its rules were read for, each verdict lands on the animation it was
    // read for. One started since is not in it, so it is left to the next poll's listing. With
    // none, it lists what it can reach itself, as `listMotion` does.
    const animations = listed ?? reachableAnimations();
    const counts = { finished: 0, paused: 0, running: 0 };
    for (const [index, animation] of animations.entries()) {
      if (animation.playState !== "running") continue;
      if (
        !advances(animation) ||
        (layoutInertOnly && !layoutInert(animation, declared?.[index]))
      ) {
        counts.running += 1;
        continue;
      }
      // An animation with no effect ends at time 0, so it finishes like any that ends.
      const timing = animation.effect?.getComputedTiming();
      if (!timing || Number.isFinite(timing.endTime)) {
        animation.finish();
        counts.finished += 1;
        continue;
      }
      animation.pause();
      animation.currentTime = firstIterationStart(timing);
      counts.paused += 1;
    }
    return counts;
  };

  return { listMotion, stillMotion };
};
