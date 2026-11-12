"use strict";

const PROTOCOL_VERSION = window.NEUROBRIDGE_VERSION.protocolVersion;
const SUBPROTOCOL = "neurobridge.v1";
let socket = null;
let initialStatusRequestId = null;
let subscriptions = new Map();
let logEntries = [];
let activeSource = null;
const sourceLabels = { serial: "耳机串口", bluetooth: "头环蓝牙", unknown: "来源未指定" };
const configuredSource = window.NEUROBRIDGE_B_CLIENT_SOURCE;
const configuredEndpoint = window.NEUROBRIDGE_B_CLIENT_ENDPOINT;
const fixedSource = configuredSource === "serial" || configuredSource === "bluetooth";
const spectrumBands = ["delta", "theta", "alpha", "lowBeta", "highBeta", "beta", "gamma"];
const algorithmMetrics = {
  eeg: [
    ["eeg.quality", "脑电质量"], ["attention", "注意力"], ["relaxation", "放松度"], ["pleasure", "愉悦度"],
    ...spectrumBands.map((band) => [`eeg.bandPower.${band}`, `${band} 能量`]),
    ["flow.meditation", "冥想"], ["flow.meditationTips", "冥想提示"], ["sleep.updated", "睡眠结果更新"],
    ["sleep.degree", "睡眠程度"], ["sleep.state", "睡眠状态"], ["sleep.stage", "睡眠分期"], ["sleep.spindle", "睡眠纺锤"],
  ],
  hr: [
    ["hr.value", "心率（bpm）"], ["hr.hrv", "HRV"], ["pressure", "压力"], ["coherence", "和谐度"], ["arousal", "激活度"],
  ],
};

const elements = {
  endpoint: document.querySelector("#endpoint"),
  dataSource: document.querySelector("#dataSource"),
  sourceHint: document.querySelector("#sourceHint"),
  sourcePanels: document.querySelector("#sourcePanels"),
  sourcePanelTemplate: document.querySelector("#sourcePanelTemplate"),
  connectButton: document.querySelector("#connectButton"),
  disconnectButton: document.querySelector("#disconnectButton"),
  connectionDot: document.querySelector("#connectionDot"),
  connectionLabel: document.querySelector("#connectionLabel"),
  getStatusButton: document.querySelector("#getStatusButton"),
  getLatestButton: document.querySelector("#getLatestButton"),
  subscribeButton: document.querySelector("#subscribeButton"),
  unsubscribeButton: document.querySelector("#unsubscribeButton"),
  subscriptionSelect: document.querySelector("#subscriptionSelect"),
  includeInvalid: document.querySelector("#includeInvalid"),
  customRequest: document.querySelector("#customRequest"),
  sendCustomButton: document.querySelector("#sendCustomButton"),
  clearLogButton: document.querySelector("#clearLogButton"),
  copyLogButton: document.querySelector("#copyLogButton"),
  log: document.querySelector("#log"),
};

function createSourcePanel(source) {
  const article = elements.sourcePanelTemplate.content.firstElementChild.cloneNode(true);
  article.dataset.source = source;
  article.hidden = source === "unknown";
  const fields = Object.fromEntries(
    [...article.querySelectorAll("[data-field]")].map((field) => [field.dataset.field, field]),
  );
  const algorithmGroups = [...article.querySelectorAll("[data-algorithm]")].map((section) => {
    const stream = section.dataset.algorithm;
    const list = section.querySelector(".algorithm-metrics");
    const metrics = algorithmMetrics[stream].map(([key, title]) => {
      const item = document.createElement("div");
      const label = document.createElement("dt");
      const output = document.createElement("dd");
      label.textContent = title;
      output.textContent = "—";
      item.append(label, output);
      list.append(item);
      return { path: key.split("."), output };
    });
    return { section, stream, metrics, time: section.querySelector("[data-window-time]"),
      status: section.querySelector("[data-window-status]") };
  });
  fields.title.textContent = sourceLabels[source];
  elements.sourcePanels.append(article);
  return { article, fields, algorithmGroups, channels: {}, bandPower: {} };
}

const panels = Object.fromEntries(Object.keys(sourceLabels).map((source) => [source, createSourcePanel(source)]));
if (typeof configuredEndpoint === "string") {
  elements.endpoint.value = configuredEndpoint;
}
if (fixedSource) {
  elements.endpoint.readOnly = true;
  elements.dataSource.value = configuredSource;
  elements.dataSource.disabled = true;
  document.querySelector('input[name="subscribeStream"][value="status"]').checked = false;
  elements.sourceHint.textContent = "数据来源由本机网关配置提供。";
}

function isConnected() {
  return socket && socket.readyState === WebSocket.OPEN;
}

function createRequestId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `btest-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function setConnectionState(state, label) {
  elements.connectionLabel.textContent = label;
  elements.connectionDot.className = `dot dot-${state}`;
  elements.connectButton.disabled = state === "on" || state === "wait";
  elements.disconnectButton.disabled = state === "off";
  elements.endpoint.disabled = state !== "off";
  elements.dataSource.disabled = fixedSource || state !== "off";
}

function timeLabel(date = new Date()) {
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function appendLog(kind, label, body) {
  const entry = {
    time: timeLabel(), kind, label,
    body: typeof body === "string" ? body : JSON.stringify(body, (key, value) =>
      key === "bytesBase64" ? undefined : value, 2),
  };
  logEntries.push(entry);
  const node = document.createElement("div");
  node.className = `log-entry ${kind}`;
  const time = document.createElement("span");
  time.className = "log-time";
  time.textContent = entry.time;
  const tag = document.createElement("span");
  tag.className = "log-tag";
  tag.textContent = label;
  const content = document.createElement("pre");
  content.className = "log-body";
  content.textContent = entry.body;
  node.append(time, tag, content);
  elements.log.append(node);
  if (logEntries.length > 200) {
    logEntries.shift();
    elements.log.firstElementChild.remove();
  }
  elements.log.scrollTop = elements.log.scrollHeight;
}

function readableTimestamp(timestampMs) {
  if (typeof timestampMs !== "number") return "—";
  const date = new Date(timestampMs);
  return Number.isNaN(date.getTime()) ? String(timestampMs) : date.toLocaleString("zh-CN", { hour12: false });
}

const algorithmTimeFormat = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3, hour12: false,
});

function showAlgorithmWindow(group, validity, timestampMs, status, algorithm = {}) {
  group.section.dataset.validity = validity;
  const date = new Date(timestampMs ?? NaN);
  group.time.textContent = Number.isNaN(date.getTime()) ? "—" : algorithmTimeFormat.format(date);
  group.status.textContent = status;
  for (const { path, output } of group.metrics) {
    const value = path.reduce((node, key) => node?.[key], algorithm);
    if (typeof value === "boolean") output.textContent = value ? "是" : "否";
    else output.textContent = Number.isFinite(value) ? String(value) : "—";
  }
}

function updateLiveAlgorithm(data, panel) {
  const result = data?.result || data;
  const payload = result?.payload;
  const connection = payload?.status?.connectionState ?? result?.connectionState;
  if (result?.mode === "replay" || connection === "disconnected" || connection === "connecting") {
    const status = result?.mode === "replay" ? "当前为录播" : "等待设备接入";
    for (const group of panel.algorithmGroups) showAlgorithmWindow(group, "waiting", null, status);
    return;
  }
  if (data?.event === "status" || data?.action === "getStatus") {
    for (const group of panel.algorithmGroups) {
      if (group.section.dataset.validity === "waiting") showAlgorithmWindow(group, "waiting", null, "等待实时窗口");
    }
    return;
  }
  const algorithm = payload?.algorithm;
  if (data?.event !== "data" || result.mode !== "live" || !algorithm || typeof algorithm !== "object") return;

  const requestedStreams = subscriptions.get(data.subscriptionId) || [];
  const valid = result.valid === true;
  const reasons = Array.isArray(payload.invalidReasons) ? payload.invalidReasons.join("、") : "";
  for (const group of panel.algorithmGroups) {
    const hasMetrics = group.metrics.some(({ path }) => Object.prototype.hasOwnProperty.call(algorithm, path[0]));
    if (!hasMetrics && (valid || !requestedStreams.includes(group.stream))) continue;
    showAlgorithmWindow(group, valid ? "valid" : "invalid", result.timestampMs,
      valid ? "有效窗口" : `无效窗口${reasons ? ` · ${reasons}` : ""}`, valid ? algorithm : {});
  }
}

function getCheckedValues(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);
}

function refreshSubscriptionSelect() {
  const oldValue = elements.subscriptionSelect.value;
  elements.subscriptionSelect.replaceChildren();
  if (subscriptions.size === 0) {
    const option = new Option("暂无订阅", "");
    elements.subscriptionSelect.add(option);
    elements.subscriptionSelect.disabled = true;
    elements.unsubscribeButton.disabled = true;
    return;
  }
  elements.subscriptionSelect.disabled = false;
  elements.unsubscribeButton.disabled = false;
  for (const [id, streams] of subscriptions) {
    elements.subscriptionSelect.add(new Option(`${id} · ${streams.join(", ")}`, id));
  }
  elements.subscriptionSelect.value = subscriptions.has(oldValue) ? oldValue : subscriptions.keys().next().value;
}

function setPanelSource(source, label) {
  activeSource = source;
  for (const [key, panel] of Object.entries(panels)) {
    const active = key === source;
    panel.article.hidden = key === "unknown" && !active;
    panel.article.classList.toggle("source-active", active);
    panel.fields.details.hidden = !active;
    panel.fields.state.textContent = active ? label : "未连接";
    if (active) {
      for (const value of panel.article.querySelectorAll("dd")) value.textContent = "—";
      panel.fields.summary.textContent = "尚未收到网关数据。";
      panel.fields.waveformMeta.textContent = "等待数据";
      panel.fields.spectrumMeta.textContent = "等待数据";
      for (const group of panel.algorithmGroups) showAlgorithmWindow(group, "waiting", null, "等待实时窗口");
      panel.channels = {};
      panel.bandPower = {};
    }
  }
  drawCharts();
}

function updateSnapshot(data, panel) {
  if (!data || typeof data !== "object") return;
  const { fields } = panel;
  const result = data.result || data;
  const payload = result.payload || {};
  const status = payload.status || result.status || result;
  const hr = payload.algorithm?.hr || {};

  if (result.mode !== undefined) fields.mode.textContent = { live: "实时", replay: "录播" }[result.mode] || result.mode;
  if (result.subjectId !== undefined) fields.subject.textContent = result.subjectId ?? "未配置";
  if (result.timestampMs !== undefined) fields.timestamp.textContent = readableTimestamp(result.timestampMs);
  if (status.connectionState !== undefined) {
    fields.connection.textContent = { connected: "已连接", connecting: "连接中", disconnected: "未连接" }[status.connectionState] || status.connectionState;
  }
  if (status.wearState !== undefined) fields.wear.textContent = status.wearState;
  if (status.batteryPercent !== undefined) fields.battery.textContent = status.batteryPercent === null ? "—" : `${status.batteryPercent}%`;
  if (hr.value !== undefined) fields.heartRate.textContent = hr.value === null ? "—" : `${hr.value} bpm`;
  if (hr.hrv !== undefined) fields.hrv.textContent = hr.hrv ?? "—";
  for (const stream of ["eegRaw", "hrRaw"]) {
    const raw = payload[stream];
    if (raw) fields[stream].textContent = `${raw.byteLength ?? "?"} B · ${raw.packetCount ?? "?"} 包`;
  }

  if (result.valid === false) fields.summary.textContent = "该数据窗口无效。";
  else if (payload.algorithm || payload.eegRaw || payload.hrRaw) fields.summary.textContent = "已收到数据窗口。";
  else if (data.event) fields.summary.textContent = `事件：${data.event}`;
}

function drawCharts() {
  if (!activeSource) return;
  const { fields, channels, bandPower } = panels[activeSource];
  drawWaveform(fields.waveform, channels);
  drawSpectrum(fields.spectrum, bandPower);
}

function fitCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1, rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, rect.width * ratio); canvas.height = Math.max(1, rect.height * ratio);
  const ctx = canvas.getContext("2d"); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); return [ctx, rect.width, rect.height];
}

const channelStyles = {
  single: { label: "单通道", color: "#5fd1e8" },
  left: { label: "左", color: "#5fd1e8" },
  right: { label: "右", color: "#bd9af5" },
};

function drawWaveform(canvas, channels) {
  const [ctx, width, height] = fitCanvas(canvas);
  ctx.strokeStyle = "rgba(91,127,162,.3)";
  ctx.beginPath();
  ctx.moveTo(0, height / 2);
  ctx.lineTo(width, height / 2);
  ctx.stroke();
  const values = Object.values(channels).flat();
  if (!values.length) return;
  const min = Math.min(...values), span = Math.max(...values) - min || 1;
  for (const [channel, samples] of Object.entries(channels)) {
    ctx.strokeStyle = channelStyles[channel].color;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    samples.forEach((value, index) => {
      const x = index / (samples.length - 1 || 1) * width;
      const y = height - ((value - min) / span) * height * .82 - height * .09;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
}

function drawSpectrum(canvas, bands) {
  if (!canvas) return; const [ctx,w,h] = fitCanvas(canvas); ctx.clearRect(0,0,w,h); const vals=spectrumBands.map(k=>Number(bands[k])||0), max=Math.max(...vals,1), gap=8, bw=(w-gap*(vals.length+1))/vals.length;
  vals.forEach((v,i)=>{const bh=v/max*(h-42), x=gap+i*(bw+gap); ctx.fillStyle="#8f7bea"; ctx.fillRect(x,h-22-bh,bw,bh); ctx.fillStyle="#aebbd0"; ctx.font="11px system-ui"; ctx.textAlign="center"; ctx.fillText(spectrumBands[i],x+bw/2,h-7)});
}
function isAllFf(values) {
  if (!Array.isArray(values) || values.length === 0) return false;
  return values.every((value) => value === 255 || value === -1 || (typeof value === "string" && /^f+$/i.test(value.replace(/[\s:]/g, ""))));
}
function updateVisuals(data, panel) {
  const eeg = (data?.result?.payload || data?.payload)?.algorithm?.eeg;
  if (!eeg) return;
  const { fields } = panel;
  const wave = eeg.wave || {};
  if (Object.keys(channelStyles).some((channel) => isAllFf(wave[channel]))) {
    fields.wear.textContent = "未佩戴";
    fields.summary.textContent = "脑波窗口数据全为 FF，判定为未佩戴状态。";
  }
  if (eeg.wave) {
    panel.channels = {};
    for (const channel of Object.keys(channelStyles)) {
      if (!Array.isArray(wave[channel])) continue;
      const values = wave[channel].slice(-1200);
      if (values.length && values.every(Number.isFinite)) panel.channels[channel] = values;
    }
    fields.waveformMeta.replaceChildren(...Object.entries(panel.channels).map(([channel, values]) => {
      const label = document.createElement("span");
      label.className = `channel-${channel}`;
      label.textContent = `${channelStyles[channel].label} · ${values.length} 点`;
      return label;
    }));
    if (!fields.waveformMeta.childElementCount) fields.waveformMeta.textContent = "等待数据";
  }
  if (eeg.bandPower) {
    panel.bandPower = eeg.bandPower;
    fields.spectrumMeta.textContent = "已更新";
  }
  if (eeg.wave || eeg.bandPower) drawCharts();
}

function handleGatewayMessage(message) {
  if (!message || typeof message !== "object") return;
  const data = message.data;
  const panel = panels[activeSource];
  if (message.code !== 200) {
    if (panel && message.message) panel.fields.summary.textContent = message.message;
    appendLog("error", `← ${message.code ?? "ERR"}`, message);
    return;
  }
  if (panel) {
    updateSnapshot(data, panel);
    updateLiveAlgorithm(data, panel);
    updateVisuals(data, panel);
  }

  if (data?.action === "subscribe" && data.result?.subscriptionId) {
    subscriptions.set(data.result.subscriptionId, data.result.streams || []);
    refreshSubscriptionSelect();
  }
  if (data?.action === "unsubscribe" && data.result?.subscriptionId) {
    subscriptions.delete(data.result.subscriptionId);
    refreshSubscriptionSelect();
  }
  appendLog("in", "← IN", message);
  if (initialStatusRequestId && data?.requestId === initialStatusRequestId) {
    initialStatusRequestId = null;
    sendRequest("subscribe", { streams: ["status"] });
  }
}

function sendPayload(payload) {
  if (!isConnected()) {
    appendLog("error", "ERROR", "尚未建立 WebSocket 连接，无法发送请求。");
    return false;
  }
  socket.send(JSON.stringify(payload));
  appendLog("out", "OUT →", payload);
  return true;
}

function sendRequest(action, params) {
  const request = {
    protocolVersion: PROTOCOL_VERSION,
    messageType: "request",
    requestId: createRequestId(),
    action,
    params,
  };
  return sendPayload(request) ? request.requestId : null;
}

function connect() {
  if (socket) return;
  const endpoint = elements.endpoint.value.trim();
  const source = elements.dataSource.value;
  if (!endpoint.startsWith("ws://") && !endpoint.startsWith("wss://")) {
    appendLog("error", "ERROR", "地址必须以 ws:// 或 wss:// 开头。");
    return;
  }
  try {
    setConnectionState("wait", "正在连接");
    appendLog("system", "SYSTEM", `连接 ${endpoint}（子协议 ${SUBPROTOCOL}）`);
    socket = new WebSocket(endpoint, SUBPROTOCOL);
    setPanelSource(source, "网关连接中");
  } catch (error) {
    setConnectionState("off", "未连接");
    appendLog("error", "ERROR", error.message);
    return;
  }
  socket.addEventListener("open", () => {
    setConnectionState("on", "已连接");
    panels[source].fields.state.textContent = "网关已连接";
    appendLog("system", "SYSTEM", "WebSocket 已建立，正在自动发送 getStatus。" );
    const requestId = sendRequest("getStatus", {});
    if (fixedSource) initialStatusRequestId = requestId;
  });
  socket.addEventListener("message", (event) => {
    try {
      handleGatewayMessage(JSON.parse(event.data));
    } catch {
      appendLog("error", "ERROR", `收到非 JSON 文本帧：${event.data}`);
    }
  });
  socket.addEventListener("error", () => appendLog("error", "ERROR", "WebSocket 发生网络错误或网关拒绝了子协议。"));
  socket.addEventListener("close", (event) => {
    setConnectionState("off", "已断开");
    setPanelSource(null);
    initialStatusRequestId = null;
    subscriptions.clear();
    refreshSubscriptionSelect();
    appendLog("system", "SYSTEM", `连接已关闭（code=${event.code}${event.reason ? `，${event.reason}` : ""}）。`);
    socket = null;
  });
}

elements.connectButton.addEventListener("click", () => {
  if (fixedSource) window.location.reload();
  else connect();
});
elements.disconnectButton.addEventListener("click", () => {
  if (socket) socket.close(1000, "B-side test console disconnect");
});
elements.getStatusButton.addEventListener("click", () => sendRequest("getStatus", {}));
elements.getLatestButton.addEventListener("click", () => {
  const streams = getCheckedValues("latestStream");
  if (streams.length === 0) return appendLog("error", "ERROR", "getLatest 至少选择 EEG 或 HR 一个流。");
  sendRequest("getLatest", { streams });
});
elements.subscribeButton.addEventListener("click", () => {
  const streams = getCheckedValues("subscribeStream");
  if (streams.length === 0) return appendLog("error", "ERROR", "subscribe 至少选择一个流。");
  sendRequest("subscribe", { streams, includeInvalid: elements.includeInvalid.checked });
});
elements.unsubscribeButton.addEventListener("click", () => {
  const subscriptionId = elements.subscriptionSelect.value;
  if (!subscriptionId) return appendLog("error", "ERROR", "没有可取消的订阅。");
  sendRequest("unsubscribe", { subscriptionId });
});
elements.sendCustomButton.addEventListener("click", () => {
  try {
    const payload = JSON.parse(elements.customRequest.value);
    if (payload.requestId === "") payload.requestId = createRequestId();
    sendPayload(payload);
  } catch (error) {
    appendLog("error", "ERROR", `自定义 JSON 无法解析：${error.message}`);
  }
});
elements.clearLogButton.addEventListener("click", () => {
  logEntries = [];
  elements.log.replaceChildren();
});
elements.copyLogButton.addEventListener("click", async () => {
  const text = logEntries.map((entry) => `[${entry.time}] ${entry.label}\n${entry.body}`).join("\n\n");
  try {
    await navigator.clipboard.writeText(text);
    appendLog("system", "SYSTEM", "协议日志已复制到剪贴板。");
  } catch {
    appendLog("error", "ERROR", "浏览器未授予剪贴板权限，请手动复制日志。");
  }
});

refreshSubscriptionSelect();
appendLog("system", "SYSTEM", configuredEndpoint
  ? `网关联调台已就绪；已使用本机服务地址 ${elements.endpoint.value}，不会发送任何保活帧。`
  : "网关联调台已就绪；不会发送任何保活帧。");
if (typeof window.NEUROBRIDGE_B_CLIENT_ENDPOINT === "string") {
  connect();
}

window.addEventListener("resize", drawCharts);
