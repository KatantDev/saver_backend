const DEFAULT_SERVERS = [
  { url: "https://dev.saver.tg", token: "" },
  { url: "https://saver.tg", token: "" },
];

function render(servers) {
  const box = document.getElementById("servers");
  box.textContent = "";
  for (const server of servers) {
    const row = document.createElement("div");
    row.className = "server";
    const url = document.createElement("input");
    url.placeholder = "https://dev.saver.tg";
    url.value = server.url;
    const token = document.createElement("input");
    token.type = "password";
    token.placeholder = "токен";
    token.value = server.token;
    row.append(url, token);
    box.append(row);
  }
}

function readServers() {
  return Array.from(document.querySelectorAll(".server"), (row) => {
    const [url, token] = row.querySelectorAll("input");
    return { url: url.value.trim(), token: token.value.trim() };
  });
}

function showStatus(status) {
  const box = document.getElementById("status");
  if (!status) {
    box.textContent = "Ещё не отправляли";
    return;
  }
  const lines = status.results.map((r) => `${r.ok ? "OK" : "Ошибка"} ${r.url} ${r.text}`.trim());
  if (!lines.length) {
    lines.push("Нет серверов с токеном");
  }
  box.textContent = `${new Date(status.at).toLocaleString()}\n${lines.join("\n")}`;
}

async function load() {
  const { servers, status } = await chrome.storage.local.get({ servers: DEFAULT_SERVERS, status: null });
  render(servers);
  showStatus(status);
}

document.getElementById("save").addEventListener("click", async () => {
  await chrome.storage.local.set({ servers: readServers() });
  document.getElementById("status").textContent = "Сохранено";
});

document.getElementById("push").addEventListener("click", async () => {
  await chrome.storage.local.set({ servers: readServers() });
  document.getElementById("status").textContent = "Отправляю...";
  await chrome.runtime.sendMessage("push");
  const { status } = await chrome.storage.local.get({ status: null });
  showStatus(status);
});

load();
