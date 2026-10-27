// The in-page half of browser_pass: ONE function the driver evaluates in the page as
// page.evaluate(source, [op, config]). It is a bare function expression rather than a module
// because the driver hands its source text to the engine. It is written as a function OF its
// motion half, page_probe_motion.js -- the stilling both settles run, kept in a file of its own
// -- and of its tree walker, page_probe_trees.js, the one walk every read here and the motion
// half's make, so an element inside an open shadow root is read wherever the document's are.
// The driver applies this file to the other two, so the source an engine receives is still one
// function. Every value it shares with the Python side -- the plant marker, the planted texts,
// the missing path, the late-shift log's name -- arrives in `config`, so the two sides cannot
// drift apart. The late-script load's DevTools session calls the same function with a second
// argument, `listed`: a list the motion half made and the page kept (page_probe_motion.js).
//
// It runs with the page's scripts OFF as well as on: the engines still evaluate driver-sent
// code there, which is what keeps a faithful no-script page measurable. So nothing here may wait
// on a timer or a promise; each operation reads the page and returns at once. The late-script
// load's operations are the one exception to keeping no state: they run only with scripts on, and
// share a log in a page global that an init script (`watchShifts`) creates before the page's own
// code runs.
(motion, reachableElements) =>
  ([op, config], listed) => {
    // A box ending within half a pixel of the edge is rounding, not overflow.
    const EDGE_TOLERANCE_PX = 0.5;
    // WCAG 2.2's minimum target size (2.5.8).
    const MIN_TARGET_PX = 24;
    // The visually-hidden pattern (a 1px clipped box) is not a target anyone taps.
    const VISUALLY_HIDDEN_PX = 1;
    const TARGETS = [
      "a[href]",
      "area[href]",
      "button",
      'input:not([type="hidden"])',
      "select",
      "textarea",
      "summary",
      '[role="button"]',
      '[role="link"]',
      '[tabindex]:not([tabindex="-1"])',
    ].join(", ");
    const root = document.documentElement;

    const hasBox = (element) => element.getClientRects().length > 0;
    const shown = (element) =>
      hasBox(element) && getComputedStyle(element).visibility !== "hidden";
    const tenth = (value) => Math.round(value * 10) / 10;
    const hundredth = (value) => Math.round(value * 100) / 100;

    // The element holding `node` in its own tree -- its parent, or at the top of a shadow root,
    // that root's host, since a root is not an element.
    /**
     * @param {Node} node
     * @returns {Element | null}
     */
    const parentOrHost = (node) =>
      node.parentElement ??
      (node.parentNode instanceof ShadowRoot ? node.parentNode.host : null);

    // The element `node` is laid out inside: the slot it is assigned to, else its parent, else
    // -- at the top of a shadow root -- that root's host. This is the route a box takes up the
    // flat tree, so a scroller or an `inert` in a root holds what is slotted into it. A slot in a
    // CLOSED root is not reported (`assignedSlot` reads null), so the climb stays in the light
    // tree there, as every read stays out of a closed root.
    /**
     * @param {Element} node
     * @returns {Element | null}
     */
    const flatParent = (node) => node.assignedSlot ?? parentOrHost(node);

    // A path that names the same element in every engine that built the same DOM: each step is
    // the tag, its position among same-tag siblings, and its id when it has one. One inside a
    // shadow root is named through its host, the root itself a `#shadow-root` step.
    /** @param {Element} element */
    const path = (element) => {
      const steps = [];
      for (
        let node = /** @type {Element | null} */ (element);
        node && node !== document.body;
        node = node.parentElement
      ) {
        let index = 1;
        for (
          let sib = node.previousElementSibling;
          sib;
          sib = sib.previousElementSibling
        ) {
          if (sib.tagName === node.tagName) index += 1;
        }
        const id = node.id ? `#${node.id}` : "";
        steps.unshift(`${node.tagName.toLowerCase()}:${index}${id}`);
      }
      const tree = element.getRootNode();
      const base =
        tree instanceof ShadowRoot ? `${path(tree.host)}>#shadow-root` : "body";
      return [base, ...steps].join(">");
    };

    /** @param {Element} element */
    const describe = (element) => {
      const plant = element.getAttribute(config.plantAttribute);
      return path(element) + (plant ? `${config.plantMark}${plant}` : "");
    };

    // Content past the right edge is only a defect when nothing between it and <body> scrolls
    // or clips it -- a carousel's off-screen slides are inside a scroller by design. <body> and
    // <html> are deliberately NOT counted: content they clip is content nobody can reach.
    const insideScrollerOrClip = (element) => {
      for (
        let node = flatParent(element);
        node && node !== document.body;
        node = flatParent(node)
      ) {
        if (getComputedStyle(node).overflowX !== "visible") return true;
      }
      return false;
    };

    // The nearest element up the flat tree that has a box of its own.
    /** @param {Element} element */
    const boxedParent = (element) => {
      let node = flatParent(element);
      while (node && !hasBox(node)) node = flatParent(node);
      return node;
    };

    const pastRightEdge = () => {
      const limit = innerWidth + EDGE_TOLERANCE_PX;
      const flagged = new Set();
      const found = [];
      for (const element of reachableElements(document.body)) {
        if (!shown(element)) continue;
        const box = element.getBoundingClientRect();
        if (box.width === 0 || box.height === 0 || box.right <= limit) continue;
        if (insideScrollerOrClip(element)) continue;
        flagged.add(element);
        // Its parent already names this overflow; listing every descendant is noise. A parent
        // with no box -- a <slot>, a `display: contents` wrapper -- is never found itself, so
        // the box it stands in is looked for above it.
        if (flagged.has(boxedParent(element))) continue;
        found.push(
          `${describe(element)} right edge at ${Math.round(box.right)}px`,
        );
      }
      return found;
    };

    // Only a VISIBLE leak counts. `hidden="until-found"` renders a box by design, and so does a
    // layout-shift reserve: a script-revealed element held as `visibility: hidden` so its space is
    // kept until the reveal. That has a box and paints nothing, which is the point of it.
    const hiddenRendered = () =>
      reachableElements(document)
        .filter((element) => element.matches("[hidden]"))
        .filter((element) => element.getAttribute("hidden") !== "until-found")
        .filter(shown)
        .map(describe);

    // Whether `element` or anything it is laid out inside is inert: inertness reaches down the
    // flat tree, into a host's root and onto what a slot holds.
    /** @param {Element} element */
    const inert = (element) => {
      for (
        let node = /** @type {Element | null} */ (element);
        node;
        node = flatParent(node)
      ) {
        if (node.matches("[inert]")) return true;
      }
      return false;
    };

    // A link inside a sentence (display: inline) is exempt, as WCAG exempts it.
    const smallTargets = () => {
      const found = [];
      const targets = reachableElements(document).filter((element) =>
        element.matches(TARGETS),
      );
      for (const element of targets) {
        if (!shown(element) || inert(element) || element.matches(":disabled"))
          continue;
        if (getComputedStyle(element).display === "inline") continue;
        const box = element.getBoundingClientRect();
        if (box.width <= VISUALLY_HIDDEN_PX || box.height <= VISUALLY_HIDDEN_PX)
          continue;
        if (Math.min(box.width, box.height) < MIN_TARGET_PX) {
          found.push(
            `${describe(element)} ${tenth(box.width)}x${tenth(box.height)}`,
          );
        }
      }
      return found;
    };

    // Forces each declaration past any author rule, so a site's own CSS cannot un-plant it. They
    // are CSSOM writes, never a style attribute: a page whose policy refuses style attributes
    // would refuse one, and Content Security Policy does not govern CSSOM.
    const plantElement = (tag, detector, declarations) => {
      const element = document.createElement(tag);
      element.setAttribute(config.plantAttribute, detector);
      for (const [name, value] of Object.entries(declarations)) {
        element.style.setProperty(name, value, "important");
      }
      return element;
    };

    // One fault per detector, planted AFTER load -- an init script runs before <body> exists.
    // Returns whether each plant took, checked independently of the detector meant to catch it.
    const plant = () => {
      const overflow = plantElement("div", "page_overflow", {
        position: "absolute",
        left: `${root.scrollWidth + 50}px`,
        top: "0",
        width: "50px",
        height: "10px",
        display: "block",
      });
      // Fixed, so it sits past the edge WITHOUT widening the page: the edge detector alone.
      const edge = plantElement("div", "past_right_edge", {
        position: "fixed",
        left: `${innerWidth + 10}px`,
        top: "0",
        width: "20px",
        height: "20px",
        display: "block",
      });
      const leak = plantElement("div", "hidden_rendered", { display: "block" });
      leak.hidden = true;
      leak.textContent = "planted";
      const target = plantElement("button", "small_target", {
        display: "block",
        width: "10px",
        height: "10px",
        "min-width": "0",
        "min-height": "0",
        padding: "0",
        border: "0",
      });
      target.type = "button";
      const image = document.createElement("img");
      image.alt = "";
      image.src = config.missingPath;
      document.body.append(overflow, edge, leak, target, image);
      console.error(config.consoleText);
      // Throws only where page scripts run; with scripts off the timer never fires.
      setTimeout(() => {
        throw new Error(config.pageErrorText);
      }, 0);
      const targetBox = target.getBoundingClientRect();
      return {
        page_overflow:
          hasBox(overflow) &&
          overflow.getBoundingClientRect().left >= innerWidth,
        past_right_edge:
          getComputedStyle(edge).position === "fixed" &&
          edge.getBoundingClientRect().left >= innerWidth,
        hidden_rendered: leak.hidden && hasBox(leak),
        small_target:
          targetBox.width > VISUALLY_HIDDEN_PX &&
          targetBox.width < MIN_TARGET_PX,
        console_error: true,
        page_error: true,
        http_error: image.isConnected,
      };
    };

    // --- The late-script load (pass_late_load.py): Chromium, scripts on, scripts held. ---

    /**
     * A layout-shift entry. The DOM library this file is type-checked against declares no
     * LayoutShift, so the fields read here are named here.
     * @typedef {PerformanceEntry & {value: number, hadRecentInput: boolean,
     *   sources: {node: Node | null}[]}} LayoutShift
     */
    /**
     * A resource entry with the one field that library does not declare yet.
     * @typedef {PerformanceResourceTiming & {renderBlockingStatus: string}} ResourceTiming
     */
    /**
     * The log this load's operations share between calls.
     * @typedef {{supported: boolean, released: number | null, presentableAt: number | null,
     *   entries: LayoutShift[]}} ShiftLog
     */

    // The page global that log lives in, under the name `config` gives it. The page's own
    // globals are not this file's to type, so the window is viewed as holding only that log.
    const pageGlobals = /** @type {Record<string, ShiftLog>} */ (
      /** @type {unknown} */ (window)
    );

    // Installed as an init script, before any of the page's own code: a buffered observer
    // recording every layout shift into a page global the later operations read. Whether this
    // engine reports shifts at all is recorded too, so an engine that does not reads as not
    // measured, never as a page that did not move.
    const watchShifts = () => {
      /** @type {ShiftLog} */
      const log = {
        supported:
          PerformanceObserver.supportedEntryTypes.includes("layout-shift"),
        released: null,
        presentableAt: null,
        entries: [],
      };
      pageGlobals[config.shiftLog] = log;
      if (log.supported) {
        new PerformanceObserver((list) => {
          log.entries.push(.../** @type {LayoutShift[]} */ (list.getEntries()));
        }).observe({ type: "layout-shift", buffered: true });
      }
      // WHEN THE PAGE BECAME PRESENTABLE: painted, and then in its own fonts. This is what
      // shifts are counted from, and it is NOT always the release -- a held script the page
      // cannot paint without is released at once, before any paint, so a font swapping in
      // afterwards would be charged to the scripts without this.
      //
      // `document.fonts.ready` is awaited AFTER the first paint, never before, and the order is
      // the whole correctness of it: on a document with no layout yet there is no text to match
      // a face to, so that promise resolves immediately and would mark a page presentable before
      // it had drawn anything. Once something is painted, it resolves when the faces that layout
      // asked for have arrived.
      //
      // TWO FRAMES AFTER THAT, for the reason the late-shift plant below waits two: the promise
      // resolves when the faces are IN, not when the frame showing them has been drawn, so the
      // swap's own layout shift can still be timestamped after it. Measured: a render-blocking
      // page whose face swapped in scored 0.0027 from the resolution alone, on one run out of
      // two -- a race, and the flakiest kind, since the reading that looks right is the one that
      // silently charges a font to the scripts. The first callback runs before the next paint;
      // the second runs after it, which is where the page is genuinely presentable.
      new PerformanceObserver((entries, observer) => {
        if (entries.getEntries().length === 0) return;
        observer.disconnect();
        document.fonts.ready.then(() => {
          requestAnimationFrame(() =>
            requestAnimationFrame(() => {
              log.presentableAt = performance.now();
            }),
          );
        });
      }).observe({ type: "paint", buffered: true });
      return log.supported;
    };

    // A layout shift names the node that moved: often a text node, sometimes one since removed. A
    // text node atop a shadow root is named by that root's host.
    const describeSource = (node) => {
      const element =
        node?.nodeType === Node.ELEMENT_NODE
          ? node
          : node
            ? parentOrHost(node)
            : null;
      if (!element) return "(a node no longer in the page)";
      return element.isConnected
        ? describe(element)
        : `${describe(element)} (since removed)`;
    };

    const lateShifts = () => {
      const log = pageGlobals[config.shiftLog];
      return {
        supported: log.supported,
        released: log.released,
        // Null until frames have shown the page in its own fonts; pass_late_load waits for it.
        presentableAt: log.presentableAt,
        paints: performance.getEntriesByType("paint").map((p) => p.startTime),
        // Known only once a script has arrived: whether the page could not paint without it.
        blockingHeld: /** @type {ResourceTiming[]} */ (
          performance.getEntriesByType("resource")
        ).some(
          (entry) =>
            config.heldUrls.includes(entry.name) &&
            entry.renderBlockingStatus === "blocking",
        ),
        entries: log.entries.map((entry) => ({
          start: entry.startTime,
          value: entry.value,
          recentInput: entry.hadRecentInput,
          sources: entry.sources.map((source) => describeSource(source.node)),
        })),
      };
    };

    // The late-shift plant: a box drawn across the top of the viewport, moved down by half the
    // viewport two frames later -- once it has been painted where it started, or its arrival would
    // be an appearance rather than a shift. Absolutely placed, so nothing else moves with it.
    const plantShift = () => {
      const half = Math.round(innerHeight / 2);
      const box = plantElement("div", "late_shift", {
        position: "absolute",
        left: `${scrollX}px`,
        top: `${scrollY}px`,
        width: `${innerWidth}px`,
        height: `${half}px`,
        margin: "0",
        display: "block",
        visibility: "visible",
        background: "#808",
      });
      document.body.append(box);
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          box.style.setProperty("top", `${scrollY + half}px`, "important");
          box.dataset.browserPassMoved = "true";
        }),
      );
      return hasBox(box);
    };

    // A script the page cannot paint without: one marked blocking="render", or a classic script
    // in <head> without async or defer, which stops the parser before there is a <body> to paint.
    const blocksRendering = (script) =>
      script.src !== "" &&
      ((script.getAttribute("blocking") ?? "")
        .split(/\s+/)
        .includes("render") ||
        (script.type !== "module" &&
          !script.async &&
          !script.defer &&
          script.closest("head") !== null));

    const plantMoved = () =>
      /** @type {HTMLElement | null} */ (
        document.querySelector(`[${config.plantAttribute}="late_shift"]`)
      )?.dataset.browserPassMoved === "true";

    const operations = {
      watchShifts,
      // Whether the held scripts can be let through: the page has painted and its fonts are in,
      // or a held script is one it cannot paint without, so no paint will come until it runs.
      // The paint is what makes the font status meaningful -- before a layout there is nothing
      // to load a face for, and `document.fonts.status` reads "loaded" on an empty document.
      holdState: () => ({
        painted: performance.getEntriesByType("paint").length > 0,
        fontsLoaded: document.fonts.status === "loaded",
        blocked: [...document.scripts].some(blocksRendering),
      }),
      markRelease: () => {
        pageGlobals[config.shiftLog].released = performance.now();
        return true;
      },
      lateShifts,
      plantShift,
      plantMoved,
      // Both loads' settles, and the late-script load's listing before each of its stillings:
      // page_probe_motion.js's, given this call's options and the list handed back, if any.
      listMotion: () => motion(reachableElements, config, listed).listMotion(),
      stillMotion: () =>
        motion(reachableElements, config, listed).stillMotion(),
      state: () => ({
        innerWidth,
        innerHeight,
        clientWidth: root.clientWidth,
        scriptingNone: matchMedia("(scripting: none)").matches,
        dark: matchMedia("(prefers-color-scheme: dark)").matches,
        landscape: matchMedia("(orientation: landscape)").matches,
        reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
      }),
      // Cheap to read every poll, and it moves whenever anything laid out moves.
      signature: () => {
        const all = document.body ? reachableElements(document.body) : [];
        let sum = 0;
        for (const element of all) {
          const box = element.getBoundingClientRect();
          sum +=
            Math.round(box.x) +
            3 * Math.round(box.y) +
            5 * Math.round(box.width) +
            7 * Math.round(box.height);
        }
        return [
          root.scrollWidth,
          root.scrollHeight,
          all.length,
          document.fonts.status,
          sum,
        ];
      },
      layout: () => ({
        overflow: Math.max(0, root.scrollWidth - root.clientWidth),
        pastRightEdge: pastRightEdge(),
        hiddenRendered: hiddenRendered(),
        smallTargets: smallTargets(),
      }),
      digest: () =>
        reachableElements(document.body).map((element) => {
          const box = element.getBoundingClientRect();
          return [
            describe(element),
            hasBox(element) ? 1 : 0,
            hundredth(box.x + scrollX),
            hundredth(box.y + scrollY),
            hundredth(box.width),
            hundredth(box.height),
            hundredth(parseFloat(getComputedStyle(element).fontSize) || 0),
          ];
        }),
      plant,
    };
    return operations[op]();
  };
