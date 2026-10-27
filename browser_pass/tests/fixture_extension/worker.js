/* global chrome -- the extension API, which only an extension's own scripts are given */
// The fixture's service worker. Its first run opens a welcome tab off loopback, as many real
// extensions do on install, which the egress guard must fail. And once the first page it marks
// is loading, it opens a page of its own, `welcome.html`, which breaks the extension's own
// policy while the pass is measuring: the live suite shows that violation never becomes the
// measured page's finding, since the pass measures only the page it opened itself.
let welcomed = false;
chrome.runtime.onInstalled.addListener(() => {
  chrome.tabs.create({ url: "http://192.0.2.1/welcome.html" });
});
chrome.runtime.onMessage.addListener(() => {
  if (welcomed) return;
  welcomed = true;
  chrome.tabs.create({ url: chrome.runtime.getURL("welcome.html") });
});
