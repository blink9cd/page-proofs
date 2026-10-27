/* global chrome -- the extension API, which only an extension's own scripts are given */
// The fixture's content script: what an extension visibly does to a page. It marks the root
// element and inserts a <style> element, the two insertions the live suite reads back from the
// page's own script, so an extension that loaded but never acted is told from one that did.
// Then it tells the worker a page is loading, which opens the extension's own welcome page.
document.documentElement.setAttribute("data-fixture-extension", "on");
const style = document.createElement("style");
style.setAttribute("data-fixture-extension", "on");
style.textContent = "html { outline-offset: 0; }";
document.documentElement.append(style);
chrome.runtime.sendMessage("marked");
