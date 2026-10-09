// Settings, permissions and the server API, shared by the popup and the
// background service worker.

export const DEFAULTS = { server: "http://localhost:8000", token: "", sites: [] };
const SCRIPT_ID = "docshelf-autosend";

export async function getSettings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(DEFAULTS)) };
}

export function saveSettings(patch) {
  return chrome.storage.local.set(patch);
}

// Host permission patterns. Match patterns cannot name a port, so a pattern
// covers every port on the host; the exact host (with port) is checked
// against the settings before anything is sent.
export function serverPattern(server) {
  const url = new URL(server);
  return `${url.protocol}//${url.hostname}/*`;
}

export function sitePattern(host) {
  return `*://${new URL(`http://${host}`).hostname}/*`;
}

// The host (with any port) of an http(s) URL, or null for anything else.
export function webHost(url) {
  try {
    const parsed = new URL(url);
    return /^https?:$/.test(parsed.protocol) ? parsed.host : null;
  } catch {
    return null;
  }
}

export async function hasSitePermission(host) {
  return chrome.permissions.contains({ origins: [sitePattern(host)] });
}

// Calls the docshelf server; throws an Error with a readable message.
export async function api(settings, path, { method = "GET", body } = {}) {
  const base = settings.server.replace(/\/*$/, "/");
  const url = new URL(path.replace(/^\//, ""), base);
  const headers = { Authorization: `Bearer ${settings.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    throw new Error(
      `Can't reach ${settings.server}. Check the address and that docshelf is running.`,
    );
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `The server answered HTTP ${response.status}.`);
  return data;
}

// Registers the auto-send content script for every enabled site the
// extension has permission for, and unregisters it when there are none.
// Calls are queued: Chrome rejects overlapping registrations of one script.
let queue = Promise.resolve();
export function syncAutoSend() {
  queue = queue.then(registerAutoSend, registerAutoSend);
  return queue;
}

async function registerAutoSend() {
  const { sites } = await getSettings();
  const matches = [];
  for (const host of sites) {
    if (await hasSitePermission(host)) matches.push(sitePattern(host));
  }
  const existing = await chrome.scripting.getRegisteredContentScripts({ ids: [SCRIPT_ID] });
  if (existing.length) await chrome.scripting.unregisterContentScripts({ ids: [SCRIPT_ID] });
  if (!matches.length) return;
  await chrome.scripting.registerContentScripts([
    {
      id: SCRIPT_ID,
      matches: [...new Set(matches)],
      js: ["capture.js", "autosend.js"],
      runAt: "document_idle",
    },
  ]);
}
