// Sends instagram.com cookies to the Saver servers so the bot uses a session
// created by a person instead of logging in from a server IP.

const PUSH_PATH = "/api/instagram/session";
const PERIOD_MINUTES = 360;
const MIN_GAP_MS = 30 * 60 * 1000;
const DEFAULT_SERVERS = [
  { url: "https://dev.saver.tg", token: "" },
  { url: "https://saver.tg", token: "" },
];

async function digest(text) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function instagramCookies() {
  const list = await chrome.cookies.getAll({ domain: "instagram.com" });
  return Object.fromEntries(list.map((c) => [c.name, c.value]));
}

async function pushToServer(server, cookies) {
  const url = server.url.replace(/\/+$/, "") + PUSH_PATH;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Saver-Token": server.token },
      body: JSON.stringify({ cookies }),
    });
    const body = await response.json().catch(() => ({}));
    if (response.ok) {
      return { url: server.url, ok: true, text: `ok, ${body.username}` };
    }
    return { url: server.url, ok: false, text: `${response.status} ${body.detail || ""}`.trim() };
  } catch (error) {
    return { url: server.url, ok: false, text: String(error) };
  }
}

async function push(reason) {
  const state = await chrome.storage.local.get({
    servers: DEFAULT_SERVERS,
    lastSession: "",
    lastPush: 0,
  });
  const cookies = await instagramCookies();
  if (!cookies.sessionid) {
    await chrome.storage.local.set({
      status: { at: Date.now(), results: [{ url: "", ok: false, text: "not logged in to Instagram in this browser" }] },
    });
    return;
  }
  const session = await digest(cookies.sessionid);
  const sameSession = session === state.lastSession;
  if (reason !== "manual" && sameSession && Date.now() - state.lastPush < MIN_GAP_MS) {
    return;
  }
  const servers = state.servers.filter((s) => s.url && s.token);
  const results = await Promise.all(servers.map((s) => pushToServer(s, cookies)));
  await chrome.storage.local.set({
    lastSession: session,
    lastPush: Date.now(),
    status: { at: Date.now(), reason, results },
  });
}

chrome.cookies.onChanged.addListener(({ cookie, removed }) => {
  if (!removed && cookie.name === "sessionid" && cookie.domain.endsWith("instagram.com")) {
    push("session changed");
  }
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "push") {
    push("schedule");
  }
});

function schedule() {
  chrome.alarms.create("push", { delayInMinutes: 1, periodInMinutes: PERIOD_MINUTES });
}

chrome.runtime.onInstalled.addListener(schedule);
chrome.runtime.onStartup.addListener(schedule);

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (message === "push") {
    push("manual").then(() => reply(true));
    return true;
  }
  return false;
});
