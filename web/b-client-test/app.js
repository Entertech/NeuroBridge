"use strict";

const PROTOCOL_VERSION = window.NEUROBRIDGE_VERSION.protocolVersion;
const SUBPROTOCOL = "neurobridge.v1";
const sourceLabels = { serial: "耳机串口", bluetooth: "头环蓝牙" };
const deviceBySource = { serial: "headset", bluetooth: "headband" };
const sourceByDevice = new Map(Object.entries(deviceBySource).map(([source, device]) => [device, source]));
const configuredSource = window.NEUROBRIDGE_B_CLIENT_SOURCE;
const configuredEndpoint = window.NEUROBRIDGE_B_CLIENT_ENDPOINT;
const fixedSource = configuredSource === "serial" || configuredSource === "bluetooth";
const sharedConnection = configuredSource === "dual";
const localConnection = fixedSource || sharedConnection;
const showConnectedDevicesOnly = sharedConnection || window.NEUROBRIDGE_B_CLIENT_WINDOWS === true;
const waveformCapacity = 500;
const sdkDualWaveLimit = 2000;
const liveDisplayIntervalMs = 600;
const pendingLiveMessages = new Map();
let liveDisplayTimer = null;
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
const algorithmStreamFields = Object.entries(algorithmMetrics).map(([stream, metrics]) => [
  stream, [...new Set(metrics.map(([key]) => key.split(".")[0]))],
]);
const eegScoresWithZeroPlaceholder = new Set(["attention", "relaxation", "pleasure", "flow.meditation"]);

const elements = {
  connectionRows: document.querySelector("#connectionRows"),
  connectionRowTemplate: document.querySelector("#connectionRowTemplate"),
  algorithmMetrics: document.querySelector("#algorithmMetrics"),
  algorithmHint: document.querySelector("#algorithmHint"),
  waitingForDevice: document.querySelector("#waitingForDevice"),
  sourceWindows: document.querySelector("#sourceWindows"),
  sourceWindowTemplate: document.querySelector("#sourceWindowTemplate"),
  sourceRequests: document.querySelector("#sourceRequests"),
  sourceRequestTemplate: document.querySelector("#sourceRequestTemplate"),
  protocolRequests: document.querySelector("#protocolRequests"),
  protocolLogs: document.querySelector("#protocolLogs"),
  connectionDot: document.querySelector("#connectionDot"),
  connectionLabel: document.querySelector("#connectionLabel"),
  sourceLogs: document.querySelector("#sourceLogs"),
  sourceLogTemplate: document.querySelector("#sourceLogTemplate"),
};

document.querySelector("#protocolVersion").textContent = `v${PROTOCOL_VERSION}`;

const overviewMetrics = [
  ["放松度", "eeg", ["relaxation"]], ["压力值", "hr", ["pressure"]],
  ["心率", "hr", ["hr", "value"], " bpm"], ["HRV", "hr", ["hr", "hrv"]],
  ["注意力", "eeg", ["attention"]], ["Flow", "eeg", ["flow", "meditation"]],
  ["和谐度", "hr", ["coherence"]], ["激活度", "hr", ["arousal"]],
  ...algorithmMetrics.eeg.filter(([key]) => key.startsWith("sleep."))
    .map(([key, title]) => [title, "eeg", key.split(".")]),
];

function createOverview(source, sleepGrid) {
  const section = document.createElement("section");
  const heading = document.createElement("div");
  const title = document.createElement("h3");
  const state = document.createElement("span");
  const grid = document.createElement("div");
  section.className = "source-window";
  section.dataset.source = source;
  heading.className = "chart-heading";
  title.id = `overview-${source}`;
  title.textContent = sourceLabels[source];
  section.setAttribute("aria-labelledby", title.id);
  state.className = "source-status";
  grid.className = "metric-grid";
  heading.append(title, state);
  section.append(heading, grid);
  elements.algorithmMetrics.append(section);

  const metrics = overviewMetrics.map(([name, stream, path, unit = ""]) => {
    const card = document.createElement("article");
    const label = document.createElement("h4");
    const output = document.createElement("output");
    const meta = document.createElement("p");
    card.className = "metric";
    label.textContent = name;
    output.setAttribute("aria-label", `${sourceLabels[source]}${name}`);
    card.append(label, output, meta);
    const group = path[0] === "sleep" ? sleepGrid : grid;
    group.append(card);
    return { stream, path, unit, output, meta, timestamp: null };
  });
  return { section, state, metrics };
}

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
    const metrics = algorithmMetrics[stream].filter(([key]) => !key.startsWith("sleep.")).map(([key, title]) => {
      const item = document.createElement("div");
      const label = document.createElement("dt");
      const output = document.createElement("dd");
      label.textContent = title;
      output.textContent = "—";
      item.append(label, output);
      list.append(item);
      return { key, path: key.split("."), output };
    });
    return { section, stream, metrics, time: section.querySelector("[data-window-time]"),
      status: section.querySelector("[data-window-status]") };
  });
  return {
    source, section, fields, algorithmGroups, overview: createOverview(source, fields.sleepMetrics),
    socket: null, initialStatusRequestId: null, statusSubscriptionRequestId: null, statusSubscriptionId: null,
    eventModeSubscriptions: new Set(), deviceStatusTimestamp: null,
    eegStateTimestamp: null, eegStaleTimer: null, wearState: "unknown", eegDisplay: "shown",
    subscriptions: new Map(), channels: {}, bandPower: {}, identity: {}, eegTimestamp: null,
  };
}

const panels = Object.fromEntries(Object.keys(sourceLabels).map((source) => [source, createSourcePanel(source)]));
const localPanel = fixedSource ? panels[configuredSource] : panels.serial;

function createConnectionRow(panel) {
  const row = elements.connectionRowTemplate.content.firstElementChild.cloneNode(true);
  const title = sharedConnection ? "Windows 网关" : sourceLabels[panel.source];
  row.setAttribute("aria-label", title);
  row.querySelector("span").textContent = `${title} WebSocket 地址`;
  const endpoint = row.querySelector("input");
  const connectButton = row.querySelector("[data-connect]");
  const disconnectButton = row.querySelector("[data-disconnect]");
  if (panel === localPanel && typeof configuredEndpoint === "string") endpoint.value = configuredEndpoint;
  endpoint.readOnly = localConnection && typeof configuredEndpoint === "string";
  connectButton.addEventListener("click", () => {
    if (endpoint.readOnly) window.location.reload();
    else connect(panel);
  });
  disconnectButton.addEventListener("click", () => {
    panel.socket?.close(1000, "B-side test console disconnect");
    refreshConnectionControls(panel);
  });
  elements.connectionRows.append(row);
  return { endpoint, connectButton, disconnectButton };
}

function createRequestId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `btest-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function refreshConnectionControls(panel) {
  const { endpoint, connectButton, disconnectButton } = panel.connection;
  const state = panel.socket?.readyState;
  connectButton.disabled = Boolean(panel.socket);
  connectButton.textContent = state === WebSocket.OPEN ? "已连接" : panel.socket ? "连接处理中" : "连接";
  disconnectButton.disabled = state !== WebSocket.OPEN && state !== WebSocket.CONNECTING;
  endpoint.disabled = Boolean(panel.socket);

  const active = Object.values(panels).filter((item) => item.socket?.readyState === WebSocket.OPEN);
  const pending = Object.values(panels).some((item) => item.socket);
  const labels = active.length && showConnectedDevicesOnly ? "Windows 网关" : active.map((item) => sourceLabels[item.source]).join("、");
  elements.connectionLabel.textContent = labels ? `${labels} · 已连接` : pending ? "连接处理中" : "未连接";
  elements.connectionDot.className = `dot dot-${labels ? "on" : pending ? "wait" : "off"}`;
}

function setSourceStatus(panel, label) {
  panel.fields.state.textContent = label;
  panel.overview.state.textContent = label;
}

function timeLabel(date = new Date()) {
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function createSourceLog(source) {
  const section = elements.sourceLogTemplate.content.firstElementChild.cloneNode(true);
  const title = section.querySelector("h3");
  const list = section.querySelector(".log");
  const entries = [];
  const followButton = section.querySelector("[data-log-follow]");
  const followState = followButton.querySelector("[data-log-follow-state]");
  const sourceLog = { section, list, entries, followLatest: true };
  section.dataset.source = source;
  title.id = `log-${source}`;
  title.textContent = sourceLabels[source];
  section.setAttribute("aria-labelledby", title.id);
  list.id = `log-entries-${source}`;
  list.setAttribute("aria-label", `${sourceLabels[source]}协议日志`);
  followButton.setAttribute("aria-controls", list.id);

  function setFollowLatest(enabled) {
    sourceLog.followLatest = enabled;
    followButton.setAttribute("aria-pressed", String(enabled));
    followState.textContent = enabled ? "开启" : "暂停";
    if (enabled) list.scrollTop = list.scrollHeight;
  }

  followButton.addEventListener("click", () => setFollowLatest(!sourceLog.followLatest));
  list.addEventListener("scroll", () => {
    if (sourceLog.followLatest && list.scrollHeight - list.clientHeight - list.scrollTop > 2) {
      setFollowLatest(false);
    }
  });
  section.querySelector("[data-log-clear]").addEventListener("click", () => {
    entries.length = 0;
    list.replaceChildren();
  });
  section.querySelector("[data-log-copy]").addEventListener("click", async () => {
    const text = entries.map((entry) => `[${entry.time}] ${sourceLabels[source]} ${entry.label}\n${entry.body}`).join("\n\n");
    try {
      await navigator.clipboard.writeText(text);
      appendLog("system", "SYSTEM", "协议日志已复制到剪贴板。", panels[source]);
    } catch {
      appendLog("error", "ERROR", "浏览器未授予剪贴板权限，请手动复制日志。", panels[source]);
    }
  });
  elements.sourceLogs.append(section);
  return sourceLog;
}

const sourceLogs = Object.fromEntries(Object.keys(sourceLabels).map((source) => [source, createSourceLog(source)]));

function appendLog(kind, label, body, panel) {
  const { list, entries, followLatest } = sourceLogs[panel.source];
  const entry = {
    time: timeLabel(), label,
    body: typeof body === "string" ? body : JSON.stringify(body, (key, value) =>
      key === "bytesBase64" ? undefined : value, 2),
  };
  entries.push(entry);
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
  list.append(node);
  if (entries.length > 200) {
    entries.shift();
    const previousTop = followLatest ? 0 : list.scrollTop;
    const previousHeight = followLatest ? 0 : list.scrollHeight;
    list.firstElementChild.remove();
    if (previousTop > 0) {
      list.scrollTop = Math.max(0, previousTop - (previousHeight - list.scrollHeight));
    }
  }
  if (followLatest) list.scrollTop = list.scrollHeight;
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
  for (const metric of panel.overview.metrics) {
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
  panel.fields.eegOrigin.textContent = "—";
  panel.fields.hrRawDetails.hidden = true;
  panel.fields.hrRawDetails.open = false;
  panel.fields.hrRawTime.textContent = "";
  panel.fields.hrRawBytes.textContent = "";
  drawSourceCharts(panel);
}

function updateAlgorithmOverview(data, panel, unavailable, renderedStreams) {
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

  const streams = renderedStreams ?? liveMessageStreams(data, panel);
  for (const metric of panel.overview.metrics) {
    if (!streams.includes(metric.stream)) continue;
    const value = metric.path.reduce((node, key) => node?.[key], algorithm);
    const isSleep = metric.path[0] === "sleep";
    if (metric.timestamp !== null && result.timestampMs < metric.timestamp) continue;
    metric.timestamp = result.timestampMs;
    const hasValue = typeof value === "boolean" || Number.isFinite(value);
    const valid = result.valid !== false && hasValue;
    const missingHeartRate = metric.path[0] === "hr" && metric.path[1] === "value" && value === 0;
    const missingEeg = metric.stream === "eeg" && (unavailable.eeg === "未佩戴"
      || (!isSleep && (value === 0 || unavailable.eeg)));
    if (mode === "live" && (!valid || missingHeartRate || missingEeg)) {
      if (metric.output.textContent !== "—") metric.meta.textContent = "沿用上次有效值";
      else if (result.valid === false) metric.meta.textContent = "无效窗口";
      else if (!hasValue) metric.meta.textContent = "本窗口未上报";
      continue;
    }
    if (!valid) metric.output.textContent = "—";
    else if (typeof value === "boolean") metric.output.textContent = value ? "是" : "否";
    else metric.output.textContent = `${Number(value.toPrecision(8))}${metric.unit}`;
    const status = result.valid === false ? "无效窗口" : !valid ? "算法未提供" : query ? "单次查询" : "连续订阅";
    metric.meta.textContent = `${status} · ${algorithmTimeFormat.format(result.timestampMs)}`;
  }
}

function showAlgorithmWindow(group, validity, timestampMs, status, algorithm = {}, holdLast = false) {
  group.timestamp = timestampMs;
  group.section.dataset.validity = validity;
  const date = new Date(timestampMs ?? NaN);
  group.time.textContent = Number.isNaN(date.getTime()) ? "—" : algorithmTimeFormat.format(date);
  group.status.textContent = status;
  for (const { key, path, output } of group.metrics) {
    const value = path.reduce((node, key) => node?.[key], algorithm);
    const zeroPlaceholder = value === 0 && (key === "hr.value" || (holdLast && eegScoresWithZeroPlaceholder.has(key)));
    const hasValue = typeof value === "boolean" || (Number.isFinite(value) && !zeroPlaceholder);
    if (holdLast && (validity !== "valid" || !hasValue)) {
      if (output.textContent !== "—") output.title = "沿用上次有效值";
      continue;
    }
    output.removeAttribute("title");
    if (typeof value === "boolean") output.textContent = value ? "是" : "否";
    else output.textContent = Number.isFinite(value) ? String(value) : "—";
  }
}

function updateLiveAlgorithm(data, panel, unavailable, renderedStreams) {
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
  const query = data?.action === "getLatest";
  if ((!query && data?.event !== "data") || result.mode !== "live" || !algorithm || typeof algorithm !== "object") return;

  const requestedStreams = panel.subscriptions.get(data.subscriptionId) || [];
  const valid = result.valid === true;
  const reasons = Array.isArray(payload.invalidReasons) ? payload.invalidReasons.join("、") : "";
  for (const group of panel.algorithmGroups) {
    if (renderedStreams && !renderedStreams.includes(group.stream)) continue;
    const hasMetrics = group.metrics.some(({ path }) => Object.prototype.hasOwnProperty.call(algorithm, path[0]));
    if (!hasMetrics && (valid || !requestedStreams.includes(group.stream))) continue;
    if (group.timestamp != null && result.timestampMs < group.timestamp) continue;
    const missingEeg = group.stream === "eeg" && unavailable.eeg;
    const label = unavailable[group.stream] || (query ? "单次查询" : "有效窗口");
    showAlgorithmWindow(group, !valid ? "invalid" : missingEeg ? "missing" : "valid", result.timestampMs,
      valid ? label : `无效窗口${reasons ? ` · ${reasons}` : ""}`, algorithm, true);
  }
}

function getCheckedValues(section, name) {
  return [...section.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);
}

function refreshSubscriptionSelect(panel) {
  const subscriptions = [...panel.subscriptions].filter(([id]) => id !== panel.statusSubscriptionId);
  const { subscriptionSelect, unsubscribeButton } = panel.requests;
  const oldValue = subscriptionSelect.value;
  subscriptionSelect.replaceChildren();
  if (subscriptions.length === 0) {
    const option = new Option("暂无订阅", "");
    subscriptionSelect.add(option);
    subscriptionSelect.disabled = true;
    unsubscribeButton.disabled = true;
    return;
  }
  subscriptionSelect.disabled = false;
  unsubscribeButton.disabled = false;
  for (const [id, streams] of subscriptions) {
    subscriptionSelect.add(new Option(`${id} · ${streams.join(", ")}`, id));
  }
  subscriptionSelect.value = subscriptions.some(([id]) => id === oldValue) ? oldValue : subscriptions[0][0];
}

function showDevice(panel, visible) {
  if (!showConnectedDevicesOnly || panel.section.hidden === !visible) return;
  panel.overview.section.hidden = !visible;
  panel.section.hidden = !visible;
  const sourceLog = sourceLogs[panel.source];
  sourceLog.section.hidden = !visible;
  if (panel.requests) panel.requests.section.hidden = !visible;

  const hasDevice = Object.values(panels).some((item) => !item.section.hidden);
  elements.algorithmHint.hidden = !hasDevice;
  elements.waitingForDevice.hidden = hasDevice;
  elements.protocolRequests.hidden = !hasDevice;
  elements.protocolLogs.hidden = !hasDevice;
  if (visible && sourceLog.followLatest) sourceLog.list.scrollTop = sourceLog.list.scrollHeight;
  drawCharts();
}

function resetSource(panel, status, waiting) {
  clearPendingLiveMessages(panel);
  panel.deviceStatusTimestamp = null;
  clearEegState(panel);
  showDevice(panel, false);
  panel.fields.details.hidden = panel.socket === null;
  for (const value of panel.fields.details.querySelectorAll(".status-list dd")) value.textContent = "—";
  panel.fields.summary.textContent = "尚未收到网关数据。";
  panel.identity = {};
  setSourceStatus(panel, status);
  resetAlgorithmReadings(panel, waiting);
}

function updateDeviceState(data, panel) {
  const result = data?.result || data;
  const state = result?.payload?.status?.connectionState ?? result?.connectionState;
  if (state === undefined) return !panel.section.hidden;

  const timestamp = result.serverTimeMs ?? result.timestampMs;
  if (Number.isFinite(timestamp)) {
    if (panel.deviceStatusTimestamp !== null && timestamp < panel.deviceStatusTimestamp) return false;
    panel.deviceStatusTimestamp = timestamp;
  }
  if (state !== "connected") {
    clearEegState(panel);
    if (!panel.section.hidden) {
      resetAlgorithmReadings(panel, "等待设备接入");
      panel.identity = {};
    }
  } else if (sharedConnection && panel.section.hidden) {
    showMissingEeg(panel, "无脑波数据");
  }
  showDevice(panel, state === "connected");
  return true;
}

function clearEegState(panel) {
  clearTimeout(panel.eegStaleTimer);
  panel.eegStaleTimer = null;
  panel.eegStateTimestamp = null;
  panel.wearState = "unknown";
  panel.eegDisplay = "shown";
}

function showMissingAlgorithmStream(panel, stream, label, timestamp) {
  for (const metric of panel.overview.metrics.filter((metric) => metric.stream === stream)) {
    if (metric.timestamp !== null && timestamp !== null && timestamp < metric.timestamp) continue;
    metric.meta.textContent = metric.output.textContent === "—" ? label : `${label} · 沿用上次有效值`;
    if (timestamp !== null) metric.timestamp = timestamp;
  }
  const group = panel.algorithmGroups.find((group) => group.stream === stream);
  if (group.timestamp == null || timestamp === null || timestamp >= group.timestamp) {
    showAlgorithmWindow(group, "missing", timestamp ?? group.timestamp, label, {}, true);
  }
}

function showMissingEeg(panel, label, timestamp = null, clearChartOnly = false) {
  if (!clearChartOnly) showMissingAlgorithmStream(panel, "eeg", label, timestamp);
  panel.channels = {};
  panel.bandPower = {};
  panel.eegTimestamp = null;
  panel.fields.waveformMeta.textContent = label;
  panel.fields.spectrumMeta.textContent = label;
  drawSourceCharts(panel);
}

function eegWindowState(message, wearState, eegDisplay) {
  const data = message.data;
  const result = data?.result || data;
  const eeg = result?.payload?.algorithm?.eeg;
  const channels = Object.values(eeg?.wave || {}).filter((values) => Array.isArray(values) && values.length);
  const detached = eegDisplay === "hidden" || wearState === "notWorn"
    || channels.some((values) => values.every((value) => [255, -1, "FF", "ff"].includes(value)));
  const zeroPlaceholder = eeg?.quality === 0 && channels.length > 0
    && !channels.some((values) => values.some((value) => Number.isFinite(value) && value !== 0));
  return {
    issue: detached ? "未佩戴" : zeroPlaceholder ? "无有效脑波" : null,
    hasWave: result?.valid === true && !detached && channels.some((values) => values.some(Number.isFinite)),
  };
}

function updateEegState(message, panel, { issue, hasWave }) {
  if (!sharedConnection || panel.section.hidden) return;
  const data = message.data;
  const result = data?.result || data;
  const status = result?.payload?.status || result;
  const display = result?.payload?.display?.eeg;
  if (display === "shown" || display === "hidden") panel.eegDisplay = display;
  if (status?.wearState !== undefined) {
    const wasDetached = panel.wearState === "notWorn" || panel.eegDisplay === "hidden";
    panel.wearState = status.wearState;
    if (status.wearState === "notWorn" || panel.eegDisplay === "hidden") {
      clearTimeout(panel.eegStaleTimer);
      panel.eegStaleTimer = null;
      showMissingEeg(panel, "未佩戴", panel.eegStateTimestamp);
    } else if (wasDetached && !hasWave) {
      showMissingEeg(panel, "无脑波数据", panel.eegStateTimestamp);
    }
  }
  if (data?.event !== "data" && data?.action !== "getLatest") return;
  const timestamp = result.timestampMs;
  if (!Number.isFinite(timestamp) || (panel.eegStateTimestamp !== null && timestamp < panel.eegStateTimestamp)) return;

  const eeg = result.payload?.algorithm?.eeg;
  const streams = panel.subscriptions.get(data.subscriptionId) || [];
  const hasEegWindow = issue === "未佩戴" || eeg
    || (data.event === "data" && streams.includes("eeg") && result.payload?.algorithm && result.valid === false);
  if (!hasEegWindow) return;

  panel.eegStateTimestamp = timestamp;
  clearTimeout(panel.eegStaleTimer);
  panel.eegStaleTimer = null;
  // Only explicit not-worn/display-hidden data clears the 500-point history.
  // Invalid windows and silent gaps keep the previous samples; append logic
  // draws a break instead of replacing the whole trace.
  if (issue === "未佩戴") {
    showMissingEeg(panel, "未佩戴", timestamp);
    return;
  }
  if (result.valid !== true || !eeg || !hasWave) {
    return;
  }
}

function updateSnapshot(data, panel, renderedStreams) {
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
  if (status.signalQuality !== undefined) fields.signalQuality.textContent = status.signalQuality ?? "—";
  if (status.algorithmState !== undefined) {
    fields.algorithmState.textContent = {
      ready: "就绪", warmingUp: "预热中", unavailable: "不可用", error: "异常",
    }[status.algorithmState] ?? status.algorithmState;
  }
  if (Array.isArray(result.availableStreams)) fields.availableStreams.textContent = result.availableStreams.join("、") || "—";
  if ((!renderedStreams || renderedStreams.includes("eeg"))
    && (payload.eegOrigin !== undefined || payload.algorithm?.eeg)) {
    fields.eegOrigin.textContent = { headset: "耳机", headband: "头环" }[payload.eegOrigin] ?? "—";
  }
  for (const [stream, key] of [["eeg.raw", "eegRaw"], ["hr.raw", "hrRaw"]]) {
    const raw = payload[key];
    if (!raw || (renderedStreams && !renderedStreams.includes(stream))) continue;
    fields[key].textContent = `${raw.byteLength ?? "?"} B · ${raw.packetCount ?? "?"} 包`;
    if (stream === "hr.raw") {
      fields.hrRawDetails.hidden = false;
      fields.hrRawTime.textContent = `${readableTimestamp(raw.windowStartMs)} ～ ${readableTimestamp(raw.windowEndMs)} · ${raw.packetBytes ?? "?"} B/包 · Base64`;
      fields.hrRawBytes.textContent = result.valid === false ? "无效窗口" : raw.bytesBase64 ?? "—";
    }
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
  const step = (right - left) / (waveformCapacity - 1);
  for (const [channel, samples] of Object.entries(channels)) {
    ctx.strokeStyle = channelStyles[channel].color;
    ctx.fillStyle = ctx.strokeStyle;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    let previousValid = false;
    samples.forEach((value, index) => {
      if (!Number.isFinite(value)) {
        previousValid = false;
        return;
      }
      const x = right - (samples.length - 1 - index) * step;
      const y = scale(value);
      if (previousValid) ctx.lineTo(x, y);
      else {
        ctx.moveTo(x, y);
        ctx.fillRect(x - 0.8, y - 0.8, 1.6, 1.6);
      }
      previousValid = true;
    });
    ctx.stroke();
    if (channel !== "single") {
      // Mark clipped samples without breaking the polyline: they are valid
      // values at the algorithm's display limit, not missing samples.
      ctx.fillStyle = "#ff6b6b";
      samples.forEach((value, index) => {
        if (!Number.isFinite(value) || Math.abs(value) < sdkDualWaveLimit) return;
        const x = right - (samples.length - 1 - index) * step;
        ctx.fillRect(x - 1, scale(value) - 1, 2, 2);
      });
    }
  }
  ctx.fillStyle = "#aebbd0";
  ctx.textAlign = "left";
  ctx.fillText("0", left, bottom + 18);
  ctx.textAlign = "right";
  ctx.fillText(`${waveformCapacity - 1} 样本`, right, bottom + 18);
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
  if (!Number.isFinite(result?.timestampMs) || (data.event !== "data" && data.action !== "getLatest")) return;
  const connection = result.payload?.status?.connectionState ?? result.connectionState;
  if (connection && connection !== "connected") return;
  const eeg = result?.payload?.algorithm?.eeg;
  const invalid = result?.valid === false;
  const eegSubscribed = panel.subscriptions.get(data?.subscriptionId)?.includes("eeg");
  if (!eeg && !(invalid && eegSubscribed && result.payload?.algorithm)) return;
  if (sharedConnection && panel.eegStateTimestamp !== null
    && result.timestampMs < panel.eegStateTimestamp) return;
  if (panel.eegTimestamp !== null && result.timestampMs < panel.eegTimestamp) return;
  const newWindow = result.timestampMs !== panel.eegTimestamp;
  if (!invalid && Object.values(eeg?.wave || {}).some((wave) => Array.isArray(wave) && wave.some(Number.isFinite))) {
    panel.eegTimestamp = result.timestampMs;
  }

  const bandPower = eeg?.bandPower || {};
  const reportedBands = spectrumBands.map((band) => bandPower[band]).filter(Number.isFinite);
  const spectrumUnavailable = showConnectedDevicesOnly && eeg?.quality === 0
    && reportedBands.length > 0 && reportedBands.every((value) => value === 0);
  panel.bandPower = invalid || spectrumUnavailable ? {} : bandPower;
  if (newWindow) appendWaveChannels(panel, eeg, invalid);
  updateWaveformMeta(panel, invalid);
  panel.fields.spectrumMeta.textContent = invalid ? "无效窗口"
    : spectrumUnavailable ? "暂无有效频段能量"
    : reportedBands.length ? readableTimestamp(result.timestampMs) : "未提供频段能量";
  drawSourceCharts(panel);
}

function appendWaveChannels(panel, eeg, invalid) {
  for (const channel of Object.keys(channelStyles)) {
    const previous = panel.channels[channel] || [];
    const wave = eeg?.wave?.[channel];
    if (invalid || !Array.isArray(wave) || !wave.length) {
      if (Number.isFinite(previous.at(-1))) panel.channels[channel] = [...previous.slice(1 - waveformCapacity), null];
      continue;
    }
    // A short window is one more piece of the same trace, not a replacement.
    // Replacing it made the headset chart jump from 500 points to one window.
    const noSignal = showConnectedDevicesOnly && eeg?.quality === 0 && wave.every((value) => value === 0);
    // A clipped sample is still a sample. Keep it in the trace and mark it in
    // drawWaveform; null is reserved for a window that really has no data.
    const incoming = wave.slice(-waveformCapacity).map((value) => (noSignal ? null : value));
    if (previous.length || noSignal || incoming.some(Number.isFinite)) {
      panel.channels[channel] = previous.concat(incoming).slice(-waveformCapacity);
    }
  }
}

function updateWaveformMeta(panel, invalid) {
  panel.fields.waveformMeta.replaceChildren(...Object.entries(panel.channels).map(([channel, values]) => {
    const label = document.createElement("span");
    label.className = `channel-${channel}`;
    const count = values.filter(Number.isFinite).length;
    const clipped = showConnectedDevicesOnly && channel !== "single"
      ? values.filter((value) => Number.isFinite(value) && Math.abs(value) >= sdkDualWaveLimit).length
      : 0;
    label.textContent = `${channelStyles[channel].label} · ${count ? `${count} 点` : "无有效样本"}`
      + (clipped ? ` · ${clipped} 点饱和` : "");
    if (clipped) label.title = "红色点为算法 ±2000 饱和点，折线保持连续";
    return label;
  }));
  if (!panel.fields.waveformMeta.childElementCount) panel.fields.waveformMeta.textContent = invalid ? "无效窗口" : "未提供波形";
}

function updateLiveWaveform(data, panel) {
  const result = data?.result || data;
  if (!Number.isFinite(result?.timestampMs) || data?.event !== "data") return;
  const connection = result.payload?.status?.connectionState ?? result.connectionState;
  if (connection && connection !== "connected") return;
  const eeg = result?.payload?.algorithm?.eeg;
  const invalid = result?.valid === false;
  const eegSubscribed = panel.subscriptions.get(data?.subscriptionId)?.includes("eeg");
  if (!eeg && !(invalid && eegSubscribed && result.payload?.algorithm)) return;
  const status = result.payload?.status || result;
  if ((status?.wearState ?? panel.wearState) === "notWorn" || panel.eegDisplay === "hidden") return;
  if (sharedConnection && panel.eegStateTimestamp !== null
    && result.timestampMs < panel.eegStateTimestamp) return;
  if (panel.eegTimestamp !== null && result.timestampMs < panel.eegTimestamp) return;
  if (result.timestampMs === panel.eegTimestamp) return;
  if (!invalid && Object.values(eeg?.wave || {}).some((wave) => Array.isArray(wave) && wave.some(Number.isFinite))) {
    panel.eegTimestamp = result.timestampMs;
  }
  appendWaveChannels(panel, eeg, invalid);
  updateWaveformMeta(panel, invalid);
  if (!panel.section.hidden) drawWaveform(panel.fields.waveform, panel.channels);
}

function isLiveDataMessage(message, panel) {
  const data = message?.data;
  if (data?.event !== "data" || data?.mode === "replay") return false;
  const subscriptionId = data.subscriptionId;
  // Window-mode subscriptions already arrive every 600 ms; only as_available
  // subscriptions need display coalescing.
  if (subscriptionId && panel?.subscriptions.has(subscriptionId)
    && !panel.eventModeSubscriptions.has(subscriptionId)) return false;
  return true;
}

function liveMessageStreams(data, panel) {
  const result = data.result || data;
  const payload = result.payload || {};
  const algorithm = payload.algorithm || {};
  const requested = panel.subscriptions.get(data.subscriptionId) || [];
  const emptyInvalidAlgorithm = result.valid === false && payload.algorithm && Object.keys(algorithm).length === 0;
  const streams = algorithmStreamFields.filter(([stream, fields]) =>
    fields.some((field) => Object.prototype.hasOwnProperty.call(algorithm, field))
    || (emptyInvalidAlgorithm && requested.includes(stream))
  ).map(([stream]) => stream);
  if (payload.eegRaw) streams.push("eeg.raw");
  if (payload.hrRaw) streams.push("hr.raw");
  if (payload.status) streams.push("status");
  return streams.length ? streams : ["other"];
}

function queueLiveMessage(message, panel) {
  let pending = pendingLiveMessages.get(panel.source);
  if (!pending) {
    pending = new Map();
    pendingLiveMessages.set(panel.source, pending);
  }
  // EEG, HR and raw windows may arrive separately under one subscription.
  for (const stream of liveMessageStreams(message.data, panel)) {
    const previous = pending.get(stream);
    if (!previous || message.data.timestampMs >= previous.data.timestampMs) pending.set(stream, message);
  }
  if (liveDisplayTimer === null) {
    liveDisplayTimer = window.setTimeout(flushPendingLiveMessages, liveDisplayIntervalMs);
  }
}

function flushLiveMessages(panel) {
  const pending = pendingLiveMessages.get(panel.source);
  if (!pending) return;
  pendingLiveMessages.delete(panel.source);
  const messages = new Map();
  for (const [stream, message] of pending) {
    if (!messages.has(message)) messages.set(message, []);
    messages.get(message).push(stream);
  }
  const ordered = [...messages].sort(
    ([left], [right]) => left.data.timestampMs - right.data.timestampMs,
  );
  for (const [message, streams] of ordered) handleGatewayMessage(message, panel, { log: false, streams });
}

function flushPendingLiveMessages() {
  liveDisplayTimer = null;
  for (const panel of Object.values(panels)) flushLiveMessages(panel);
}

function clearPendingLiveMessages(panel) {
  pendingLiveMessages.delete(panel.source);
  if (pendingLiveMessages.size === 0 && liveDisplayTimer !== null) {
    window.clearTimeout(liveDisplayTimer);
    liveDisplayTimer = null;
  }
}

function handleGatewayMessage(message, panel, { log = true, streams } = {}) {
  if (!message || typeof message !== "object") return;
  const data = message.data;
  if (message.protocolVersion === PROTOCOL_VERSION) {
    const source = sourceByDevice.get(data?.source);
    if (source !== panel.source) {
      const warning = source ? `网关报告来源为「${sourceLabels[source]}」，与当前连接来源不符。` : "报文缺少有效的设备来源。";
      panel.fields.summary.textContent = warning;
      appendLog("error", "ERROR", warning, panel);
      appendLog("error", "← ERR", message, panel);
      return;
    }
  }
  if (message.code !== 200) {
    if (message.message) panel.fields.summary.textContent = message.message;
    appendLog("error", `← ${message.code ?? "ERR"}`, message, panel);
    return;
  }
  if (!showConnectedDevicesOnly || updateDeviceState(data, panel)) {
    const result = data?.result || data;
    const status = result?.payload?.status || result;
    const display = result?.payload?.display?.eeg;
    if (display === "shown" || display === "hidden") panel.eegDisplay = display;
    const wearState = status?.wearState ?? panel.wearState;
    const eeg = eegWindowState(message, wearState, panel.eegDisplay);
    if (panel.eegDisplay === "hidden") showMissingEeg(panel, "未佩戴", result?.timestampMs ?? panel.eegStateTimestamp);
    const unavailable = {
      eeg: eeg.issue,
      hr: result?.payload?.algorithm?.hr?.value === 0 ? "无心率数据" : null,
    };
    updateAlgorithmOverview(data, panel, unavailable, streams);
    updateSnapshot(data, panel, streams);
    updateLiveAlgorithm(data, panel, unavailable, streams);
    if (!streams || streams.includes("eeg") || streams.includes("status")) {
      updateVisuals(data, panel);
      updateEegState(message, panel, eeg);
    }
  }

  const subscriptionId = data?.result?.subscriptionId;
  if (data?.action === "subscribe" && subscriptionId) {
    panel.subscriptions.set(subscriptionId, data.result.streams || []);
    if (data.result.eventMode === "as_available") panel.eventModeSubscriptions.add(subscriptionId);
    else panel.eventModeSubscriptions.delete(subscriptionId);
    if (data.requestId === panel.statusSubscriptionRequestId) {
      panel.statusSubscriptionRequestId = null;
      panel.statusSubscriptionId = subscriptionId;
      // A device may connect between the initial status query and the subscription.
      sendRequest("getStatus", {}, panel);
    }
  }
  if (data?.action === "unsubscribe" && subscriptionId) {
    panel.subscriptions.delete(subscriptionId);
    panel.eventModeSubscriptions.delete(subscriptionId);
    if (subscriptionId === panel.statusSubscriptionId) {
      panel.statusSubscriptionId = null;
      subscribeToStatus(panel);
    }
  }
  if (subscriptionId) refreshSubscriptionSelect(panel);
  if (log) appendLog("in", "← IN", message, panel);
  if (panel.initialStatusRequestId && data?.requestId === panel.initialStatusRequestId) {
    panel.initialStatusRequestId = null;
    subscribeToStatus(panel);
  }
}

function subscribeToStatus(panel) {
  const requestId = sendRequest("subscribe", { streams: ["status"] }, panel);
  if (showConnectedDevicesOnly) panel.statusSubscriptionRequestId = requestId;
}

function sendPayload(payload, panel) {
  if (panel.socket?.readyState !== WebSocket.OPEN) {
    appendLog("error", "ERROR", "请先连接该来源的网关。", panel);
    return false;
  }
  let request = payload;
  if (sharedConnection) {
    if (payload.protocolVersion !== PROTOCOL_VERSION && panel !== localPanel) {
      appendLog("error", "ERROR", `头环请求必须使用 v${PROTOCOL_VERSION}。`, panel);
      return false;
    }
    if (payload.protocolVersion === PROTOCOL_VERSION) {
      if (!payload.params || typeof payload.params !== "object" || Array.isArray(payload.params)) {
        appendLog("error", "ERROR", "双设备请求的 params 必须为 JSON 对象。", panel);
        return false;
      }
      request = { ...payload, params: { ...payload.params, source: deviceBySource[panel.source] } };
    }
  }
  panel.socket.send(JSON.stringify(request));
  appendLog("out", "OUT →", request, panel);
  return true;
}

function sendRequest(action, params, panel) {
  const request = {
    protocolVersion: PROTOCOL_VERSION,
    messageType: "request",
    requestId: createRequestId(),
    action,
    params,
  };
  return sendPayload(request, panel) ? request.requestId : null;
}

function connect(panel) {
  if (panel.socket) return;
  let endpoint;
  let socket;
  try {
    endpoint = new URL(panel.connection.endpoint.value.trim());
    if (!["ws:", "wss:"].includes(endpoint.protocol)) throw new Error("地址必须以 ws:// 或 wss:// 开头。");
    if (Object.values(panels).some((other) => other.socket?.url === endpoint.href)) {
      throw new Error("此网关已关联其他来源，请为当前来源填写独立的 WebSocket 地址。");
    }
    socket = new WebSocket(endpoint.href, SUBPROTOCOL);
  } catch (error) {
    appendLog("error", "ERROR", error.message, panel);
    return;
  }
  const connectedPanels = sharedConnection ? Object.values(panels) : [panel];
  for (const item of connectedPanels) {
    item.socket = socket;
    resetSource(item, "正在连接", "等待算法数据");
    appendLog("system", "SYSTEM", `连接 ${endpoint.href}（子协议 ${SUBPROTOCOL}）`, item);
  }
  refreshConnectionControls(panel);

  socket.addEventListener("open", () => {
    refreshConnectionControls(panel);
    for (const item of connectedPanels) {
      setSourceStatus(item, "网关已连接");
      appendLog("system", "SYSTEM", "WebSocket 已建立，正在自动发送 getStatus。", item);
      const requestId = sendRequest("getStatus", {}, item);
      if (localConnection) item.initialStatusRequestId = requestId;
    }
  });
  socket.addEventListener("message", (event) => {
    try {
      const message = JSON.parse(event.data);
      const source = sourceByDevice.get(message?.data?.source);
      const target = sharedConnection && source ? panels[source] : panel;
      if (message?.code === 200 && (message.protocolVersion !== PROTOCOL_VERSION || source === target.source)
        && Number.isFinite(message.data?.timestampMs) && isLiveDataMessage(message, target)) {
        appendLog("in", "← IN", message, target);
        const display = message.data?.payload?.display?.eeg;
        if (display === "shown" || display === "hidden") target.eegDisplay = display;
        updateLiveWaveform(message.data, target);
        queueLiveMessage(message, target);
      } else {
        flushLiveMessages(target);
        handleGatewayMessage(message, target);
      }
    } catch {
      appendLog("error", "ERROR", `无法读取网关消息（${event.data.length} 字符）。`, panel);
    }
  });
  socket.addEventListener("error", () => {
    for (const item of connectedPanels) appendLog("error", "ERROR", "WebSocket 发生网络错误或网关拒绝了子协议。", item);
  });
  socket.addEventListener("close", (event) => {
    for (const item of connectedPanels) {
      item.socket = null;
      item.initialStatusRequestId = null;
      item.statusSubscriptionRequestId = null;
      item.statusSubscriptionId = null;
      item.subscriptions.clear();
      item.eventModeSubscriptions.clear();
      resetSource(item, "已断开", "等待连接网关");
      refreshSubscriptionSelect(item);
      appendLog("system", "SYSTEM", `连接已关闭（code=${event.code}${event.reason ? `，${event.reason}` : ""}）。`, item);
    }
    refreshConnectionControls(panel);
  });
}

function createRequestSection(panel) {
  const section = elements.sourceRequestTemplate.content.firstElementChild.cloneNode(true);
  section.dataset.source = panel.source;
  const title = section.querySelector("[data-request-title]");
  title.id = `requests-${panel.source}`;
  title.textContent = sourceLabels[panel.source];
  section.setAttribute("aria-labelledby", title.id);

  const subscriptionSelect = section.querySelector("[data-subscription]");
  const unsubscribeButton = section.querySelector("[data-unsubscribe]");
  const includeInvalid = section.querySelector("[data-include-invalid]");
  const customRequest = section.querySelector("textarea");
  customRequest.value = JSON.stringify({
    protocolVersion: PROTOCOL_VERSION, messageType: "request", requestId: "", action: "getStatus", params: {},
  }, null, 2);
  if (localConnection) {
    const statusOption = section.querySelector('input[name="subscribeStream"][value="status"]');
    statusOption.checked = false;
    statusOption.closest("label").hidden = showConnectedDevicesOnly;
  }

  section.querySelector("[data-get-status]").addEventListener("click", () => sendRequest("getStatus", {}, panel));
  section.querySelector("[data-get-latest]").addEventListener("click", () => {
    const streams = getCheckedValues(section, "latestStream");
    if (streams.length === 0) return appendLog("error", "ERROR", "getLatest 至少选择 EEG 或 HR 一个流。", panel);
    sendRequest("getLatest", { streams }, panel);
  });
  section.querySelector("[data-subscribe]").addEventListener("click", () => {
    const streams = getCheckedValues(section, "subscribeStream");
    if (streams.length === 0) return appendLog("error", "ERROR", "subscribe 至少选择一个流。", panel);
    sendRequest("subscribe", { streams, includeInvalid: includeInvalid.checked, mode: "as_available" }, panel);
  });
  unsubscribeButton.addEventListener("click", () => {
    const subscriptionId = subscriptionSelect.value;
    if (!subscriptionId) return appendLog("error", "ERROR", "没有可取消的订阅。", panel);
    sendRequest("unsubscribe", { subscriptionId }, panel);
  });
  section.querySelector("[data-send-custom]").addEventListener("click", () => {
    try {
      const payload = JSON.parse(customRequest.value);
      if (payload.requestId === "") payload.requestId = createRequestId();
      sendPayload(payload, panel);
    } catch (error) {
      appendLog("error", "ERROR", `自定义 JSON 无法解析：${error.message}`, panel);
    }
  });
  elements.sourceRequests.append(section);
  return { section, subscriptionSelect, unsubscribeButton };
}

for (const panel of Object.values(panels)) {
  if (!localConnection || panel === localPanel) {
    panel.connection = createConnectionRow(panel);
    refreshConnectionControls(panel);
  }
  if (!fixedSource || panel === localPanel) panel.requests = createRequestSection(panel);
  resetSource(panel, "未连接", fixedSource && panel.source !== configuredSource ? "等待其他网关接入" : "等待连接网关");
}
if (typeof configuredEndpoint === "string") {
  connect(localPanel);
}

window.addEventListener("resize", drawCharts);
