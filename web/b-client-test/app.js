"use strict";

const PROTOCOL_VERSION = window.NEUROBRIDGE_VERSION.protocolVersion;
const SUBPROTOCOL = "neurobridge.v1";
let socket = null;
let subscriptions = new Map();
let logEntries = [];
const waveformState = { values: [], channel: "single" };
const spectrumBands = ["delta", "theta", "alpha", "lowBeta", "highBeta", "beta", "gamma"];

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
  waveformCanvas: document.querySelector("#waveformCanvas"),
  spectrumCanvas: document.querySelector("#spectrumCanvas"),
  waveformMeta: document.querySelector("#waveformMeta"),
  spectrumMeta: document.querySelector("#spectrumMeta"),
};

if (typeof window.NEUROBRIDGE_B_CLIENT_ENDPOINT === "string") {
  elements.endpoint.value = window.NEUROBRIDGE_B_CLIENT_ENDPOINT;
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
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function appendLog(kind, label, body) {
  const entry = { time: timeLabel(), kind, label, body: typeof body === "string" ? body : JSON.stringify(body, null, 2) };
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

function updateSnapshot(data) {
  if (!data || typeof data !== "object") return;
  const result = data.result && typeof data.result === "object" ? data.result : data;
  const payload = result.payload || data.payload || {};
  const status = result.status || payload.status || {};
  const mode = result.mode ?? data.mode;
  const subjectId = result.subjectId ?? data.subjectId;
  const timestampMs = result.timestampMs ?? data.timestampMs;

  if (mode !== undefined) elements.modeValue.textContent = mode;
  if (subjectId !== undefined) elements.subjectValue.textContent = subjectId ?? "未配置";
  if (timestampMs !== undefined) elements.timestampValue.textContent = readableTimestamp(timestampMs);
  if (status.connectionState !== undefined) elements.deviceConnectionValue.textContent = status.connectionState;
  if (status.wearState !== undefined) elements.wearValue.textContent = status.wearState;
  if (status.batteryPercent !== undefined) {
    elements.batteryValue.textContent = status.batteryPercent === null ? "—" : `${status.batteryPercent}%`;
  }

  const messages = [];
  if (data.event) messages.push(`事件：${data.event}`);
  if (data.valid === false || result.valid === false) messages.push("该数据窗口无效");
  if (payload.algorithm) messages.push("已收到算法数据");
  if (payload.eegRaw) messages.push(`EEG 原始字节：${payload.eegRaw.byteLength ?? "?"} B`);
  if (payload.hrRaw) messages.push(`HR 原始字节：${payload.hrRaw.byteLength ?? "?"} B`);
  if (messages.length) elements.latestSummary.textContent = messages.join(" · ");
}

function drawCharts() {
  drawWaveform(elements.waveformCanvas, waveformState.values);
  drawSpectrum(elements.spectrumCanvas, waveformState.bandPower || {});
}
function fitCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1, rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, rect.width * ratio); canvas.height = Math.max(1, rect.height * ratio);
  const ctx = canvas.getContext("2d"); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); return [ctx, rect.width, rect.height];
}
function drawWaveform(canvas, values) {
  if (!canvas) return; const [ctx,w,h] = fitCanvas(canvas); ctx.clearRect(0,0,w,h);
  ctx.strokeStyle = "rgba(91,127,162,.3)"; ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(0,h/2); ctx.lineTo(w,h/2); ctx.stroke();
  if (!values.length) return; const min=Math.min(...values), max=Math.max(...values), span=max-min||1;
  ctx.strokeStyle="#5fd1e8"; ctx.lineWidth=1.6; ctx.beginPath(); values.forEach((v,i)=>{const x=i/(values.length-1||1)*w,y=h-((v-min)/span)*h*.82-h*.09;i?ctx.lineTo(x,y):ctx.moveTo(x,y)}); ctx.stroke();
}
function drawSpectrum(canvas, bands) {
  if (!canvas) return; const [ctx,w,h] = fitCanvas(canvas); ctx.clearRect(0,0,w,h); const vals=spectrumBands.map(k=>Number(bands[k])||0), max=Math.max(...vals,1), gap=8, bw=(w-gap*(vals.length+1))/vals.length;
  vals.forEach((v,i)=>{const bh=v/max*(h-42), x=gap+i*(bw+gap); ctx.fillStyle="#8f7bea"; ctx.fillRect(x,h-22-bh,bw,bh); ctx.fillStyle="#aebbd0"; ctx.font="11px system-ui"; ctx.textAlign="center"; ctx.fillText(spectrumBands[i],x+bw/2,h-7)});
}
function isAllFf(values) {
  if (!Array.isArray(values) || values.length === 0) return false;
  return values.every((value) => value === 255 || value === -1 || (typeof value === "string" && /^f+$/i.test(value.replace(/[\s:]/g, ""))));
}
function updateVisuals(data) {
  const payload=(data?.result?.payload || data?.payload || {}), eeg=payload.algorithm?.eeg || {}, wave=eeg.wave || {};
  const channel = Array.isArray(wave.single) ? "single" : Array.isArray(wave.left) ? "left" : Array.isArray(wave.right) ? "right" : null;
  const unworn = isAllFf(wave.single) || isAllFf(wave.left) || isAllFf(wave.right);
  if (unworn) {
    elements.wearValue.textContent = "未佩戴";
    elements.latestSummary.textContent = "脑波窗口数据全为 FF，判定为未佩戴状态。";
  }
  if (channel) { waveformState.values=wave[channel].slice(-1200); waveformState.channel=channel; elements.waveformMeta.textContent=`${channel} · ${waveformState.values.length} 点`; }
  if (eeg.bandPower) { waveformState.bandPower=eeg.bandPower; elements.spectrumMeta.textContent="已更新"; }
  if (channel || eeg.bandPower) drawCharts();
}
function handleGatewayMessage(message) {
  if (!message || typeof message !== "object") return;
  const data = message.data;
  updateSnapshot(data);
  updateVisuals(data);

  if (message.code !== 200) {
    appendLog("error", `← ${message.code ?? "ERR"}`, message);
    return;
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
  return sendPayload(request);
}

function connect() {
  const endpoint = elements.endpoint.value.trim();
  if (!endpoint.startsWith("ws://") && !endpoint.startsWith("wss://")) {
    appendLog("error", "ERROR", "地址必须以 ws:// 或 wss:// 开头。");
    return;
  }
  try {
    setConnectionState("wait", "正在连接");
    appendLog("system", "SYSTEM", `连接 ${endpoint}（子协议 ${SUBPROTOCOL}）`);
    socket = new WebSocket(endpoint, SUBPROTOCOL);
  } catch (error) {
    setConnectionState("off", "未连接");
    appendLog("error", "ERROR", error.message);
    return;
  }
  socket.addEventListener("open", () => {
    setConnectionState("on", "已连接");
    appendLog("system", "SYSTEM", "WebSocket 已建立，正在自动发送 getStatus。" );
    sendRequest("getStatus", {});
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
    subscriptions.clear();
    refreshSubscriptionSelect();
    appendLog("system", "SYSTEM", `连接已关闭（code=${event.code}${event.reason ? `，${event.reason}` : ""}）。`);
    socket = null;
  });
}

elements.connectButton.addEventListener("click", connect);
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
appendLog("system", "SYSTEM", window.NEUROBRIDGE_B_CLIENT_ENDPOINT
  ? `网关联调台已就绪；已使用本机服务地址 ${elements.endpoint.value}，不会发送任何保活帧。`
  : "网关联调台已就绪；不会发送任何保活帧。");
if (typeof window.NEUROBRIDGE_B_CLIENT_ENDPOINT === "string") {
  connect();
}

window.addEventListener("resize", drawCharts);
drawCharts();
