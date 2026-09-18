"use strict";

const state = {
  streamId: null,
  cursor: 0,
  socket: null,
  reconnectDelay: 1000,
  events: [],
  devices: [],
  conversations: [],
  selectedDeviceIds: new Set(),
  commonActions: new Set(),
  actionSubmitting: false,
  controlAuthRejected: false,
  controlAuthVerified: false,
  selectionRevision: 0,
  focusedRefreshTimer: null,
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

function hasControlAuthorization() {
  return Boolean(token()) && !state.controlAuthRejected;
}

function setControlStatus(message, kind = "unknown") {
  const status = byId("control-status");
  status.textContent = message;
  status.className = `control-status ${kind}`;
}

function focusTokenInput() {
  byId("console-token")?.scrollIntoView({behavior: "smooth", block: "center"});
  byId("console-token")?.focus({preventScroll: true});
}

function renderControlAuthorization() {
  const badge = byId("control-auth-badge");
  if (!token()) {
    setBadge(badge, "控制未授权", "unknown");
  } else if (state.controlAuthRejected) {
    setBadge(badge, "控制授权失效", "error");
  } else if (state.controlAuthVerified) {
    setBadge(badge, "控制已授权", "healthy");
  } else {
    setBadge(badge, "授权校验中", "degraded");
  }
  byId("cluster-stop").disabled = !hasControlAuthorization();
  updateQuickActionButtons();
  updateSelectionControls();
}

function requireControlAuthorization() {
  if (hasControlAuthorization()) return true;
  const message = state.controlAuthRejected
    ? "控制命令未发送：当前标签页令牌已失效，请重新应用控制台令牌。"
    : "控制命令未发送：当前标签页没有控制台令牌，请先在安全设置中应用令牌。";
  setControlStatus(message, "error");
  showToast(message);
  focusTokenInput();
  return false;
}

function bootstrapLoopbackToken() {
  const loopbackHosts = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);
  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const value = fragment.get("console_token");
  if (!value || !loopbackHosts.has(window.location.hostname)) return false;
  window.sessionStorage.setItem("otto-console-token", value);
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  return true;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (token()) headers.set("Authorization", `Bearer ${token()}`);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {...options, headers, cache: "no-store"});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = body.error || {};
    if (response.status === 401) {
      state.controlAuthRejected = true;
      state.controlAuthVerified = false;
      renderControlAuthorization();
      setControlStatus("控制命令未发送：服务端拒绝了当前标签页令牌，请重新授权。", "error");
      focusTokenInput();
    }
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

function statusKind(status) {
  if (["online", "idle", "running", "listening", "answering"].includes(status)) return "healthy";
  if (["stale", "connecting", "laughing", "recognizing", "starting"].includes(status)) return "degraded";
  if (["offline", "failed", "error"].includes(status)) return "error";
  return "unknown";
}

function renderDevices(data) {
  const items = data.items || [];
  state.devices = items;
  const currentIds = new Set(items.map((device) => device.device_id));
  [...state.selectedDeviceIds].forEach((deviceId) => {
    if (!currentIds.has(deviceId)) state.selectedDeviceIds.delete(deviceId);
  });
  setText("device-count", String(items.length));
  const table = byId("device-table");
  table.replaceChildren();
  if (!items.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.textContent = "暂无已上报 hello 和 heartbeat 的设备。";
    row.append(cell);
    table.append(row);
    updateSelection();
    return;
  }
  items.forEach((device) => {
    const row = document.createElement("tr");
    const selectCell = document.createElement("td");
    const selector = document.createElement("input");
    selector.type = "checkbox";
    selector.className = "device-selector";
    selector.checked = state.selectedDeviceIds.has(device.device_id);
    selector.setAttribute("aria-label", `选择 ${device.name || device.device_id}`);
    selector.addEventListener("change", () => {
      if (selector.checked) state.selectedDeviceIds.add(device.device_id);
      else state.selectedDeviceIds.delete(device.device_id);
      updateSelection();
    });
    selectCell.append(selector);

    const nameCell = document.createElement("td");
    const nameMeta = document.createElement("div");
    nameMeta.className = "device-meta";
    const name = document.createElement("strong");
    const address = document.createElement("small");
    name.textContent = device.name || "未命名";
    address.textContent = device.ip_address || "IP 未上报";
    nameMeta.append(name, address);
    nameCell.append(nameMeta);

    const idCell = document.createElement("td");
    const idCode = document.createElement("code");
    idCode.textContent = device.device_id;
    idCell.append(idCode);

    const statusCell = document.createElement("td");
    statusCell.textContent = device.status || "unknown";
    statusCell.className = `state-text ${statusKind(device.status)}`;

    const transportCell = document.createElement("td");
    transportCell.textContent = device.transport || "未知";

    const runtimeCell = document.createElement("td");
    const runtimeMeta = document.createElement("div");
    runtimeMeta.className = "device-meta";
    const action = document.createElement("span");
    const firmware = document.createElement("small");
    action.textContent = `${device.action_state || "unknown"}${device.current_action ? ` · ${device.current_action}` : ""}`;
    const display = device.display_image_alias || "显示未知";
    const volume = Number.isInteger(device.output_volume) ? `音量 ${device.output_volume}` : "音量未知";
    firmware.textContent = `固件 ${device.firmware_version || "未知"} · ${device.actions_count ?? 0} 动作 · ${display} · ${volume}`;
    runtimeMeta.append(action, firmware);
    runtimeCell.append(runtimeMeta);

    row.append(selectCell, nameCell, idCell, statusCell, transportCell, runtimeCell);
    table.append(row);
  });
  updateSelection();
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

function renderConversations(data) {
  const items = data.items || [];
  state.conversations = items;
  setText("conversation-count", String(data.active_count || 0));
  const grid = byId("conversation-grid");
  grid.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty-card";
    empty.textContent = "暂无设备会话。";
    grid.append(empty);
    return;
  }
  items.forEach((conversation) => {
    const card = document.createElement("article");
    card.className = "conversation-card";

    const heading = document.createElement("div");
    heading.className = "conversation-heading";
    const identity = document.createElement("div");
    const name = document.createElement("h3");
    const deviceId = document.createElement("code");
    name.textContent = conversation.name || conversation.device_id;
    deviceId.textContent = conversation.device_id;
    identity.append(name, deviceId);
    const badge = document.createElement("span");
    setBadge(badge, conversation.state || "waiting", statusKind(conversation.state));
    heading.append(identity, badge);

    const meta = document.createElement("div");
    meta.className = "conversation-meta";
    const session = conversation.session_id ? conversation.session_id.slice(0, 12) : "—";
    const updated = conversation.updated_at ? new Date(conversation.updated_at).toLocaleTimeString() : "—";
    meta.textContent = `${conversation.device_status || "unknown"} · ${conversation.transport || "无传输"} · session ${session} · ${updated}`;

    const user = conversationCopy("用户", conversation.user_text || conversation.user_partial || "等待用户说话…", "user");
    const assistant = conversationCopy("奶龙", conversation.assistant_text || "等待回答…", "assistant");
    card.append(heading, meta, user, assistant);

    if (conversation.tool_status || conversation.tool_name) {
      const tool = document.createElement("div");
      tool.className = "conversation-tool";
      tool.textContent = `工具 ${conversation.tool_name || "未知"} · ${conversation.tool_status || "unknown"}${conversation.action ? ` · 动作 ${conversation.action}` : ""}`;
      card.append(tool);
    }
    if (conversation.error_code) {
      const error = document.createElement("div");
      error.className = "conversation-error";
      error.textContent = `错误：${conversation.error_code}`;
      card.append(error);
    }
    grid.append(card);
  });
}

function conversationCopy(label, value, kind) {
  const container = document.createElement("div");
  container.className = `conversation-copy ${kind}`;
  const title = document.createElement("span");
  const copy = document.createElement("p");
  title.textContent = label;
  copy.textContent = value;
  container.append(title, copy);
  return container;
}

function selectedDevices() {
  return state.devices.filter((device) => state.selectedDeviceIds.has(device.device_id));
}

function updateSelection() {
  const selected = selectedDevices();
  setBadge(byId("selected-count"), `已选 ${selected.length} 台`, selected.length ? "healthy" : "unknown");
  setText(
    "selected-targets",
    selected.length
      ? selected.map((device) => `${device.name || "未命名"} (${device.device_id})`).join("、")
      : "请先在设备表中勾选明确目标；空选择不会解释成全部设备。",
  );
  updateSelectionControls();
  state.selectionRevision += 1;
  state.commonActions = new Set();
  updateQuickActionButtons("正在读取所选设备的共同动作…");
  loadCommonActions(selected, state.selectionRevision).catch((error) => showToast(error.message));
}

function updateSelectionControls() {
  const selected = selectedDevices();
  const authorized = hasControlAuthorization();
  byId("selected-stop").disabled = selected.length === 0 || state.actionSubmitting || !authorized;
  const conversationReady = authorized && selected.length > 0 && selected.every((device) => (
    device.status === "online" && device.capabilities?.conversation_control === true
  ));
  byId("conversation-start").disabled = !conversationReady;
  byId("conversation-stop").disabled = !conversationReady;
  setText(
    "conversation-control-hint",
    !authorized
      ? "当前标签页未获得控制授权，动作和对话按钮已锁定。"
      : conversationReady
      ? `已准备 ${selected.length} 台正式对话目标；设备ACK后仍以对话泳道状态为准。`
      : "所选设备必须全部在线并声明 conversation_control 能力。",
  );
}

function updateQuickActionButtons(hint = null) {
  const devices = selectedDevices();
  const allOnline = devices.length > 0 && devices.every((device) => device.status === "online");
  document.querySelectorAll("[data-quick-action]").forEach((button) => {
    button.disabled = (
      state.actionSubmitting
      || !hasControlAuthorization()
      || !allOnline
      || !state.commonActions.has(button.dataset.quickAction)
    );
  });
  byId("selected-stop").disabled = state.actionSubmitting || devices.length === 0 || !hasControlAuthorization();
  if (hint !== null) {
    setText("quick-action-hint", hint);
    return;
  }
  if (!devices.length) {
    setText("quick-action-hint", "先在上方勾选 EVA1、其他设备，或同时勾选多台设备。");
  } else if (!allOnline) {
    setText("quick-action-hint", "所选目标包含离线设备；动作按钮已锁定，请改选在线设备。");
  } else if (!state.commonActions.size) {
    setText("quick-action-hint", "所选设备没有共同动作，或动作目录尚未返回。");
  } else if (!hasControlAuthorization()) {
    setText("quick-action-hint", "设备已就绪，但当前标签页未授权；请先在安全设置中应用控制台令牌。");
  } else {
    setText("quick-action-hint", `可向 ${devices.length} 台选中设备发送共同动作；速度数值越小越快。`);
  }
}

async function loadCommonActions(devices, revision) {
  const select = byId("batch-action");
  const submit = byId("batch-submit");
  if (!devices.length) {
    state.commonActions = new Set();
    select.replaceChildren(new Option("请选择设备", ""));
    select.disabled = true;
    submit.disabled = true;
    updateQuickActionButtons();
    return;
  }
  if (devices.some((device) => device.status !== "online")) {
    state.commonActions = new Set();
    select.replaceChildren(new Option("所选目标包含离线设备", ""));
    select.disabled = true;
    submit.disabled = true;
    updateQuickActionButtons();
    return;
  }
  select.disabled = true;
  submit.disabled = true;
  const results = await Promise.allSettled(
    devices.map(async (device) => {
      const encodedId = encodeURIComponent(device.device_id);
      if (!Number.isInteger(device.actions_count) || device.actions_count === 0) {
        await api(`/api/v1/devices/${encodedId}/verify`, {method: "POST"});
      }
      return api(`/api/v1/devices/${encodedId}/actions`);
    }),
  );
  if (revision !== state.selectionRevision) return;
  const catalogs = results.map((result) => {
    if (result.status !== "fulfilled") return new Set();
    return new Set((result.value.items || []).map((item) => item.name).filter(Boolean));
  });
  let common = catalogs.length ? new Set(catalogs[0]) : new Set();
  catalogs.slice(1).forEach((catalog) => {
    common = new Set([...common].filter((name) => catalog.has(name)));
  });
  const previous = select.value;
  select.replaceChildren();
  if (!common.size) {
    state.commonActions = new Set();
    select.append(new Option("所选设备没有共同动作", ""));
    updateQuickActionButtons();
    return;
  }
  state.commonActions = common;
  [...common].sort().forEach((name) => select.append(new Option(name, name)));
  if (common.has(previous)) select.value = previous;
  else if (common.has("swing")) select.value = "swing";
  else if (common.has("laugh")) select.value = "laugh";
  select.disabled = false;
  submit.disabled = !hasControlAuthorization();
  updateQuickActionButtons();
}

function boundedInteger(id, minimum, maximum) {
  const value = Number(byId(id).value);
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${byId(id).closest("label")?.firstChild?.textContent?.trim() || id} 必须是 ${minimum}-${maximum} 的整数`);
  }
  return value;
}

function quickParameters(button) {
  const action = button.dataset.quickAction;
  const steps = boundedInteger("quick-steps", 1, 100);
  const speed = boundedInteger("quick-speed", 500, 1500);
  const amount = boundedInteger("quick-amount", 0, 170);
  const direction = Number(button.dataset.direction || 1);
  if (action === "laugh" || action === "home") {
    return {steps: 1, speed: 1000, direction: 1, amount: 0};
  }
  return {steps, speed, direction, amount};
}

function parseParameters() {
  let value;
  try {
    value = JSON.parse(byId("batch-parameters").value || "{}");
  } catch (error) {
    throw new Error(`动作参数不是有效 JSON：${error.message}`);
  }
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw new Error("动作参数必须是 JSON 对象");
  }
  return value;
}

function renderBatchResult(result) {
  byId("batch-result").textContent = JSON.stringify(result, null, 2);
}

async function submitBatchAction(action, parameters, label = action) {
  const devices = selectedDevices();
  if (!devices.length || !action) return showToast("请先选择设备和共同动作");
  if (!state.commonActions.has(action)) return showToast("所选设备不共同支持这个动作");
  if (!requireControlAuthorization()) return;
  const targets = devices.map((device) => `${device.name || "未命名"} (${device.device_id})`).join("、");
  if (!window.confirm(`确认让以下设备执行“${label}”？\n${targets}`)) return;
  state.actionSubmitting = true;
  setControlStatus(`正在向 ${devices.length} 台设备提交“${label}”…`, "degraded");
  updateQuickActionButtons("动作正在提交，请等待设备确认…");
  try {
    const result = await api("/api/v1/commands/actions/batch", {
      method: "POST",
      body: JSON.stringify({
        device_ids: devices.map((device) => device.device_id),
        action,
        parameters,
        confirmation: true,
      }),
    });
    renderBatchResult(result);
    state.controlAuthVerified = true;
    renderControlAuthorization();
    setControlStatus(`“${label}”已进入服务端：${result.accepted}/${result.requested} 台接受。`, "healthy");
    showToast(`“${label}”已提交：${result.accepted}/${result.requested} 台接受`);
    scheduleFocusedRefresh(true);
  } catch (error) {
    setControlStatus(`“${label}”提交失败：${error.message}`, "error");
    showToast(error.message);
  } finally {
    state.actionSubmitting = false;
    updateQuickActionButtons();
  }
}

function addEvent(frame) {
  if (!frame || frame.type !== "event") return;
  state.cursor = Math.max(state.cursor, Number(frame.cursor || 0));
  state.events.push(frame);
  state.events = state.events.slice(-50);
  setText("event-cursor", String(state.cursor));
  renderEvents();
  const topic = frame.event?.topic || "";
  if (topic.startsWith("voice.") || topic.startsWith("tts.") || topic.startsWith("audio.input.")) {
    scheduleFocusedRefresh(false);
  }
  if (topic.startsWith("device.") || topic.startsWith("robot.")) {
    scheduleFocusedRefresh(true);
  }
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

function scheduleFocusedRefresh(includeDevices) {
  if (state.focusedRefreshTimer) window.clearTimeout(state.focusedRefreshTimer);
  state.focusedRefreshTimer = window.setTimeout(async () => {
    state.focusedRefreshTimer = null;
    const tasks = [api("/api/v1/conversations").then(renderConversations)];
    if (includeDevices) tasks.push(api("/api/v1/devices").then(renderDevices));
    await Promise.allSettled(tasks);
  }, 180);
}

async function refresh() {
  const requests = [
    ["health", "/api/v1/health", renderHealth],
    ["system", "/api/v1/system/status", renderSystem],
    ["mqtt", "/api/v1/mqtt/status", renderMqtt],
    ["devices", "/api/v1/devices", renderDevices],
    ["conversations", "/api/v1/conversations", renderConversations],
    ["firmware", "/api/v1/firmware", renderFirmware],
    ["settings", "/api/v1/settings", renderSettings],
  ];
  const results = await Promise.allSettled(requests.map(([, path]) => api(path)));
  let failures = 0;
  results.forEach((result, index) => {
    if (result.status === "fulfilled") requests[index][2](result.value);
    else failures += 1;
  });
  if (failures) setBadge(byId("connection-badge"), `${failures} 项读取失败`, "error");
}

function connectEvents() {
  if (state.socket) state.socket.close();
  if (!token()) {
    state.socket = null;
    setBadge(byId("stream-badge"), "需要控制授权", "unknown");
    renderControlAuthorization();
    return;
  }
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
    state.controlAuthRejected = false;
    state.controlAuthVerified = true;
    renderControlAuthorization();
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
      scheduleFocusedRefresh(true);
    } else if (frame.type === "event") {
      addEvent(frame);
    } else if (frame.type === "resync_required") {
      state.streamId = null;
      state.cursor = 0;
      socket.close();
    }
  });
  socket.addEventListener("close", (event) => {
    if (state.socket !== socket) return;
    if (event.code === 4401) {
      state.controlAuthRejected = true;
      state.controlAuthVerified = false;
      renderControlAuthorization();
      setControlStatus("事件流拒绝了当前令牌；控制按钮已锁定，请重新授权。", "error");
      setBadge(byId("stream-badge"), "授权失败", "error");
      return;
    }
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
  state.controlAuthRejected = false;
  state.controlAuthVerified = false;
  renderControlAuthorization();
  setControlStatus(
    value ? "正在校验当前标签页的控制授权…" : "控制令牌已清除，动作和对话按钮已锁定。",
    value ? "degraded" : "unknown",
  );
  connectEvents();
  refresh().catch((error) => showToast(error.message));
});

byId("select-online").addEventListener("click", () => {
  state.selectedDeviceIds = new Set(
    state.devices.filter((device) => device.status === "online").map((device) => device.device_id),
  );
  renderDevices({items: state.devices});
});

byId("clear-selection").addEventListener("click", () => {
  state.selectedDeviceIds.clear();
  renderDevices({items: state.devices});
});

byId("batch-action-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const devices = selectedDevices();
  const action = byId("batch-action").value;
  if (!devices.length || !action) return showToast("请先选择设备和共同动作");
  let parameters;
  try {
    parameters = parseParameters();
  } catch (error) {
    return showToast(error.message);
  }
  await submitBatchAction(action, parameters);
});

document.querySelectorAll("[data-quick-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    let parameters;
    try {
      parameters = quickParameters(button);
    } catch (error) {
      return showToast(error.message);
    }
    await submitBatchAction(
      button.dataset.quickAction,
      parameters,
      button.textContent.trim(),
    );
  });
});

byId("selected-stop").addEventListener("click", async () => {
  const devices = selectedDevices();
  if (!devices.length) return;
  if (!requireControlAuthorization()) return;
  const targets = devices.map((device) => `${device.name || "未命名"} (${device.device_id})`).join("、");
  if (!window.confirm(`确认停止以下明确目标？\n${targets}`)) return;
  try {
    const result = await api("/api/v1/commands/stops/batch", {
      method: "POST",
      body: JSON.stringify({
        device_ids: devices.map((device) => device.device_id),
        confirmation: true,
      }),
    });
    renderBatchResult(result);
    setControlStatus(`停止命令已进入服务端：${result.accepted}/${result.requested} 台接受。`, "healthy");
    showToast(`停止请求已提交：${result.accepted}/${result.requested} 台接受`);
    scheduleFocusedRefresh(true);
  } catch (error) {
    setControlStatus(`停止命令提交失败：${error.message}`, "error");
    showToast(error.message);
  }
});

async function submitConversationControl(command) {
  const devices = selectedDevices();
  if (!devices.length) return;
  if (!requireControlAuthorization()) return;
  const targets = devices.map((device) => `${device.name || "未命名"} (${device.device_id})`).join("、");
  const verb = command === "start" ? "进入循环对话" : "退出循环对话";
  if (!window.confirm(`确认让以下明确目标${verb}？\n${targets}`)) return;
  try {
    const result = await api("/api/v1/commands/conversations/batch", {
      method: "POST",
      body: JSON.stringify({
        device_ids: devices.map((device) => device.device_id),
        command,
        confirmation: true,
      }),
    });
    renderBatchResult(result);
    setControlStatus(`${verb}命令已进入服务端：${result.accepted}/${result.requested} 台已 ACK。`, "healthy");
    showToast(`${verb}请求：${result.accepted}/${result.requested} 台已ACK`);
    scheduleFocusedRefresh(true);
  } catch (error) {
    setControlStatus(`${verb}命令提交失败：${error.message}`, "error");
    showToast(error.message);
  }
}

byId("conversation-start").addEventListener("click", () => submitConversationControl("start"));
byId("conversation-stop").addEventListener("click", () => submitConversationControl("stop"));

byId("cluster-stop").addEventListener("click", async () => {
  if (!requireControlAuthorization()) return;
  if (!window.confirm("确认向所有已解析设备发送紧急停止？这会拆成每台设备的独立命令。")) return;
  try {
    const result = await api("/api/v1/cluster/stop", {method: "POST"});
    renderBatchResult(result);
    showToast("集群停止请求已提交");
  } catch (error) {
    showToast(error.message);
  }
});

const loopbackAuthorized = bootstrapLoopbackToken();
byId("console-token").value = token();
renderControlAuthorization();
if (loopbackAuthorized) {
  setControlStatus("本机令牌已载入，正在校验事件流授权…", "degraded");
  showToast("本机控制台令牌已载入，正在校验授权。");
}
refresh().catch((error) => showToast(error.message));
connectEvents();
window.setInterval(() => refresh().catch(() => {}), 15000);
