// The node suite for src/print_probe.js, the in-page probe print_pass evaluates in a page.
//
// There is no DOM library: the probe reads a handful of page globals, and each test hands it
// plain objects standing in for them. The file runs through `vm` under its own path, which is
// what lands node's coverage on print_probe.js itself rather than on an anonymous script.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const PROBE_PATH = fileURLToPath(
  new URL("../src/print_probe.js", import.meta.url),
);
const PROBE_SOURCE = readFileSync(PROBE_PATH, "utf8");

// A loopback origin, as every page print_pass measures is served from; the port is arbitrary.
const ORIGIN = "http://127.0.0.1:1";
const DARK = "(prefers-color-scheme: dark)";
const LIGHT = "(prefers-color-scheme: light)";
const REDUCE = "(prefers-reduced-motion: reduce)";
const NO_ARGUMENT = { reads: [], storageKeys: [] };
// The bounds print_probe.py hands every call; test_print_probe_bounds.mjs tests smaller ones.
const BOUNDS = { maxText: 1000, maxItems: 5000 };

// One element as the probe reads it: a tag, an id, its classes, attributes and text, plus any
// DOM property a test names.
function element({
  tagName = "DIV",
  id = "",
  classes = [],
  attributes = {},
  text = "",
  ...properties
} = {}) {
  return {
    tagName,
    id,
    classList: classes,
    textContent: text,
    getAttribute: (name) =>
      Object.hasOwn(attributes, name) ? attributes[name] : null,
    ...properties,
  };
}

// One image. Its `loading` property is what a browser's IDL attribute reads: only the known
// values, and "eager" when the attribute is absent, so the probe has to ask the attribute
// itself to tell an unset one apart. `srcAttribute` is the src attribute as written, the
// `src` property's own value unless a test says otherwise, and null for an image built with
// no src attribute at all.
function image({
  src = "",
  currentSrc = "",
  complete = true,
  loading = null,
  naturalWidth = 640,
  srcAttribute = src,
} = {}) {
  const attributes = { loading, src: srcAttribute };
  return {
    src,
    currentSrc,
    complete,
    naturalWidth,
    loading: loading === null ? "eager" : loading,
    getAttribute: (name) => attributes[name] ?? null,
    hasAttribute: (name) => (attributes[name] ?? null) !== null,
  };
}

// One animation from document.getAnimations(), running on a plain <div> unless a test says
// otherwise; `names` carries a CSS animation's or transition's own name fields.
function animation({
  playState = "running",
  id = "",
  effect = { target: element() },
  ...names
} = {}) {
  return { playState, id, effect, ...names };
}

// One [[read]] as the probe's argument carries it.
function read({ name = "value", selector = "#it", kind, key = null, all }) {
  return { name, selector, kind, key, all };
}

// The page globals the probe reads. `media` lists the queries that match, `elements` maps a
// selector to what querySelectorAll finds, `storage` is what localStorage holds, and
// `documentFields` replaces any of the document's own fields.
function pageGlobals({
  media = [],
  elements = {},
  storage = {},
  documentFields = {},
} = {}) {
  return {
    URL,
    location: { origin: ORIGIN },
    matchMedia: (query) => ({ matches: media.includes(query) }),
    localStorage: {
      getItem: (key) => (Object.hasOwn(storage, key) ? storage[key] : null),
    },
    document: {
      readyState: "complete",
      title: "A page",
      fonts: { status: "loaded" },
      images: [],
      getAnimations: () => [],
      querySelectorAll: (selector) => elements[selector] ?? [],
      ...documentFields,
    },
  };
}

// Compile the file as the engine would, call it, and return a plain copy of its result: an
// object built inside the vm context carries that context's prototypes, which a strict deep
// comparison would otherwise count as a difference.
function runProbe(globals, argument = NO_ARGUMENT) {
  const probe = vm.runInNewContext(PROBE_SOURCE, globals, {
    filename: PROBE_PATH,
  });
  return structuredClone(probe({ ...BOUNDS, ...argument }));
}

// A read-only stand-in: any write to it fails the test at once.
function readOnly(target) {
  const refuse = (_target, key) => {
    throw new Error(`the probe wrote ${String(key)}`);
  };
  return new Proxy(target, {
    set: refuse,
    defineProperty: refuse,
    deleteProperty: refuse,
  });
}

test("the file's completion value is the probe function itself", () => {
  const probe = vm.runInNewContext(PROBE_SOURCE, pageGlobals(), {
    filename: PROBE_PATH,
  });
  assert.equal(typeof probe, "function");
});

test("it returns exactly the documented keys", () => {
  assert.deepEqual(Object.keys(runProbe(pageGlobals())), [
    "colorScheme",
    "reducedMotion",
    "readyState",
    "fontsStatus",
    "title",
    "images",
    "animations",
    "reads",
    "storage",
  ]);
});

test("a dark color scheme reads dark", () => {
  assert.equal(runProbe(pageGlobals({ media: [DARK] })).colorScheme, "dark");
});

test("a light color scheme reads light", () => {
  assert.equal(runProbe(pageGlobals({ media: [LIGHT] })).colorScheme, "light");
});

test("neither color scheme reads no-preference", () => {
  assert.equal(runProbe(pageGlobals()).colorScheme, "no-preference");
});

test("reduced motion reads reduce", () => {
  assert.equal(
    runProbe(pageGlobals({ media: [REDUCE] })).reducedMotion,
    "reduce",
  );
});

test("no reduced motion reads no-preference", () => {
  assert.equal(runProbe(pageGlobals()).reducedMotion, "no-preference");
});

test("the document's ready state and title pass through", () => {
  const result = runProbe(
    pageGlobals({ documentFields: { readyState: "interactive", title: "T" } }),
  );
  assert.equal(result.readyState, "interactive");
  assert.equal(result.title, "T");
});

test("the fonts status is the document's font set status", () => {
  const globals = pageGlobals({
    documentFields: { fonts: { status: "loading" } },
  });
  assert.equal(runProbe(globals).fontsStatus, "loading");
});

test("a document with no font set reads unavailable", () => {
  const globals = pageGlobals({ documentFields: { fonts: undefined } });
  assert.equal(runProbe(globals).fontsStatus, "unavailable");
});

// The images array of a page holding exactly `images`.
function imagesOf(...images) {
  return runProbe(pageGlobals({ documentFields: { images } })).images;
}

test("an image's state passes through", () => {
  const [state] = imagesOf(
    image({
      src: `${ORIGIN}/a.png`,
      complete: false,
      loading: "lazy",
      naturalWidth: 0,
    }),
  );
  assert.deepEqual(state, {
    src: "/a.png",
    hasSrc: true,
    complete: false,
    loading: "lazy",
    naturalWidth: 0,
  });
});

test("an image with a src attribute records that it has one", () => {
  const [state] = imagesOf(image({ src: `${ORIGIN}/a.png` }));
  assert.equal(state.hasSrc, true);
});

test("an empty src attribute is still a src attribute", () => {
  // `src=""` asks for something, and is judged as any other source: only an absent
  // attribute requests nothing.
  const [state] = imagesOf(image({ src: `${ORIGIN}/`, srcAttribute: "" }));
  assert.equal(state.hasSrc, true);
});

test("an image built with no src attribute records that it has none", () => {
  const [state] = imagesOf(image({ srcAttribute: null, naturalWidth: 0 }));
  assert.equal(state.hasSrc, false);
});

test("an image with no loading attribute reads an empty loading", () => {
  const [state] = imagesOf(image({ src: `${ORIGIN}/a.png` }));
  assert.equal(state.loading, "");
});

test("a same-origin source is its path and query, without the port", () => {
  const [state] = imagesOf(image({ src: `${ORIGIN}/art/a.png?v=2#top` }));
  assert.equal(state.src, "/art/a.png?v=2");
});

test("the current source is preferred over the src attribute", () => {
  const [state] = imagesOf(
    image({ src: `${ORIGIN}/small.png`, currentSrc: `${ORIGIN}/large.png` }),
  );
  assert.equal(state.src, "/large.png");
});

test("a base64 data URL is reduced to its media type", () => {
  const [state] = imagesOf(
    image({ src: "data:image/png;base64,iVBORw0KGgo=" }),
  );
  assert.equal(state.src, "data:image/png");
});

test("a plain data URL is reduced to its media type", () => {
  const [state] = imagesOf(image({ src: "data:image/svg+xml,%3Csvg%3E" }));
  assert.equal(state.src, "data:image/svg+xml");
});

test("a cross-origin source is kept whole", () => {
  const src = "https://images.example/a.png?x=1";
  const [state] = imagesOf(image({ src }));
  assert.equal(state.src, src);
});

test("a source that is not a URL is kept as it is", () => {
  const [unparsed, empty] = imagesOf(
    image({ src: "http://[bad" }),
    image({ src: "" }),
  );
  assert.equal(unparsed.src, "http://[bad");
  assert.equal(empty.src, "");
});

// The animations array of a page whose getAnimations() returns `animations`.
function animationsOf(...animations) {
  const getAnimations = () => animations;
  return runProbe(pageGlobals({ documentFields: { getAnimations } }))
    .animations;
}

test("a document with no getAnimations reads no animations", () => {
  const globals = pageGlobals({ documentFields: { getAnimations: undefined } });
  assert.deepEqual(runProbe(globals).animations, []);
});

test("an animation that is not running is dropped", () => {
  const found = animationsOf(
    animation({ playState: "paused", animationName: "paused" }),
    animation({ playState: "finished", animationName: "finished" }),
    animation({ animationName: "kept" }),
  );
  assert.deepEqual(found, [{ name: "kept", target: "div" }]);
});

test("a CSS animation is named by its animation name", () => {
  const [found] = animationsOf(animation({ animationName: "fade", id: "x" }));
  assert.equal(found.name, "fade");
});

test("a CSS transition is named by the property it transitions", () => {
  const [found] = animationsOf(
    animation({ transitionProperty: "opacity", id: "x" }),
  );
  assert.equal(found.name, "transition:opacity");
});

test("a script animation is named by its id", () => {
  const [found] = animationsOf(animation({ id: "spin" }));
  assert.equal(found.name, "spin");
});

test("an animation with no name and no id is called animation", () => {
  const [found] = animationsOf(animation());
  assert.equal(found.name, "animation");
});

test("a target is its lower-case tag, its id and its classes", () => {
  const target = element({ tagName: "SECTION", id: "hero", classes: ["card"] });
  const [found] = animationsOf(animation({ effect: { target } }));
  assert.equal(found.target, "section#hero.card");
});

test("a target with no id names at most two classes", () => {
  const target = element({ classes: ["one", "two", "three"] });
  const [found] = animationsOf(animation({ effect: { target } }));
  assert.equal(found.target, "div.one.two");
});

test("an animation with no effect, or no target, targets unknown", () => {
  const found = animationsOf(
    animation({ effect: null }),
    animation({ effect: { target: null } }),
  );
  assert.deepEqual(
    found.map((entry) => entry.target),
    ["unknown", "unknown"],
  );
});

// A shadow root as the probe walks it: its own animations, and every element inside it.
function shadowRoot(animations, ...inside) {
  return {
    getAnimations: () => animations,
    querySelectorAll: (selector) => (selector === "*" ? inside : []),
  };
}

// document.getAnimations() lists no animation inside a shadow root, in any of the three
// engines (measured): each open root lists its own, a nested root's only in the inner root's.
test("each open shadow root's animations follow the document's, recursively", () => {
  const nested = shadowRoot([animation({ id: "nested" })]);
  const outer = shadowRoot(
    [animation({ id: "outer" })],
    element(),
    element({ shadowRoot: nested }),
  );
  const getAnimations = () => [animation({ id: "page" })];
  const globals = pageGlobals({
    elements: { "*": [element({ shadowRoot: outer })] },
    documentFields: { getAnimations },
  });
  assert.deepEqual(
    runProbe(globals).animations.map((found) => found.name),
    ["page", "outer", "nested"],
  );
});

test("a closed shadow root's host reads no root, so its motion is not listed", () => {
  const closedHost = element({ shadowRoot: null });
  const globals = pageGlobals({ elements: { "*": [closedHost] } });
  assert.deepEqual(runProbe(globals).animations, []);
});

// The values one read returns from a page where its selector finds `found`.
function valuesOf(spec, found) {
  const globals = pageGlobals({ elements: { [spec.selector]: found } });
  const [result] = runProbe(globals, { reads: [spec], storageKeys: [] }).reads;
  assert.equal(result.name, spec.name);
  return result.values;
}

const TEXTS = ["one", "two", "three"].map((text) => element({ text }));

test("an all-matches read of nothing is empty", () => {
  assert.deepEqual(valuesOf(read({ kind: "text", all: true }), []), []);
});

test("an all-matches read of one match is that one", () => {
  const spec = read({ kind: "text", all: true });
  assert.deepEqual(valuesOf(spec, TEXTS.slice(0, 1)), ["one"]);
});

test("an all-matches read of three matches is all three, in order", () => {
  const spec = read({ kind: "text", all: true });
  assert.deepEqual(valuesOf(spec, TEXTS), ["one", "two", "three"]);
});

test("a first-match read of three matches is the first", () => {
  assert.deepEqual(valuesOf(read({ kind: "text", all: false }), TEXTS), [
    "one",
  ]);
});

test("a first-match read of nothing is empty", () => {
  assert.deepEqual(valuesOf(read({ kind: "text", all: false }), []), []);
});

test("an attribute read is the attribute, or null when it is absent", () => {
  const spec = read({ kind: "attribute", key: "data-theme", all: true });
  const found = [
    element({ attributes: { "data-theme": "dark" } }),
    element({ attributes: {} }),
  ];
  assert.deepEqual(valuesOf(spec, found), ["dark", null]);
});

test("a property read keeps a string, number, boolean or null", () => {
  const spec = read({ kind: "property", key: "value", all: true });
  const found = ["on", 3, 1.5, true, null].map((value) => element({ value }));
  assert.deepEqual(valuesOf(spec, found), ["on", 3, 1.5, true, null]);
});

test("a property read of anything else is that value as a string", () => {
  const spec = read({ kind: "property", key: "value", all: true });
  const found = [{ nested: true }, undefined].map((value) =>
    element({ value }),
  );
  assert.deepEqual(valuesOf(spec, found), ["[object Object]", "undefined"]);
});

test("a text read is trimmed, its inner whitespace collapsed to one space", () => {
  const spec = read({ kind: "text", all: false });
  const found = [element({ text: "  Two\n\t  words   here \n" })];
  assert.deepEqual(valuesOf(spec, found), ["Two words here"]);
});

test("a stored key reads its value, and a missing key reads null", () => {
  const globals = pageGlobals({ storage: { theme: "dark" } });
  const argument = { reads: [], storageKeys: ["theme", "missing"] };
  assert.deepEqual(runProbe(globals, argument).storage, [
    ["theme", "dark"],
    ["missing", null],
  ]);
});

test("a storage read that throws reads null", () => {
  const globals = pageGlobals();
  globals.localStorage.getItem = () => {
    throw new Error("denied");
  };
  const argument = { reads: [], storageKeys: ["theme"] };
  assert.deepEqual(runProbe(globals, argument).storage, [["theme", null]]);
});

test("storage whose very access throws reads null", () => {
  const globals = pageGlobals();
  Object.defineProperty(globals, "localStorage", {
    get() {
      throw new Error("denied");
    },
  });
  const argument = { reads: [], storageKeys: ["theme"] };
  assert.deepEqual(runProbe(globals, argument).storage, [["theme", null]]);
});

test("it writes nothing on the page it reads", () => {
  const target = readOnly(element({ id: "hero" }));
  const globals = pageGlobals({
    elements: { "#it": [readOnly(element({ text: "x", value: "v" }))] },
    storage: { theme: "dark" },
    documentFields: {
      images: [readOnly(image({ src: `${ORIGIN}/a.png` }))],
      getAnimations: () => [
        readOnly(animation({ animationName: "fade", effect: { target } })),
      ],
    },
  });
  globals.document = readOnly(globals.document);
  const before = Object.keys(globals);
  const reads = ["attribute", "property", "text"].map((kind) =>
    read({ kind, key: "value", all: true }),
  );
  runProbe(globals, { reads, storageKeys: ["theme"] });
  assert.deepEqual(Object.keys(globals), before);
});
