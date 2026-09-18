"use strict";

const state = {
  streamId: null,
  cursor: 0,
  socket: null,
  reconnectDelay: 1000,
  events: [],
};

const byId = (id) => document.getElementById(id);

function setText(id, value) {
  byId(id).textContent = value ?? "—";
}

function setBadge(element, label, kind) {
  element.textContent = label;
  element.className = `badge ${kind}`;
}

function showToast(message) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.setTimeout(() => toast.classList.add("hidden"), 5000);
}

function token() {
  return window.sessionStorage.getItem("otto-console-token") || "";
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (token()) headers.set("Authorization", `Bearer ${token()}`);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {...options, headers, cache: "no-store"});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = body.error || {};
    throw new Error(`${error.code || response.status}: ${error.message || response.statusText}`);
  }
  return body;
}

function renderHealth(health) {
  const healthy = health.status === "healthy";
  setBadge(byId("connection-badge"), healthy ? "系统正常" : "系统降级", healthy ? "healthy" : "degraded");
  setText("server-state", healthy ? "正常" : "降级");
  setText("server-version", `v${health.version}`);
  setText("health-checked-at", new Date(health.checked_at).toLocaleString());
  const table = byId("component-table");
  table.replaceChildren();
  Object.entries(health.components).forEach(([name, component]) => {
    const row = document.createElement("tr");
    const nameCell = document.createElement("td");
    const stateCell = document.createElement("td");
    const detailCell = document.createElement("td");
    nameCell.textContent = name;
    const ok = component.healthy === true;
    stateCell.textContent = component.state || (ok ? "healthy" : "unhealthy");
    stateCell.className = `state-text ${ok ? "healthy" : (component.enabled === false ? "unknown" : "degraded")}`;
    const detail = {...component};
    delete detail.healthy;
    delete detail.state;
    detailCell.textContent = JSON.stringify(detail);
    row.append(nameCell, stateCell, detailCell);
    table.append(row);
  });
}

function renderSystem(system) {
  setText("system-summary", `${system.web_url} · ${system.discovery_hostname} · 已运行 ${Math.floor(system.uptime_seconds)} 秒`);
  setText("footer-clock", new Date(system.current_time).toLocaleString());
}

function renderMqtt(mqtt) {
  setText("mqtt-state", mqtt.healthy ? "运行中" : mqtt.state);
  setText("mqtt-detail", `${mqtt.connected_clients} 个连接 · ${mqtt.authentication} · :${mqtt.port}`);
}

function renderDevices(data) {
  const items = data.items || [];
  setText("device-count", String(items.length));
  const table = byId("device-table");
  table.replaceChildren();
  if (!items.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.textContent = "暂无已上报 hello 和 heartbeat 的设备。";
    row.append(cell);
    table.append(row);
    return;
  }
  items.forEach((device) => {
    const row = document.createElement("tr");
    [device.name, device.device_id, device.status, device.transport || "未知"].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    table.append(row);
  });
}

function renderFirmware(firmware) {
  setText("firmware-state", firmware.available ? "可下载" : `不可用（${firmware.reason || "未知"}）`);
  setText("firmware-version", firmware.version);
  setText("firmware-hardware", firmware.target_hardware);
  setText("firmware-size", firmware.size === null ? "—" : `${firmware.size} bytes`);
  setText("firmware-sha", firmware.sha256);
  byId("firmware-download").classList.toggle("hidden", !firmware.available);
}

function renderSettings(settings) {
  byId("settings-view").textContent = JSON.stringify(settings, null, 2);
}

function addEvent(frame) {
  if (!frame || frame.type !== "event") return;
  state.cursor = Math.max(state.cursor, Number(frame.cursor || 0));
  state.events.push(frame);
  state.events = state.events.slice(-50);
  setText("event-cursor", String(state.cursor));
  renderEvents();
}

function renderEvents() {
  const list = byId("event-list");
  list.replaceChildren();
  if (!state.events.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "尚无事件";
    list.append(empty);
    return;
  }
  [...state.events].reverse().forEach((frame) => {
    const event = frame.event || {};
    const item = document.createElement("li");
    const cursor = document.createElement("span");
    const topic = document.createElement("strong");
    const body = document.createElement("code");
    cursor.textContent = `#${frame.cursor}`;
    topic.textContent = event.topic || "unknown";
    body.textContent = JSON.stringify(event.payload || {});
    item.append(cursor, topic, body);
    list.append(item);
  });
}

async function refresh() {
  const requests = [
    ["health", "/api/v1/health", renderHealth],
    ["system", "/api/v1/system/status", renderSystem],
    ["mqtt", "/api/v1/mqtt/status", renderMqtt],
    ["devices", "/api/v1/devices", renderDevices],
    ["firmware", "/api/v1/firmware", renderFirmware],
    ["settings", "/api/v1/settings", renderSettings],
  ];
  const results = await Promise.allSettled(requests.map(([, path]) => api(path)));
  let failures = 0;
  results.forEach((result, index) => {
    if (result.status === "fulfilled") requests[index][2](result.value);
    else failures += 1;
  });
  if (failures) {
    setBadge(byId("connection-badge"), `${failures} 项读取失败`, "error");
  }
}

function connectEvents() {
  if (state.socket) state.socket.close();
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const query = new URLSearchParams();
  if (state.streamId) query.set("stream_id", state.streamId);
  if (state.cursor) query.set("after", String(state.cursor));
  const protocols = token() ? ["otto-console", token()] : undefined;
  const url = `${scheme}://${window.location.host}/api/v1/events/stream?${query}`;
  const socket = protocols ? new WebSocket(url, protocols) : new WebSocket(url);
  state.socket = socket;
  setBadge(byId("stream-badge"), "连接中", "unknown");

  socket.addEventListener("open", () => {
    state.reconnectDelay = 1000;
    setBadge(byId("stream-badge"), "已连接", "healthy");
  });
  socket.addEventListener("message", (message) => {
    const frame = JSON.parse(message.data);
    if (frame.type === "snapshot") {
      state.streamId = frame.stream_id;
      state.cursor = Number(frame.cursor || 0);
      state.events = frame.events || [];
      setText("event-cursor", String(state.cursor));
      setText("event-stream", state.streamId.slice(0, 8));
      renderEvents();
    } else if (frame.type === "event") {
      addEvent(frame);
    } else if (frame.type === "resync_required") {
      state.streamId = null;
      state.cursor = 0;
      socket.close();
    }
  });
  socket.addEventListener("close", () => {
    if (state.socket !== socket) return;
    setBadge(byId("stream-badge"), "已断开，等待重连", "degraded");
    window.setTimeout(connectEvents, state.reconnectDelay);
    state.reconnectDelay = Math.min(state.reconnectDelay * 2, 10000);
  });
  socket.addEventListener("error", () => socket.close());
}

byId("refresh-button").addEventListener("click", () => refresh().catch((error) => showToast(error.message)));

byId("token-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = byId("console-token").value.trim();
  if (value) window.sessionStorage.setItem("otto-console-token", value);
  else window.sessionStorage.removeItem("otto-console-token");
  connectEvents();
  refresh().catch((error) => showToast(error.message));
});

byId("cluster-stop").addEventListener("click", async () => {
  if (!window.confirm("确认向所有已解析设备发送紧急停止？当前阶段未接入设备时不会执行动作。")) return;
  try {
    await api("/api/v1/cluster/stop", {method: "POST"});
    showToast("停止请求已提交");
  } catch (error) {
    showToast(error.message);
  }
});

byId("console-token").value = token();
refresh().catch((error) => showToast(error.message));
connectEvents();
window.setInterval(() => refresh().catch(() => {}), 15000);
