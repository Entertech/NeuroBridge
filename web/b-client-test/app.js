"use strict";

const PROTOCOL_VERSION = window.NEUROBRIDGE_VERSION.protocolVersion;
const SUBPROTOCOL = "neurobridge.v1";
let logEntries = [];
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
  algorithmMetrics: document.querySelector("#algorithmMetrics"),
  sourceWindows: document.querySelector("#sourceWindows"),
  sourceWindowTemplate: document.querySelector("#sourceWindowTemplate"),
  chartView: document.querySelector("#chartView"),
  commandSource: document.querySelector("#commandSource"),
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

const overviewMetrics = [
  ["放松度", "eeg", ["relaxation"]], ["压力值", "hr", ["pressure"]],
  ["心率", "hr", ["hr", "value"], " bpm"], ["HRV", "hr", ["hr", "hrv"]],
  ["注意力", "eeg", ["attention"]], ["Flow", "eeg", ["flow", "meditation"]],
];

function createOverview() {
  const bySource = Object.fromEntries(Object.keys(sourceLabels).map((source) => [source, []]));
  for (const [name, stream, path, unit = ""] of overviewMetrics) {
    const card = document.createElement("article");
    const title = document.createElement("h3");
    card.className = "metric";
    title.textContent = name;
    card.append(title);
    for (const [source, metrics] of Object.entries(bySource)) {
      const row = document.createElement("div");
      const label = document.createElement("div");
      const state = document.createElement("span");
      const output = document.createElement("output");
      const meta = document.createElement("p");
      row.className = "metric-reading";
      row.dataset.source = source;
      label.className = "metric-reading-label";
      label.append(sourceLabels[source], state);
      output.setAttribute("aria-label", `${sourceLabels[source]}${name}`);
      row.append(label, output, meta);
      card.append(row);
      metrics.push({ stream, path, unit, row, state, output, meta, timestamp: null });
    }
    elements.algorithmMetrics.append(card);
  }
  return bySource;
}

const overviewBySource = createOverview();

function createSourcePanel(source) {
  const section = elements.sourceWindowTemplate.content.firstElementChild.cloneNode(true);
  section.dataset.source = source;
  const fields = Object.fromEntries(
    [...section.querySelectorAll("[data-field]")].map((field) => [field.dataset.field, field]),
  );
  fields.title.textContent = sourceLabels[source];
  fields.waveform.setAttribute("aria-label", `${sourceLabels[source]}脑波波形图`);
  fields.spectrum.setAttribute("aria-label", `${sourceLabels[source]}频段能量图`);
  elements.sourceWindows.append(section);
  const algorithmGroups = [...section.querySelectorAll("[data-algorithm]")].map((section) => {
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
  return {
    source, section, fields, algorithmGroups, overview: overviewBySource[source],
    socket: null, endpoint: elements.endpoint.value, initialStatusRequestId: null,
    subscriptions: new Map(), channels: {}, bandPower: {}, identity: {}, eegTimestamp: null,
  };
}

const panels = Object.fromEntries(Object.keys(sourceLabels).map((source) => [source, createSourcePanel(source)]));
if (fixedSource) {
  elements.endpoint.readOnly = true;
  elements.dataSource.value = configuredSource;
  elements.dataSource.disabled = true;
  document.querySelector('input[name="subscribeStream"][value="status"]').checked = false;
  elements.sourceHint.textContent = "本机页面使用网关配置的来源。两路联调可在独立页面分别连接两个网关地址。";
}

function selectedPanel() {
  return panels[elements.dataSource.value];
}

if (typeof configuredEndpoint === "string") selectedPanel().endpoint = configuredEndpoint;

function createRequestId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `btest-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function refreshConnectionControls() {
  const panel = selectedPanel();
  const state = panel.socket?.readyState;
  const connected = state === WebSocket.OPEN;
  const pending = state === WebSocket.CONNECTING || state === WebSocket.CLOSING;
  elements.connectionLabel.textContent = `${sourceLabels[panel.source]} · ${connected ? "已连接" : pending ? "连接处理中" : "未连接"}`;
  elements.connectionDot.className = `dot dot-${connected ? "on" : pending ? "wait" : "off"}`;
  elements.connectButton.disabled = connected || pending;
  elements.disconnectButton.disabled = state !== WebSocket.OPEN && state !== WebSocket.CONNECTING;
  elements.endpoint.disabled = connected || pending;
  elements.commandSource.textContent = `当前请求目标：${sourceLabels[panel.source]}`;
  refreshSubscriptionSelect();
}

function setSourceStatus(panel, label) {
  panel.fields.state.textContent = label;
  for (const metric of panel.overview) metric.state.textContent = label;
}

function timeLabel(date = new Date()) {
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function appendLog(kind, label, body, panel = selectedPanel()) {
  const entry = {
    time: timeLabel(), kind, label, source: sourceLabels[panel.source],
    body: typeof body === "string" ? body : JSON.stringify(body, (key, value) =>
      key === "bytesBase64" ? undefined : value, 2),
  };
  logEntries.push(entry);
  const node = document.createElement("div");
  node.className = `log-entry ${kind}`;
  const time = document.createElement("span");
  time.className = "log-time";
  time.textContent = `${entry.time} ${entry.source}`;
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

function resetOverview(panel, reason) {
  for (const metric of panel.overview) {
    metric.output.textContent = "—";
    metric.meta.textContent = reason;
    metric.timestamp = null;
  }
}

function resetAlgorithmReadings(panel, reason) {
  resetOverview(panel, reason);
  for (const group of panel.algorithmGroups) showAlgorithmWindow(group, "waiting", null, reason);
  panel.channels = {};
  panel.bandPower = {};
  panel.eegTimestamp = null;
  panel.fields.waveformMeta.textContent = reason;
  panel.fields.spectrumMeta.textContent = reason;
  drawSourceCharts(panel);
}

function updateAlgorithmOverview(data, panel) {
  const result = data?.result || data;
  if (!result) return;
  const identity = { gatewayBootId: result.gatewayBootId, subjectId: result.subjectId, mode: result.mode };
  for (const key of Object.keys(identity)) if (identity[key] === undefined) delete identity[key];
  if (Object.keys(identity).some((key) => key in panel.identity && identity[key] !== panel.identity[key])) {
    resetAlgorithmReadings(panel, "等待新数据");
  }
  Object.assign(panel.identity, identity);
  const mode = panel.identity.mode;
  if (mode === "live" || mode === "replay") setSourceStatus(panel, mode === "live" ? "实时" : "录播");
  const connection = result.payload?.status?.connectionState ?? result.connectionState;
  if (data.event === "replayEnded" || (mode === "live" && connection && connection !== "connected")) {
    resetAlgorithmReadings(panel, data.event === "replayEnded" ? "等待下一轮录播" : "等待设备接入");
    return;
  }
  const algorithm = result.payload?.algorithm;
  const query = data.action === "getLatest";
  if ((data.event !== "data" && !query) || !algorithm || !Number.isFinite(result.timestampMs)) return;

  const streams = panel.subscriptions.get(data.subscriptionId) || [];
  for (const metric of panel.overview) {
    const value = metric.path.reduce((node, key) => node?.[key], algorithm);
    if (value === undefined && (result.valid !== false || !streams.includes(metric.stream))) continue;
    if (metric.timestamp !== null && result.timestampMs < metric.timestamp) continue;
    metric.timestamp = result.timestampMs;
    const valid = result.valid !== false && Number.isFinite(value);
    metric.output.textContent = valid ? `${Number(value.toPrecision(8))}${metric.unit}` : "—";
    const status = result.valid === false ? "无效窗口" : !valid ? "算法未提供" : query ? "单次查询" : "连续订阅";
    metric.meta.textContent = `${status} · ${algorithmTimeFormat.format(result.timestampMs)}`;
  }
}

function showAlgorithmWindow(group, validity, timestampMs, status, algorithm = {}) {
  group.timestamp = timestampMs;
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

  const requestedStreams = panel.subscriptions.get(data.subscriptionId) || [];
  const valid = result.valid === true;
  const reasons = Array.isArray(payload.invalidReasons) ? payload.invalidReasons.join("、") : "";
  for (const group of panel.algorithmGroups) {
    const hasMetrics = group.metrics.some(({ path }) => Object.prototype.hasOwnProperty.call(algorithm, path[0]));
    if (!hasMetrics && (valid || !requestedStreams.includes(group.stream))) continue;
    if (group.timestamp != null && result.timestampMs < group.timestamp) continue;
    showAlgorithmWindow(group, valid ? "valid" : "invalid", result.timestampMs,
      valid ? "有效窗口" : `无效窗口${reasons ? ` · ${reasons}` : ""}`, valid ? algorithm : {});
  }
}

function getCheckedValues(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);
}

function refreshSubscriptionSelect() {
  const { subscriptions } = selectedPanel();
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

function resetSource(panel, status, waiting) {
  panel.fields.details.hidden = panel.socket === null;
  for (const value of panel.fields.details.querySelectorAll(".status-list dd")) value.textContent = "—";
  panel.fields.summary.textContent = "尚未收到网关数据。";
  panel.identity = {};
  setSourceStatus(panel, status);
  resetAlgorithmReadings(panel, waiting);
}

function updateSnapshot(data, panel) {
  if (!data || typeof data !== "object") return;
  const { fields } = panel;
  const result = data.result || data;
  const payload = result.payload || {};
  const status = payload.status || result.status || result;

  if (result.mode !== undefined) fields.mode.textContent = { live: "实时", replay: "录播" }[result.mode] || result.mode;
  if (result.subjectId !== undefined) fields.subject.textContent = result.subjectId ?? "未配置";
  if (result.timestampMs !== undefined) fields.timestamp.textContent = readableTimestamp(result.timestampMs);
  if (status.connectionState !== undefined) {
    fields.connection.textContent = { connected: "已连接", connecting: "连接中", disconnected: "未连接" }[status.connectionState] || status.connectionState;
  }
  if (status.wearState !== undefined) fields.wear.textContent = status.wearState;
  if (status.batteryPercent !== undefined) fields.battery.textContent = status.batteryPercent === null ? "—" : `${status.batteryPercent}%`;
  for (const stream of ["eegRaw", "hrRaw"]) {
    const raw = payload[stream];
    if (raw) fields[stream].textContent = `${raw.byteLength ?? "?"} B · ${raw.packetCount ?? "?"} 包`;
  }

  if (result.valid === false) fields.summary.textContent = "该数据窗口无效。";
  else if (payload.algorithm || payload.eegRaw || payload.hrRaw) fields.summary.textContent = "已收到数据窗口。";
  else if (data.event) fields.summary.textContent = `事件：${data.event}`;
}

function drawSourceCharts(panel) {
  if (panel.section.hidden) return;
  drawWaveform(panel.fields.waveform, panel.channels);
  drawSpectrum(panel.fields.spectrum, panel.bandPower);
}

function drawCharts() {
  for (const panel of Object.values(panels)) drawSourceCharts(panel);
}

const chartViewKey = "neurobridge-chart-view";
function applyChartView() {
  const view = elements.chartView.value;
  elements.sourceWindows.dataset.view = view;
  for (const panel of Object.values(panels)) {
    const unspecified = panel.source === "unknown";
    const showUnknown = unspecified && Boolean(panel.socket);
    panel.section.hidden = unspecified ? !showUnknown || view !== "both" : view !== "both" && panel.source !== view;
    if (unspecified) {
      for (const metric of panel.overview) metric.row.hidden = !showUnknown;
    }
  }
  drawCharts();
}

elements.chartView.addEventListener("change", () => {
  applyChartView();
  try { window.localStorage.setItem(chartViewKey, elements.chartView.value); } catch { /* The current page still applies the choice. */ }
});

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

function chartAxes(canvas, low, high) {
  const [ctx, width, height] = fitCanvas(canvas);
  const left = 48, right = width - 12, bottom = height - 28;
  const scale = (value) => bottom - (value - low) / (high - low) * (bottom - 12);
  ctx.font = "11px system-ui";
  ctx.fillStyle = "#aebbd0";
  ctx.strokeStyle = "#263e55";
  ctx.textAlign = "right";
  for (let tick = 0; tick <= 4; tick += 1) {
    const value = low + (high - low) * tick / 4;
    const y = scale(value);
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
    ctx.fillText(String(Number(value.toPrecision(3))), left - 6, y + 3, left - 8);
  }
  return { ctx, left, right, bottom, scale };
}

function drawWaveform(canvas, channels) {
  const values = Object.values(channels).flat().filter(Number.isFinite);
  if (!values.length) {
    fitCanvas(canvas);
    return;
  }
  let low = Math.min(...values), high = Math.max(...values);
  if (low === high) {
    low -= 1;
    high += 1;
  }
  const { ctx, left, right, bottom, scale } = chartAxes(canvas, low, high);
  const lastIndex = Math.max(...Object.values(channels).map((samples) => samples.length)) - 1;
  for (const [channel, samples] of Object.entries(channels)) {
    ctx.strokeStyle = channelStyles[channel].color;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    let previousValid = false;
    samples.forEach((value, index) => {
      if (!Number.isFinite(value)) {
        previousValid = false;
        return;
      }
      const x = left + index / Math.max(1, lastIndex) * (right - left);
      if (previousValid) ctx.lineTo(x, scale(value));
      else ctx.moveTo(x, scale(value));
      previousValid = true;
    });
    ctx.stroke();
  }
  ctx.textAlign = "left";
  ctx.fillText("0", left, bottom + 18);
  ctx.textAlign = "right";
  ctx.fillText(`${lastIndex} 样本`, right, bottom + 18);
}

function drawSpectrum(canvas, bands) {
  const values = spectrumBands.map((name) => bands[name]).filter(Number.isFinite);
  if (!values.length) {
    fitCanvas(canvas);
    return;
  }
  const { ctx, left, right, bottom, scale } = chartAxes(canvas, Math.min(0, ...values), Math.max(0, ...values) || 1);
  const step = (right - left) / spectrumBands.length;
  const labels = ["δ", "θ", "α", "低 β", "高 β", "β", "γ"];
  ctx.textAlign = "center";
  spectrumBands.forEach((name, index) => {
    const x = left + (index + .5) * step;
    const value = bands[name];
    if (Number.isFinite(value)) {
      ctx.fillStyle = "#8f7bea";
      ctx.fillRect(x - step * .28, Math.min(scale(0), scale(value)), step * .56, Math.max(1, Math.abs(scale(0) - scale(value))));
    }
    ctx.fillStyle = "#aebbd0";
    ctx.fillText(labels[index], x, bottom + 18);
  });
}

function updateVisuals(data, panel) {
  const result = data?.result || data;
  const eeg = result?.payload?.algorithm?.eeg;
  const invalid = result?.valid === false;
  const eegSubscribed = panel.subscriptions.get(data?.subscriptionId)?.includes("eeg");
  if (!eeg && !(invalid && eegSubscribed && result.payload?.algorithm)) return;
  if (panel.eegTimestamp !== null && result.timestampMs < panel.eegTimestamp) return;
  panel.eegTimestamp = result.timestampMs ?? null;

  panel.channels = {};
  panel.bandPower = invalid ? {} : eeg?.bandPower || {};
  if (!invalid) {
    for (const channel of Object.keys(channelStyles)) {
      const wave = eeg?.wave?.[channel];
      if (!Array.isArray(wave)) continue;
      const values = wave.slice(-1200);
      if (values.some(Number.isFinite)) panel.channels[channel] = values;
    }
  }
  panel.fields.waveformMeta.replaceChildren(...Object.entries(panel.channels).map(([channel, values]) => {
    const label = document.createElement("span");
    label.className = `channel-${channel}`;
    label.textContent = `${channelStyles[channel].label} · ${values.length} 点`;
    return label;
  }));
  if (!panel.fields.waveformMeta.childElementCount) panel.fields.waveformMeta.textContent = invalid ? "无效窗口" : "未提供波形";
  panel.fields.spectrumMeta.textContent = invalid ? "无效窗口" : Object.values(panel.bandPower).some(Number.isFinite)
    ? readableTimestamp(result.timestampMs) : "未提供频段能量";
  drawSourceCharts(panel);
}

function handleGatewayMessage(message, panel) {
  if (!message || typeof message !== "object") return;
  const data = message.data;
  if (message.code !== 200) {
    if (message.message) panel.fields.summary.textContent = message.message;
    appendLog("error", `← ${message.code ?? "ERR"}`, message, panel);
    return;
  }
  updateAlgorithmOverview(data, panel);
  updateSnapshot(data, panel);
  updateLiveAlgorithm(data, panel);
  updateVisuals(data, panel);

  const subscriptionId = data?.result?.subscriptionId;
  if (data?.action === "subscribe" && subscriptionId) {
    panel.subscriptions.set(subscriptionId, data.result.streams || []);
  }
  if (data?.action === "unsubscribe" && subscriptionId) panel.subscriptions.delete(subscriptionId);
  if (subscriptionId && panel === selectedPanel()) refreshSubscriptionSelect();
  appendLog("in", "← IN", message, panel);
  if (panel.initialStatusRequestId && data?.requestId === panel.initialStatusRequestId) {
    panel.initialStatusRequestId = null;
    sendRequest("subscribe", { streams: ["status"] }, panel);
  }
}

function sendPayload(payload, panel = selectedPanel()) {
  if (panel.socket?.readyState !== WebSocket.OPEN) {
    appendLog("error", "ERROR", "请先连接该来源的网关。", panel);
    return false;
  }
  panel.socket.send(JSON.stringify(payload));
  appendLog("out", "OUT →", payload, panel);
  return true;
}

function sendRequest(action, params, panel = selectedPanel()) {
  const request = {
    protocolVersion: PROTOCOL_VERSION,
    messageType: "request",
    requestId: createRequestId(),
    action,
    params,
  };
  return sendPayload(request, panel) ? request.requestId : null;
}

function connect() {
  const panel = selectedPanel();
  if (panel.socket) return;
  let endpoint;
  let socket;
  try {
    endpoint = new URL(panel.endpoint.trim());
    if (!["ws:", "wss:"].includes(endpoint.protocol)) throw new Error("地址必须以 ws:// 或 wss:// 开头。");
    if (Object.values(panels).some((other) => other.socket?.url === endpoint.href)) {
      throw new Error("此网关已关联其他来源，请为当前来源填写独立的 WebSocket 地址。");
    }
    socket = new WebSocket(endpoint.href, SUBPROTOCOL);
  } catch (error) {
    appendLog("error", "ERROR", error.message, panel);
    return;
  }
  panel.socket = socket;
  resetSource(panel, "正在连接", "等待算法数据");
  if (panel.source === "unknown") elements.chartView.value = "both";
  applyChartView();
  refreshConnectionControls();
  appendLog("system", "SYSTEM", `连接 ${endpoint.href}（子协议 ${SUBPROTOCOL}）`, panel);

  socket.addEventListener("open", () => {
    setSourceStatus(panel, "网关已连接");
    refreshConnectionControls();
    appendLog("system", "SYSTEM", "WebSocket 已建立，正在自动发送 getStatus。", panel);
    const requestId = sendRequest("getStatus", {}, panel);
    if (fixedSource) panel.initialStatusRequestId = requestId;
  });
  socket.addEventListener("message", (event) => {
    try {
      handleGatewayMessage(JSON.parse(event.data), panel);
    } catch {
      appendLog("error", "ERROR", `无法读取网关消息（${event.data.length} 字符）。`, panel);
    }
  });
  socket.addEventListener("error", () => appendLog("error", "ERROR", "WebSocket 发生网络错误或网关拒绝了子协议。", panel));
  socket.addEventListener("close", (event) => {
    panel.socket = null;
    panel.initialStatusRequestId = null;
    panel.subscriptions.clear();
    resetSource(panel, "已断开", "等待连接网关");
    applyChartView();
    refreshConnectionControls();
    appendLog("system", "SYSTEM", `连接已关闭（code=${event.code}${event.reason ? `，${event.reason}` : ""}）。`, panel);
  });
}

elements.dataSource.addEventListener("change", () => {
  elements.endpoint.value = selectedPanel().endpoint;
  refreshConnectionControls();
});
elements.endpoint.addEventListener("input", () => {
  selectedPanel().endpoint = elements.endpoint.value;
});
elements.connectButton.addEventListener("click", () => {
  if (fixedSource) window.location.reload();
  else connect();
});
elements.disconnectButton.addEventListener("click", () => {
  selectedPanel().socket?.close(1000, "B-side test console disconnect");
  refreshConnectionControls();
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
  const text = logEntries.map((entry) => `[${entry.time}] ${entry.source} ${entry.label}\n${entry.body}`).join("\n\n");
  try {
    await navigator.clipboard.writeText(text);
    appendLog("system", "SYSTEM", "协议日志已复制到剪贴板。");
  } catch {
    appendLog("error", "ERROR", "浏览器未授予剪贴板权限，请手动复制日志。");
  }
});

let savedChartView;
try { savedChartView = window.localStorage.getItem(chartViewKey); } catch { /* Storage may be disabled. */ }
const defaultView = fixedSource ? configuredSource : "both";
elements.chartView.value = ["both", "serial", "bluetooth"].includes(savedChartView) ? savedChartView : defaultView;
for (const panel of Object.values(panels)) {
  resetSource(panel, "未连接", fixedSource && panel.source !== configuredSource ? "等待其他网关接入" : "等待连接网关");
}
elements.endpoint.value = selectedPanel().endpoint;
applyChartView();
refreshConnectionControls();
appendLog("system", "SYSTEM", configuredEndpoint
  ? `网关联调台已就绪；已使用本机服务地址 ${elements.endpoint.value}，不会发送任何保活帧。`
  : "网关联调台已就绪；不会发送任何保活帧。");
if (typeof window.NEUROBRIDGE_B_CLIENT_ENDPOINT === "string") {
  connect();
}

window.addEventListener("resize", drawCharts);
