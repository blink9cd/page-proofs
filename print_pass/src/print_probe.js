// The in-page half of print_pass: ONE function the engine evaluates in the page as
// page.evaluate(source, argument). It is a bare arrow-function expression, not a module,
// because the driver hands its source text to the engine, which calls the function the text
// evaluates to. The node suite runs this same file through `vm` for the same reason.
//
// It reports what a print of the page would be taken from (the media emulation in force,
// how far loading got, every image and whether it asked for a source, the animations still
// running (the document's and every open shadow root's), the targets' reads and storage),
// and it only reads: nothing is assigned on the window, the document or an element, so
// probing a page cannot change what is then printed.
//
// Everything a page supplies is bounded by the argument: each string it chose (its title, a
// source, an animation's name or target, a read value, a stored value) is cut to `maxText`
// UTF-16 units, ending in an ellipsis, and each list it can grow (its images, its running
// animations, a read's values) stops at `maxItems`. The engine polls the probe while a page
// settles, so an unbounded page would serialize its megabytes on every poll; print_probe.py
// refuses a result past the same bounds.
//
// A @typedef in a script is global to the one tsc program every tool here shares, so these
// carry the tool's prefix rather than a name another script could also want.
/**
 * @typedef {{ name: string, selector: string, kind: "attribute" | "property" | "text", key: string | null, all: boolean }} PrintProbeReadSpec
 * @typedef {string | number | boolean | null} PrintProbeValue
 * @typedef {{ src: string, hasSrc: boolean, complete: boolean, loading: string, naturalWidth: number }} PrintProbeImage
 * @typedef {{ name: string, target: string }} PrintProbeAnimation
 * @typedef {{ name: string, values: PrintProbeValue[] }} PrintProbeRead
 * @typedef {{ colorScheme: string, reducedMotion: string, readyState: string, fontsStatus: string, title: string, images: PrintProbeImage[], animations: PrintProbeAnimation[], reads: PrintProbeRead[], storage: [string, string | null][] }} PrintProbeResult
 */
/**
 * @param {{ reads: PrintProbeReadSpec[], storageKeys: string[], maxText: number, maxItems: number }} arg
 * @returns {PrintProbeResult}
 */
(arg) => {
  // How many classes a target's description names; more add length, not identification.
  const MAX_TARGET_CLASSES = 2;
  const DATA_SCHEME = "data:";
  const CUT_MARK = "\u2026";
  const HIGH_SURROGATE_LAST = /[\uD800-\uDBFF]$/;

  // `text`, cut to `arg.maxText` units ending in the mark when it is longer; a cut that
  // would leave half a surrogate pair drops that half too.
  /** @param {string} text */
  const clip = (text) => {
    if (text.length <= arg.maxText) return text;
    const kept = text.slice(0, arg.maxText - CUT_MARK.length);
    return kept.replace(HIGH_SURROGATE_LAST, "") + CUT_MARK;
  };

  /**
   * @template T
   * @param {T[]} items
   * @returns {T[]}
   */
  const bounded = (items) => items.slice(0, arg.maxItems);

  /** @param {string} query */
  const matches = (query) => matchMedia(query).matches;

  const colorScheme = matches("(prefers-color-scheme: dark)")
    ? "dark"
    : matches("(prefers-color-scheme: light)")
      ? "light"
      : "no-preference";
  const reducedMotion = matches("(prefers-reduced-motion: reduce)")
    ? "reduce"
    : "no-preference";

  // A same-origin source is reported as its path, so the report never carries the loopback
  // port a run happened to serve on; a data URL as its media type alone, never its bytes.
  // A source that does not parse as a URL (an empty or malformed src) is kept as it is.
  /** @param {HTMLImageElement} image */
  const describeSource = (image) => {
    const source = image.currentSrc || image.src;
    if (source.startsWith(DATA_SCHEME)) {
      const mediaType = source.slice(DATA_SCHEME.length).split(/[;,]/, 1)[0];
      return `${DATA_SCHEME}${mediaType}`;
    }
    const url = URL.parse(source);
    if (url === null || url.origin !== location.origin) return source;
    return url.pathname + url.search;
  };

  // The loading IDL property reads "eager" when the attribute is absent, so an image that
  // never asked for eager loading would read as one that did; the attribute tells them apart.
  // An image with no src attribute at all requested nothing (a carousel's hidden slide,
  // waiting for its turn), where `src=""` still asks for something; only the attribute
  // tells them apart, since both read an empty source.
  /** @param {HTMLImageElement} image */
  const describeImage = (image) => ({
    src: clip(describeSource(image)),
    hasSrc: image.hasAttribute("src"),
    complete: image.complete,
    loading: image.getAttribute("loading") === null ? "" : image.loading,
    naturalWidth: image.naturalWidth,
  });

  /** @param {Element | null | undefined} element */
  const describeTarget = (element) => {
    if (!element) return "unknown";
    const id = element.id ? `#${element.id}` : "";
    const classes = [...element.classList]
      .slice(0, MAX_TARGET_CLASSES)
      .map((name) => `.${name}`)
      .join("");
    return `${element.tagName.toLowerCase()}${id}${classes}`;
  };

  // A CSSAnimation carries its animation-name and a CSSTransition the property it moves;
  // a script's Animation has neither, only the id its author may have given it.
  /** @param {Animation & { animationName?: string, transitionProperty?: string }} animation */
  const nameAnimation = (animation) => {
    if (animation.animationName) return animation.animationName;
    if (animation.transitionProperty) {
      return `transition:${animation.transitionProperty}`;
    }
    return animation.id || "animation";
  };

  /** @param {Animation} animation */
  const describeAnimation = (animation) => ({
    name: clip(nameAnimation(animation)),
    target: clip(
      describeTarget(
        /** @type {KeyframeEffect | null} */ (animation.effect)?.target,
      ),
    ),
  });

  /** @param {PrintProbeValue} value */
  const clipValue = (value) =>
    typeof value === "string" ? clip(value) : value;

  /**
   * @param {unknown} value
   * @returns {PrintProbeValue}
   */
  const plainValue = (value) =>
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
      ? value
      : String(value);

  // An attribute or property read always carries its key, since a targets file names the
  // attribute or property as that read's value; only a text read's key is null, and it is
  // never used. An element's textContent is never null: only a document's or a doctype's is.
  /**
   * @param {Element} element
   * @param {PrintProbeReadSpec} spec
   * @returns {PrintProbeValue}
   */
  const readValue = (element, spec) => {
    const key = /** @type {string} */ (spec.key);
    if (spec.kind === "attribute") return clipValue(element.getAttribute(key));
    if (spec.kind === "property") {
      return clipValue(plainValue(Reflect.get(element, key)));
    }
    const text = /** @type {string} */ (element.textContent);
    return clip(text.replace(/\s+/g, " ").trim());
  };

  /**
   * @param {PrintProbeReadSpec} spec
   * @returns {PrintProbeRead}
   */
  const readOne = (spec) => {
    const found = [...document.querySelectorAll(spec.selector)];
    const chosen = spec.all ? bounded(found) : found.slice(0, 1);
    return {
      name: spec.name,
      values: chosen.map((element) => readValue(element, spec)),
    };
  };

  // Storage a page may not touch (blocked site data, a sandboxed frame) throws on access, and
  // reads as nothing stored rather than failing the whole probe.
  /**
   * @param {string} key
   * @returns {[string, string | null]}
   */
  const readStored = (key) => {
    try {
      const stored = localStorage.getItem(key);
      return [key, stored === null ? null : clip(stored)];
    } catch {
      return [key, null];
    }
  };

  // Every animation the page can reach: `tree`'s own list, then every OPEN shadow root's
  // below it, however deeply nested. In all three engines document.getAnimations() lists
  // none whose target lives inside a shadow root, and a root nested in another is listed by
  // the inner root alone, so each animation is in exactly one list. A CLOSED root is out of
  // the page's reach (its host's shadowRoot reads null), so its motion is never reported.
  /**
   * @param {Document | ShadowRoot} tree
   * @returns {Animation[]}
   */
  const reachableAnimations = (tree) => [
    ...tree.getAnimations(),
    ...[...tree.querySelectorAll("*")].flatMap((element) =>
      element.shadowRoot ? reachableAnimations(element.shadowRoot) : [],
    ),
  ];

  const animations =
    typeof document.getAnimations === "function"
      ? reachableAnimations(document)
      : [];

  return {
    colorScheme,
    reducedMotion,
    readyState: document.readyState,
    fontsStatus: document.fonts ? document.fonts.status : "unavailable",
    title: clip(document.title),
    images: bounded([...document.images]).map(describeImage),
    animations: bounded(
      animations.filter((animation) => animation.playState === "running"),
    ).map(describeAnimation),
    reads: arg.reads.map(readOne),
    storage: arg.storageKeys.map(readStored),
  };
};
