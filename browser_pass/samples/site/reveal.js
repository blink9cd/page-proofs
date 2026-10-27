// A late script that moves nothing: it reveals the paragraph clean.html holds in the layout
// unseen, so the space it fills was already reserved before the page first painted.
/** @type {HTMLElement} */ (document.querySelector(".reserve")).hidden = false;
