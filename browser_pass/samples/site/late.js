// A late script that moves what is already on screen: the fault the late_shift detector exists
// to catch. It adds a banner above everything else in <main>, so the page's content moves down.
//
// faulty.html loads it as an ordinary module, which runs after the page has first painted.
// blocked.html loads the SAME file with blocking="render", so it runs before anything is
// painted and nothing on screen can move.
const banner = document.createElement("p");
banner.className = "banner";
banner.textContent =
  "A banner a late script added above everything already on screen";
// Both pages that load this script hold a <main>, so the lookup cannot come back empty.
/** @type {HTMLElement} */ (document.querySelector("main")).prepend(banner);
