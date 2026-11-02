// The in-page probe's one tree walker: every element a page script can reach below `tree`, the
// elements inside each OPEN shadow root included. It is a bare function expression, as the
// probe's other two files are; the driver (pass_probe.py) and the JS suites' helper hand it to
// page_probe.js, which hands it on to its motion half, so the layout signature, the digest, the
// element detectors and the motion stilling all read the same elements, and none grows a walk
// of its own.
//
// A selector query never crosses into a shadow root, so the walk enters each root it finds
// through its host's `shadowRoot`, recursively (`tree`'s own root included), and lists the
// root's elements right after their host, ahead of the host's own light children: tree order, with each root read where its host
// stands. An element slotted into a root lives in the light tree and is listed there, once. A
// CLOSED root's host reads a null `shadowRoot` to any page script, so a closed root, and every
// root inside one, is left out: the documented limit (browser_pass.md).
/**
 * @param {Document | Element | ShadowRoot} tree
 * @returns {Element[]}
 */
(tree) => {
  /**
   * @param {Document | Element | ShadowRoot} node
   * @returns {Element[]}
   */
  const walk = (node) =>
    [...node.querySelectorAll("*")].flatMap((element) =>
      element.shadowRoot ? [element, ...walk(element.shadowRoot)] : [element],
    );
  // A query lists a tree's descendants, never the tree itself, so a root on `tree` (one on
  // <body>, say, for the reads that start there) is entered here, its elements first, as a
  // host's always are.
  const own = "shadowRoot" in tree ? tree.shadowRoot : null;
  return own ? [...walk(own), ...walk(tree)] : walk(tree);
};
