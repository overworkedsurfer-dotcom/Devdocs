// Receives pages from the auto-send script, forwards them to the server,
// and keeps the auto-send registration in step with the settings.
import { api, getSettings, syncAutoSend } from "./common.js";

function resync() {
  syncAutoSend().catch((error) => console.error("docshelf: could not update auto-send", error));
}

chrome.runtime.onInstalled.addListener(resync);
chrome.runtime.onStartup.addListener(resync);
chrome.permissions.onAdded.addListener(resync);
chrome.permissions.onRemoved.addListener(resync);
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.sites) resync();
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== "docshelf:page" || !sender.tab) return false;
  forward(message.page, sender.tab.id).then(sendResponse, (error) =>
    sendResponse({ error: error.message }),
  );
  return true; // responds asynchronously
});

async function forward(page, tabId) {
  const settings = await getSettings();
  // The site may have been turned off since the script was injected.
  if (!settings.sites.includes(new URL(page.url).host)) return { status: "off" };
  try {
    const result = await api(settings, "api/pages", { method: "POST", body: page });
    const saved = result.status !== "skipped";
    await badge(tabId, saved ? "✓" : "–", "#2e7d32", `docshelf: ${result.status} ${result.path || ""}`);
    return result;
  } catch (error) {
    await badge(tabId, "!", "#c62828", `docshelf: ${error.message}`);
    throw error;
  }
}

async function badge(tabId, text, color, title) {
  try {
    await chrome.action.setBadgeBackgroundColor({ tabId, color });
    await chrome.action.setBadgeText({ tabId, text });
    await chrome.action.setTitle({ tabId, title });
    setTimeout(() => chrome.action.setBadgeText({ tabId, text: "" }).catch(() => {}), 5000);
  } catch {
    // The tab was closed.
  }
}
