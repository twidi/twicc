// Web Awesome sets `body { min-height: 100vh }`, and 100vh is the large viewport (browser toolbar
// hidden). The document is then taller than the visible area by the toolbar height. Firefox Android
// spends a drag on that extra height to hide its toolbar: the visible area grows, nothing scrolls.
// Chrome Android keeps its toolbar on screen in TwiCC (measured on phone and tablet UIs, even with a
// taller document): the same drag on a part of the app that does not scroll (a header) scrolls the
// whole document and uncovers an empty band below it.
//
// So Chromium browsers get a class on <html>, set before the first render, that sizes the body to the
// visible area (App.vue): the document never has anything to scroll. The rule stays for the others.
// Desktop Chromium is unaffected: without a collapsing toolbar, 100dvh equals 100vh.

export const STATIC_TOOLBAR_CLASS = 'static-browser-toolbar'

/** Whether the browser runs on Chromium. Read from the user agent: `userAgentData` and
 *  `virtualKeyboard` exist only in secure contexts, and TwiCC can be served over plain http. */
export function isChromium(userAgent) {
    return /\bChrome\//.test(userAgent)
}

/** Apply the class. To call before the first render, so the document never gets to scroll. */
export function initStaticBrowserToolbar() {
    if (isChromium(navigator.userAgent)) document.documentElement.classList.add(STATIC_TOOLBAR_CLASS)
}
