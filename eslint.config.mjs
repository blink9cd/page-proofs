// ESLint flat config (ESLint 9+) for the JavaScript the tools in this
// repository carry. `.mjs` so it is parsed as ESM regardless of package.json
// "type".
//
// It sits at the repository's root, beside package.json, so one `eslint .`
// here covers every tool's JavaScript, with the presets `npm ci` installs.
// Both resolve from THIS folder's node_modules; npm's global prefix is never
// on that walk.
import js from "@eslint/js";
import globals from "globals";

export default [
  js.configs.recommended,
  {
    // A tool's pure modules: no Node global and no `node:` import, so they
    // are held to the browser's globals, where `process` or `Buffer` fails
    // `no-undef`. Only an entry module, saved as `.mjs`, touches stdin and
    // stdout. They are ES modules, and leaving sourceType at "script" would
    // parse every `import` / `export` as a syntax error.
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.browser,
    },
    rules: {
      eqeqeq: "error",
      "prefer-const": "error",
      "no-var": "error",
    },
  },
  {
    // Everything that runs under Node -- the suites, the helpers they share,
    // an entry module, and this file -- is `.mjs`, so its own block, keyed on
    // the extension, gives it Node's globals. Loosening the block above
    // instead would let a pure module quietly start using `process`.
    //
    // Keyed on the extension rather than on names, because a block keyed on
    // names misses a helper whose name does not fit the pattern, and that
    // helper falls through to `js.configs.recommended` alone: no Node
    // globals and none of eqeqeq / prefer-const / no-var, with nothing said.
    files: ["**/*.mjs"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.node,
    },
    // Same trio as the block above: test files are held to the same rules
    // as the code they test, and `**/*.js` above does not match `.mjs`, so
    // without a copy here a test file could use `var` or `==` uncaught.
    rules: {
      eqeqeq: "error",
      "prefer-const": "error",
      "no-var": "error",
    },
  },
];
