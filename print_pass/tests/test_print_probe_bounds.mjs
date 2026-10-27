// The node suite for src/print_probe.js's bounds: every string a page supplies is cut to the
// argument's `maxText`, ending in a mark, and every list a page can grow to `maxItems`.
//
// A page chooses its own title, sources, animation names, element text and stored values,
// and the engine polls the probe every 100 ms, so an unbounded one would serialize a page's
// megabytes on every poll and carry them into the report. Its own file, beside
// test_print_probe.mjs, whose file has no room left; the harness here is the small part of
// that one these cases need.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const PROBE_PATH = fileURLToPath(
  new URL("../src/print_probe.js", import.meta.url),
);
const PROBE_SOURCE = readFileSync(PROBE_PATH, "utf8");
const ORIGIN = "http://127.0.0.1:1";

// Small bounds, so each case states its sizes outright.
const BOUNDS = { maxText: 8, maxItems: 2 };
const MARK = "…";

// One element as the probe reads it: its text and attributes alone.
function element({ text = "", attributes = {} } = {}) {
  return {
    tagName: "DIV",
    id: "",
    classList: [],
    textContent: text,
    getAttribute: (name) =>
      Object.hasOwn(attributes, name) ? attributes[name] : null,
  };
}

// One complete image with a src attribute.
function image(src) {
  return {
    src,
    currentSrc: "",
    complete: true,
    naturalWidth: 1,
    loading: "eager",
    getAttribute: (name) => (name === "src" ? src : null),
    hasAttribute: (name) => name === "src",
  };
}

// One running script animation on a plain element.
function animation(id) {
  return { playState: "running", id, effect: { target: element() } };
}

// The page globals the probe reads, any document field replaced by `documentFields`.
function pageGlobals({
  elements = {},
  storage = {},
  documentFields = {},
} = {}) {
  return {
    URL,
    location: { origin: ORIGIN },
    matchMedia: () => ({ matches: false }),
    localStorage: { getItem: (key) => storage[key] ?? null },
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

// The probe's result over `globals`, called with the bounds and `argument`'s reads and keys.
function runProbe(globals, argument = {}) {
  const probe = vm.runInNewContext(PROBE_SOURCE, globals, {
    filename: PROBE_PATH,
  });
  return structuredClone(
    probe({ reads: [], storageKeys: [], ...BOUNDS, ...argument }),
  );
}

test("text at the bound passes through whole", () => {
  const globals = pageGlobals({ documentFields: { title: "12345678" } });
  assert.equal(runProbe(globals).title, "12345678");
});

test("text past the bound is cut to it, ending in the mark", () => {
  const globals = pageGlobals({ documentFields: { title: "123456789" } });
  assert.equal(runProbe(globals).title, `1234567${MARK}`);
});

test("a cut never splits a surrogate pair", () => {
  // The cut would fall between the two halves of the emoji at code units 6 and 7.
  const globals = pageGlobals({
    documentFields: { title: "123456\u{1F600}89" },
  });
  assert.equal(runProbe(globals).title, `123456${MARK}`);
});

test("each string a page supplies is cut", () => {
  const long = "abcdefghijk";
  const cut = `abcdefg${MARK}`;
  const globals = pageGlobals({
    documentFields: {
      images: [image(long)],
      getAnimations: () => [animation(long)],
    },
    elements: { "#it": [element({ text: long, attributes: { a: long } })] },
    storage: { key: long },
  });
  const reads = ["text", "attribute"].map((kind) => ({
    name: kind,
    selector: "#it",
    kind,
    key: kind === "text" ? null : "a",
    all: false,
  }));
  const result = runProbe(globals, { reads, storageKeys: ["key"] });
  assert.equal(result.images[0].src, cut);
  assert.equal(result.animations[0].name, cut);
  assert.deepEqual(
    result.reads.map((read) => read.values),
    [[cut], [cut]],
  );
  assert.deepEqual(result.storage, [["key", cut]]);
});

test("a value that is no string is never cut", () => {
  const globals = pageGlobals({
    elements: { "#it": [element({ attributes: {} })] },
  });
  const reads = [
    { name: "a", selector: "#it", kind: "attribute", key: "a", all: false },
  ];
  assert.deepEqual(runProbe(globals, { reads }).reads[0].values, [null]);
});

test("each list a page can grow stops at the bound", () => {
  const globals = pageGlobals({
    documentFields: {
      images: [image("/a"), image("/b"), image("/c")],
      getAnimations: () => [animation("a"), animation("b"), animation("c")],
    },
    elements: { li: [element(), element(), element()] },
  });
  const reads = [
    { name: "items", selector: "li", kind: "text", key: null, all: true },
  ];
  const result = runProbe(globals, { reads });
  assert.deepEqual(
    result.images.map((entry) => entry.src),
    ["/a", "/b"],
  );
  assert.deepEqual(
    result.animations.map((entry) => entry.name),
    ["a", "b"],
  );
  assert.equal(result.reads[0].values.length, 2);
});
