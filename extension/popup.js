import {
  api,
  getSettings,
  hasSitePermission,
  saveSettings,
  serverPattern,
  sitePattern,
  webHost,
} from "./common.js";

const $ = (id) => document.getElementById(id);
let settings;
let tab;
let host;

function show(message, kind = "") {
  $("result").textContent = message;
  $("result").className = `result ${kind}`;
}

function describe(result) {
  switch (result.status) {
    case "added":
      return `Saved to ${result.path}`;
    case "updated":
      return `Updated ${result.path}`;
    case "unchanged":
      return `Already saved: ${result.path}`;
    case "skipped":
      return "Not saved: this page has no readable text.";
    default:
      return result.status;
  }
}

async function checkConnection() {
  const pill = $("connection");
  if (!settings.token) {
    pill.textContent = "Not set up";
    pill.className = "pill bad";
    return;
  }
  try {
    const status = await api(settings, "api/status");
    pill.textContent = `Connected · ${status.files} files`;
    pill.className = "pill ok";
  } catch (error) {
    pill.textContent = "Not connected";
    pill.className = "pill bad";
    pill.title = error.message;
  }
}

function renderSites() {
  $("site-count").textContent = settings.sites.length;
  const list = $("sites");
  list.replaceChildren();
  for (const site of settings.sites) {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = site;
    const remove = document.createElement("button");
    remove.textContent = "×";
    remove.title = `Stop auto-sending ${site}`;
    remove.addEventListener("click", () => setAutoSend(site, false));
    item.append(name, remove);
    list.append(item);
  }
}

// Turning a site on asks Chrome for access to it. The request has to start
// before anything is awaited (Chrome only allows it during the click), and
// the popup can close while Chrome asks, so the site is saved first. The
// background worker registers auto-send whenever the site list or the
// permissions change.
async function setAutoSend(site, on) {
  const permission = on ? chrome.permissions.request({ origins: [sitePattern(site)] }) : null;
  const sites = settings.sites.filter((s) => s !== site);
  if (on) sites.push(site);
  settings.sites = sites;
  await saveSettings({ sites });
  if (on && !(await permission)) {
    settings.sites = settings.sites.filter((s) => s !== site);
    await saveSettings({ sites: settings.sites });
    show("Chrome didn't allow access to this site, so auto-send is off.", "bad");
  }
  renderSites();
  await renderPage();
  if (on && site === host && settings.sites.includes(site)) {
    await sendPage();
    // Follow this tab's navigation from now on, without waiting for a reload.
    await chrome.scripting
      .executeScript({ target: { tabId: tab.id }, files: ["capture.js", "autosend.js"] })
      .catch(() => {});
  }
}

async function sendPage() {
  show("Sending…");
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["capture.js"] });
    const [{ result: page }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => globalThis.docshelfCapture(),
    });
    const result = await api(settings, "api/pages", { method: "POST", body: page });
    show(describe(result), result.status === "skipped" ? "bad" : "ok");
  } catch (error) {
    show(error.message, "bad");
  }
}

async function crawlSite() {
  show("Starting crawl…");
  try {
    let job = await api(settings, "api/crawl", {
      method: "POST",
      body: { url: tab.url, max_pages: 200 },
    });
    while (job.status === "queued" || job.status === "running") {
      show(`Crawling ${job.scope} on the server: ${job.saved} saved, ${job.queued} queued…`);
      await new Promise((resolve) => setTimeout(resolve, 1500));
      job = await api(settings, `api/crawl/${job.id}`);
    }
    const kind = job.status === "done" ? "ok" : "bad";
    show(`Crawl ${job.status}: ${job.saved} saved, ${job.unchanged} unchanged.`, kind);
  } catch (error) {
    show(error.message, "bad");
  }
}

async function renderPage() {
  const ready = Boolean(host && settings.token);
  $("site").textContent = host || "This page can't be saved";
  $("auto").checked = Boolean(
    host && settings.sites.includes(host) && (await hasSitePermission(host)),
  );
  for (const id of ["auto", "send", "crawl"]) $(id).disabled = !ready;
  if (host && !settings.token) show("Set the server address and token below first.");
}

async function saveServer() {
  let server = $("server").value.trim().replace(/\/+$/, "");
  if (server && !/^https?:\/\//i.test(server)) server = `http://${server}`;
  let pattern;
  try {
    pattern = serverPattern(server);
  } catch {
    show("That address doesn't look right, e.g. http://192.168.1.20:8000", "bad");
    return;
  }
  const permission = chrome.permissions.request({ origins: [pattern] });
  settings.server = server;
  settings.token = $("token").value.trim();
  await saveSettings({ server: settings.server, token: settings.token });
  $("server").value = server;
  if (!(await permission)) {
    show("Chrome didn't allow access to the server address.", "bad");
    return;
  }
  show("Saved.", "ok");
  await renderPage();
  await checkConnection();
}

async function init() {
  settings = await getSettings();
  $("server").value = settings.server;
  $("token").value = settings.token;
  if (!settings.token) $("server-settings").open = true;

  // ?tab=<id> opens the popup as a page for another tab, for debugging.
  const tabId = new URLSearchParams(location.search).get("tab");
  tab = tabId
    ? await chrome.tabs.get(Number(tabId))
    : (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
  host = webHost(tab?.url);

  $("auto").addEventListener("change", (event) => setAutoSend(host, event.target.checked));
  $("send").addEventListener("click", sendPage);
  $("crawl").addEventListener("click", crawlSite);
  $("save").addEventListener("click", saveServer);

  renderSites();
  await renderPage();
  await checkConnection();
}

init();
