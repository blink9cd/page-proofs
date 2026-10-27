// The in-page half of csp_watch: ONE function, which csp_watch.install hands the engine as an
// init script followed by the one call that starts it, so it runs in every document of the
// context before the document's own scripts do. It is a classic script, not a module, because an
// init script is evaluated as one; the node suite runs this same file through `vm` for the same
// reason.
//
// The listener captures on the document, so a violation an element reports reaches it whether
// or not the event bubbles, and it hands each one straight to the binding: a record kept in the
// page would be erased by the next navigation, a meta refresh included. The binding is looked up
// at each event rather than once, and a page where it was never exposed is left alone: the
// listener must not throw into a page it only observes.
/**
 * Start sending every Content Security Policy violation `win`'s document reports to the
 * binding named `bindingName`.
 * @param {Window} win
 * @param {string} bindingName
 */
// eslint-disable-next-line no-unused-vars -- called by the line install() appends to this text, which no linter of this one file can see
function cspWatch(win, bindingName) {
  // An engine already cuts a sample to 40 characters; cutting again guards the record's size
  // against one that does not. The cut is by character, so a pair of UTF-16 code units is never
  // split into a lone half the driver could not encode. The driver refuses a longer sample
  // (SAMPLE_LIMIT in csp_watch.py, whose suite reads this constant to check the two agree).
  const SAMPLE_LIMIT = 40;
  win.document.addEventListener(
    "securitypolicyviolation",
    (event) => {
      const binding = /** @type {Record<string, unknown>} */ (
        /** @type {unknown} */ (win)
      )[bindingName];
      if (typeof binding !== "function") return;
      // A sample that is not a string is handed on as it came: the driver refuses it as an
      // instrument fault, where cutting it here would throw and lose the violation unseen.
      const sample = /** @type {unknown} */ (event.sample);
      binding({
        document: win.location.pathname + win.location.search,
        directive: event.effectiveDirective,
        blocked: event.blockedURI,
        sample:
          typeof sample === "string"
            ? Array.from(sample).slice(0, SAMPLE_LIMIT).join("")
            : sample,
      });
    },
    true,
  );
}
