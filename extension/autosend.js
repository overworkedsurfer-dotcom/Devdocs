// Auto-send, injected into the sites the user turned it on for. Sends each
// page once it has settled, and again whenever a single-page app moves to a
// new URL or the page's text changes.
(() => {
  if (globalThis.docshelfAutoSend) return;
  globalThis.docshelfAutoSend = true;

  const QUIET_MS = 1200; // send once the page has stopped changing for this long
  const MAX_WAIT_MS = 8000; // or after this long, for pages that never stop
  let timer = null;
  let observer = null;
  let currentUrl = pageUrl();
  let lastSent = "";

  function pageUrl() {
    return location.href.split("#")[0];
  }

  // FNV-1a, to notice when the text has not changed since the last send.
  function hash(text) {
    let h = 0x811c9dc5;
    for (let i = 0; i < text.length; i++) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    return (h >>> 0).toString(16);
  }

  function schedule() {
    clearTimeout(timer);
    observer?.disconnect();
    const deadline = Date.now() + MAX_WAIT_MS;
    const fire = () => {
      observer?.disconnect();
      observer = null;
      send();
    };
    const wait = () => {
      clearTimeout(timer);
      timer = setTimeout(fire, Math.max(0, Math.min(QUIET_MS, deadline - Date.now())));
    };
    observer = new MutationObserver(wait);
    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      characterData: true,
    });
    wait();
  }

  function send() {
    const key = pageUrl() + " " + hash(document.body?.innerText || "");
    if (key === lastSent) return;
    lastSent = key;
    try {
      chrome.runtime
        .sendMessage({ type: "docshelf:page", page: docshelfCapture() })
        .catch(() => {});
    } catch {
      // The extension was reloaded or removed; this page's script is orphaned.
    }
  }

  setInterval(() => {
    if (pageUrl() !== currentUrl) {
      currentUrl = pageUrl();
      schedule();
    }
  }, 1000);
  schedule();
})();
