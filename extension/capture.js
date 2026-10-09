// Captures the current page for docshelf: its URL, title and HTML, without
// the scripts, styles and media the server would throw away anyway.
globalThis.docshelfCapture = function docshelfCapture() {
  const root = document.documentElement.cloneNode(true);
  root
    .querySelectorAll(
      "script, style, noscript, iframe, svg, canvas, video, audio, link, template, object, embed",
    )
    .forEach((node) => node.remove());
  root.querySelectorAll("img[src^='data:']").forEach((node) => node.removeAttribute("src"));
  return {
    url: location.href,
    title: document.title,
    html: "<!doctype html>\n" + root.outerHTML,
  };
};
