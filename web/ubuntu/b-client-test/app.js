"use strict";

const PROTOCOL_VERSION = window.NEUROBRIDGE_VERSION.protocolVersion;
const SUBPROTOCOL = "neurobridge.v1";
const LOG_LIMIT = 500;
let socket = null;
let subscriptions = new Map();
let logEntries = [];

const elements = {
  endpoint: document.querySelector("#endpoint"),
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
  modeValue: document.querySelector("#modeValue"),
  deviceConnectionValue: document.querySelector("#deviceConnectionValue"),
  wearValue: document.querySelector("#wearValue"),
  batteryValue: document.querySelector("#batteryValue"),
  subjectValue: document.querySelector("#subjectValue"),
  timestampValue: document.querySelector("#timestampValue"),
  latestSummary: document.querySelector("#latestSummary"),
};

if (typeof window.NEUROBRIDGE_B_CLIENT_ENDPOINT === "string") {
  elements.endpoint.value = window.NEUROBRIDGE_B_CLIENT_ENDPOINT;
} else if (window.location.search) {
  const linkedEndpoint = new URLSearchParams(window.location.search).get("endpoint");
  if (linkedEndpoint) elements.endpoint.value = linkedEndpoint;
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
}

function timeLabel(date = new Date()) {
  return date.toISOString();
}

function appendLog(kind, label, body) {
  const entry = { time: timeLabel(), kind, label, body: typeof body === "string" ? body : JSON.stringify(body, (key, value) => ["bytesBase64", "wave"].includes(key) ? "[原始波形已省略]" : value, 2) };
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
  if (logEntries.length > LOG_LIMIT) {
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

const metricNames = ["relaxation", "pressure", "heartRate", "hrv", "attention", "flow"];
const bandNames = ["delta", "theta", "alpha", "beta", "gamma", "lowBeta", "highBeta"];
const bandLabels = ["δ", "θ", "α", "β", "γ", "低 β", "高 β"];
const channelColors = { left: "#70dbe8", right: "#b79aff", single: "#f3bc70" };
let chartState = { wave: {}, bands: {} };
let readingTimes = new Map();
let sourceState = {};
const byId = (id) => document.querySelector(`#${id}`);
const finite = (value) => typeof value === "number" && Number.isFinite(value);
const numberLabel = (value) => finite(value) ? String(Number(value.toPrecision(6))) : "未提供";
const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

function resetReadings(reason = "尚未收到数据") {
  readingTimes.clear();
  for (const name of metricNames) {
    byId(`${name}Value`).textContent = "—";
    byId(`${name}Meta`).textContent = reason;
  }
  chartState = { wave: {}, bands: {} };
  byId("waveformMeta").textContent = reason;
  byId("spectrumMeta").textContent = reason;
  for (const name of bandNames) byId(`${name}BandValue`).textContent = "未提供";
  drawCharts();
}

function resetSnapshot() {
  sourceState = {};
  for (const name of ["modeValue", "deviceConnectionValue", "wearValue", "batteryValue", "subjectValue", "timestampValue"]) elements[name].textContent = "—";
  elements.latestSummary.textContent = "尚未收到网关数据。";
  resetReadings("连接已重置，等待新数据");
}

function acceptReading(name, timestamp) {
  const previous = readingTimes.get(name);
  if (finite(timestamp) && finite(previous) && timestamp < previous) return false;
  readingTimes.set(name, finite(timestamp) ? timestamp : null);
  return true;
}

function updateMetric(name, value, timestamp, valid) {
  if (!acceptReading(name, timestamp)) return;
  byId(`${name}Value`).textContent = valid && finite(value) ? `${numberLabel(value)}${name === "heartRate" ? " bpm" : ""}` : "—";
  byId(`${name}Meta`).textContent = !valid ? "该数据窗口无效" : finite(value) ? `数据时间：${readableTimestamp(timestamp)}` : "算法未提供";
}

function canvasFrame(id) {
  const canvas = byId(id);
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const width = Math.max(280, canvas.clientWidth || 600), height = 230;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.fillStyle = "#061421"; ctx.fillRect(0, 0, width, height);
  ctx.font = "11px system-ui"; ctx.fillStyle = "#91a6be";
  return { ctx, width, height, left: 62, top: 18, bottom: height - 28, right: width - 16 };
}

function axes(frame, low, high) {
  const { ctx, left, right, top, bottom } = frame;
  const y = (value) => bottom - (value - low) / (high - low) * (bottom - top);
  ctx.strokeStyle = "#263e55"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const value = low + (high - low) * i / 4, at = y(value);
    ctx.beginPath(); ctx.moveTo(left, at); ctx.lineTo(right, at); ctx.stroke();
    ctx.textAlign = "right"; ctx.fillStyle = "#91a6be";
    ctx.fillText(Number(value.toPrecision(3)).toString(), left - 7, at + 4);
  }
  return y;
}

function drawCharts() {
  const waveFrame = canvasFrame("waveformCanvas");
  if (waveFrame) {
    const channels = Object.entries(chartState.wave).filter(([, values]) => Array.isArray(values) && values.some(finite));
    const values = channels.flatMap(([, items]) => items.filter(finite));
    if (!values.length) waveFrame.ctx.fillText("等待有效脑波数据", 62, 115);
    else {
      let low = Math.min(...values), high = Math.max(...values);
      if (low === high) { low -= 1; high += 1; }
      const y = axes(waveFrame, low, high);
      const { ctx, left, right, bottom } = waveFrame;
      const length = Math.max(...channels.map(([, items]) => items.length));
      for (const [name, items] of channels) {
        ctx.strokeStyle = channelColors[name]; ctx.lineWidth = 1.4; ctx.beginPath();
        let started = false;
        items.forEach((value, index) => {
          if (!finite(value)) { started = false; return; }
          const x = left + index / Math.max(1, length - 1) * (right - left);
          if (started) ctx.lineTo(x, y(value)); else ctx.moveTo(x, y(value));
          started = true;
        });
        ctx.stroke();
      }
      ctx.textAlign = "left"; ctx.fillText("0", left, bottom + 19);
      ctx.textAlign = "right"; ctx.fillText(`${length - 1} 样本`, right, bottom + 19);
    }
  }
  const frame = canvasFrame("spectrumCanvas");
  if (!frame) return;
  const values = bandNames.map((name) => chartState.bands[name]).filter(finite);
  if (!values.length) { frame.ctx.fillText("等待有效频段能量", 62, 115); return; }
  const low = Math.min(0, ...values), high = Math.max(0, ...values) || 1;
  const y = axes(frame, low === high ? low - 1 : low, high);
  const { ctx, left, right, bottom } = frame, step = (right - left) / bandNames.length;
  bandNames.forEach((name, index) => {
    const value = chartState.bands[name], x = left + step * (index + .5);
    if (finite(value)) {
      ctx.fillStyle = "#70dbe8";
      ctx.fillRect(x - step * .28, Math.min(y(0), y(value)), step * .56, Math.max(1, Math.abs(y(0) - y(value))));
    }
    ctx.fillStyle = "#b8cce1"; ctx.textAlign = "center"; ctx.fillText(bandLabels[index], x, bottom + 19);
  });
}

function updateSnapshot(data) {
  if (!data || typeof data !== "object") return;
  const result = data.result && typeof data.result === "object" ? data.result : data;
  const payload = result.payload || data.payload || {};
  // getStatus returns fields directly; status events put them under payload.status.
  const status = result.status || payload.status || result;
  const incoming = { gatewayBootId: result.gatewayBootId ?? data.gatewayBootId, mode: result.mode ?? data.mode, subjectId: own(result, "subjectId") ? result.subjectId : data.subjectId };
  if (Object.entries(incoming).some(([key, value]) => value !== undefined && own(sourceState, key) && sourceState[key] !== value)) resetReadings("数据来源已变化，等待新数据");
  for (const [key, value] of Object.entries(incoming)) if (value !== undefined) sourceState[key] = value;
  const timestamp = result.timestampMs ?? data.timestampMs;
  if (incoming.mode !== undefined) elements.modeValue.textContent = ({ live: "实时（live）", replay: "录播（replay）" })[incoming.mode] || "未知";
  if (incoming.subjectId !== undefined) elements.subjectValue.textContent = incoming.subjectId ?? "未配置";
  if (timestamp !== undefined || result.serverTimeMs !== undefined) elements.timestampValue.textContent = readableTimestamp(timestamp ?? result.serverTimeMs);
  if (status.connectionState !== undefined) elements.deviceConnectionValue.textContent = ({ connected: "已连接", connecting: "连接中", disconnected: "未连接" })[status.connectionState] || "未知";
  if (status.wearState !== undefined) elements.wearValue.textContent = ({ worn: "已佩戴，接触良好", notWorn: "未佩戴或接触不良", unknown: "未知" })[status.wearState] || "未知";
  if (status.batteryPercent !== undefined) elements.batteryValue.textContent = finite(status.batteryPercent) && status.batteryPercent >= 0 && status.batteryPercent <= 100 ? `${status.batteryPercent}%` : "未知";
  if (data.event === "replayEnded" || (sourceState.mode === "live" && status.connectionState && status.connectionState !== "connected")) resetReadings(data.event === "replayEnded" ? "本轮录播结束，等待下一轮" : "头环未连接，等待新数据");

  const valid = data.valid !== false && result.valid !== false;
  const algorithm = payload.algorithm;
  if (!valid && !algorithm) resetReadings("该数据窗口无效，等待有效数据");
  if (algorithm && typeof algorithm === "object") {
    for (const [key, name] of [["relaxation", "relaxation"], ["pressure", "pressure"], ["attention", "attention"]]) if (own(algorithm, key)) updateMetric(name, algorithm[key], timestamp, valid);
    if (own(algorithm, "hr")) {
      updateMetric("heartRate", algorithm.hr?.value, timestamp, valid);
      updateMetric("hrv", algorithm.hr?.hrv, timestamp, valid);
    }
    if (own(algorithm, "flow")) updateMetric("flow", algorithm.flow?.meditation, timestamp, valid);
    if (own(algorithm, "eeg")) {
      const eeg = algorithm.eeg;
      if (acceptReading("wave", timestamp)) {
        chartState.wave = {};
        for (const name of Object.keys(channelColors)) if (valid && Array.isArray(eeg?.wave?.[name])) chartState.wave[name] = eeg.wave[name].slice(-1200).map((value) => finite(value) ? value : null);
        byId("waveformMeta").textContent = !valid ? "该数据窗口无效" : `数据时间：${readableTimestamp(timestamp)} · ${Object.entries(chartState.wave).map(([name, values]) => `${({ left: "左", right: "右", single: "单" })[name]}通道 ${values.length} 点`).join(" / ") || "未提供波形"}`;
      }
      if (acceptReading("bands", timestamp)) {
        chartState.bands = valid && eeg?.bandPower && typeof eeg.bandPower === "object" ? Object.fromEntries(bandNames.map((name) => [name, eeg.bandPower[name]])) : {};
        for (const name of bandNames) byId(`${name}BandValue`).textContent = numberLabel(chartState.bands[name]);
        byId("spectrumMeta").textContent = !valid ? "该数据窗口无效" : `数据时间：${readableTimestamp(timestamp)} · 频段能量原值`;
      }
      drawCharts();
    }
  }
  const messages = [];
  if (data.event) messages.push(`事件：${data.event}`);
  if (!valid) messages.push("该数据窗口无效");
  if (payload.algorithm) messages.push("已收到算法数据");
  if (payload.eegRaw) messages.push(`EEG 原始字节：${payload.eegRaw.byteLength ?? "?"} B`);
  if (payload.hrRaw) messages.push(`HR 原始字节：${payload.hrRaw.byteLength ?? "?"} B`);
  if (messages.length) elements.latestSummary.textContent = messages.join(" · ");
}

function handleGatewayMessage(message) {
  if (!message || typeof message !== "object") return;
  const data = message.data;

  if (message.code !== 200) {
    appendLog("error", `← ${message.code ?? "ERR"}`, message);
    return;
  }

  updateSnapshot(data);

  if (data?.action === "subscribe" && data.result?.subscriptionId) {
    subscriptions.set(data.result.subscriptionId, data.result.streams || []);
    refreshSubscriptionSelect();
  }
  if (data?.action === "unsubscribe" && data.result?.subscriptionId) {
    subscriptions.delete(data.result.subscriptionId);
    refreshSubscriptionSelect();
  }
  appendLog("in", "← IN", message);
}

function sendPayload(payload) {
  if (!isConnected()) {
    appendLog("error", "ERROR", "尚未建立 WebSocket 连接，无法发送请求。");
    return false;
  }
  try {
    socket.send(JSON.stringify(payload));
  } catch (error) {
    appendLog("error", "ERROR", `发送失败：${error.message}`);
    return false;
  }
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
  return sendPayload(request);
}

function connect() {
  if (socket && [WebSocket.CONNECTING, WebSocket.OPEN].includes(socket.readyState)) return;
  const endpoint = elements.endpoint.value.trim();
  if (!endpoint.startsWith("ws://") && !endpoint.startsWith("wss://")) {
    appendLog("error", "ERROR", "地址必须以 ws:// 或 wss:// 开头。");
    return;
  }
  try {
    resetSnapshot();
    setConnectionState("wait", "正在连接");
    appendLog("system", "SYSTEM", `连接 ${endpoint}（子协议 ${SUBPROTOCOL}，页面来源 ${window.location.protocol}）`);
    socket = new WebSocket(endpoint, SUBPROTOCOL);
  } catch (error) {
    setConnectionState("off", "未连接");
    appendLog("error", "ERROR", error.message);
    return;
  }
  const connection = socket;
  const startedAt = Date.now();
  connection.addEventListener("open", () => {
    if (socket !== connection) return;
    setConnectionState("on", "已连接");
    appendLog("system", "SYSTEM", `WebSocket 已建立（连接耗时 ${Date.now() - startedAt} ms），自动查询 getStatus。`);
    sendRequest("getStatus", {});
  });
  connection.addEventListener("message", (event) => {
    if (socket !== connection) return;
    try {
      handleGatewayMessage(JSON.parse(event.data));
    } catch {
      appendLog("error", "ERROR", `收到无法处理的消息（字符数 ${String(event.data).length}）。`);
    }
  });
  connection.addEventListener("error", () => {
    if (socket !== connection) return;
    appendLog("error", "ERROR", `连接 ${endpoint} 失败或中断；浏览器不提供底层原因，请对照网关网络日志、两端 IP/掩码、端口和子协议。`);
  });
  connection.addEventListener("close", (event) => {
    if (socket !== connection) return;
    setConnectionState("off", "已断开");
    resetSnapshot();
    subscriptions.clear();
    refreshSubscriptionSelect();
    appendLog("system", "SYSTEM", `连接已关闭（地址=${endpoint}，code=${event.code}，clean=${event.wasClean}，持续=${Date.now() - startedAt} ms${event.reason ? `，${event.reason}` : ""}）。重新连接后需重新订阅。`);
    socket = null;
  });
}

elements.connectButton.addEventListener("click", connect);
elements.disconnectButton.addEventListener("click", () => {
  if (socket) socket.close(1000, "客户端主动断开连接");
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

window.addEventListener("resize", drawCharts);
resetReadings();
refreshSubscriptionSelect();
appendLog("system", "SYSTEM", window.NEUROBRIDGE_B_CLIENT_ENDPOINT
  ? `B 端联调台已就绪；使用采集服务地址 ${elements.endpoint.value}，不会发送任何保活帧。`
  : `B 端联调台已就绪；使用部署地址 ${elements.endpoint.value}，不会发送任何保活帧。`);
connect();
