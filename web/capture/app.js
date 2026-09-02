"use strict";

const PROTOCOL_VERSION = window.NEUROBRIDGE_VERSION.protocolVersion;
const SUBPROTOCOL = "neurobridge.v1";
const MAX_DATA_LINES = 800;
const MAX_PROTOCOL_LINES = 300;

const elements = {
  endpoint: document.querySelector("#endpoint"),
  connect: document.querySelector("#connectButton"),
  disconnect: document.querySelector("#disconnectButton"),
  status: document.querySelector("#statusButton"),
  start: document.querySelector("#startButton"),
  stop: document.querySelector("#stopButton"),
  clear: document.querySelector("#clearButton"),
  export: document.querySelector("#exportButton"),
  gatewayState: document.querySelector("#gatewayState"),
  gatewayMessage: document.querySelector("#gatewayMessage"),
  mode: document.querySelector("#modeValue"),
  deviceState: document.querySelector("#deviceState"),
  algorithmState: document.querySelector("#algorithmState"),
  subscriptionState: document.querySelector("#subscriptionState"),
  eventCount: document.querySelector("#eventCount"),
  eegCount: document.querySelector("#eegCount"),
  hrCount: document.querySelector("#hrCount"),
  sequence: document.querySelector("#sequenceValue"),
  lostPackets: document.querySelector("#lostPackets"),
  lossRate: document.querySelector("#lossRate"),
  hexFormat: document.querySelector("#hexFormatButton"),
  decimalFormat: document.querySelector("#decimalFormatButton"),
  rawData: document.querySelector("#rawData"),
  decodedData: document.querySelector("#decodedData"),
  protocolLog: document.querySelector("#protocolLog"),
};

let socket = null;
let subscriptionId = null;
let dataRecords = [];
let protocolLines = [];
let stats = createStats();
let displayFormat = "hex";
let deviceConnectionState = null;

const DEVICE_STATE_LABELS = {
  not_connected: "未连接",
  connecting: "连接中",
  connected: "已连接",
  validating: "校验中",
  validation_failed: "校验失败",
  validated: "校验成功",
  disconnected: "已断开",
};

const DEVICE_STATE_MESSAGES = {
  not_connected: "尚未找到可识别的 USB 串口设备，网关会继续遍历候选串口。",
  connecting: "正在遍历并打开 USB 串口，等待固定握手。",
  connected: "已收到目标设备握手并独占串口，准备回写握手 ACK。",
  validating: "已回写握手 ACK，正在等待设备返回校验结果 01；期间若收到完整周期握手会重新 ACK，不会立即失败。",
  validation_failed: "数据路径校验失败；请从日志区分 ACK 未返回 01、算法未就绪或 E1 写入失败。E1 本身没有响应。",
  validated: "ACK 已通过 01 校验、本地算法已就绪且 E1 已写入，可以接收并转发耳机数据。",
  disconnected: "已识别的 USB 串口连接已断开，网关将按配置重新连接。",
};

if (typeof window.NEUROBRIDGE_B_CLIENT_ENDPOINT === "string") {
  elements.endpoint.value = window.NEUROBRIDGE_B_CLIENT_ENDPOINT;
}

function createStats() {
  return { events: 0, eegPackets: 0, eegBytes: 0, hrPackets: 0, hrBytes: 0, lost: 0, receivedUnique: 0, lastSequence: null };
}

function isConnected() {
  return socket && socket.readyState === WebSocket.OPEN;
}

function requestId() {
  return window.crypto?.randomUUID?.() || `capture-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function timeLabel() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function setState(state, label, message) {
  elements.gatewayState.dataset.state = state;
  elements.gatewayState.textContent = label;
  if (message) elements.gatewayMessage.textContent = message;
}

function refreshControls() {
  const connected = isConnected();
  elements.connect.disabled = connected || socket?.readyState === WebSocket.CONNECTING;
  elements.disconnect.disabled = !connected;
  elements.status.disabled = !connected;
  elements.start.disabled = !connected || Boolean(subscriptionId);
  elements.stop.disabled = !connected || !subscriptionId;
  elements.subscriptionState.textContent = subscriptionId ? `接收中 · ${subscriptionId}` : "未订阅";
}

function appendLine(lines, target, line, maximum) {
  lines.push(`${timeLabel()}  ${line}`);
  if (lines.length > maximum) lines.splice(0, lines.length - maximum);
  target.textContent = lines.join("\n");
  target.scrollTop = target.scrollHeight;
}

function appendDataRecord(record) {
  dataRecords.push({ ...record, time: timeLabel() });
  if (dataRecords.length > MAX_DATA_LINES) dataRecords.splice(0, dataRecords.length - MAX_DATA_LINES);
}

function appendProtocol(direction, value) {
  const body = typeof value === "string" ? value : JSON.stringify(redactProtocolValue(value));
  appendLine(protocolLines, elements.protocolLog, `${direction}  ${body}`, MAX_PROTOCOL_LINES);
}

function redactProtocolValue(value) {
  if (Array.isArray(value)) return value.map(redactProtocolValue);
  if (!value || typeof value !== "object") return value;
  const result = {};
  for (const [key, item] of Object.entries(value)) {
    if (key === "bytesBase64") {
      result[key] = `[REDACTED raw payload; byteLength=${value.byteLength ?? "unknown"}]`;
    } else {
      result[key] = redactProtocolValue(item);
    }
  }
  return result;
}

function bytesFromBase64(value) {
  const binary = atob(value);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function hex(bytes) {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0").toUpperCase()).join(" ");
}

function formatBytes(bytes) {
  if (displayFormat === "hex") return hex(bytes);
  return Array.from(bytes, (value) => String(value)).join(" ");
}

function unsigned24(bytes, offset) {
  return (bytes[offset] * 0x10000) + (bytes[offset + 1] * 0x100) + bytes[offset + 2];
}

function observeSequence(sequence) {
  if (stats.lastSequence === null) {
    stats.receivedUnique += 1;
    stats.lastSequence = sequence;
  } else {
    const distance = (sequence - stats.lastSequence + 0x10000) % 0x10000;
    if (distance === 0) {
      appendDataRecord({ type: "note", text: `EEG 重复序列号=${sequence}，不计入丢包率` });
    } else if (distance <= 0x8000) {
      stats.receivedUnique += 1;
      if (distance > 1) {
        stats.lost += distance - 1;
        appendDataRecord({ type: "note", text: `EEG 序列跳变 ${stats.lastSequence} → ${sequence}，推算丢失 ${distance - 1} 包` });
      }
      stats.lastSequence = sequence;
    } else {
      appendDataRecord({ type: "note", text: `EEG 乱序或回退 ${stats.lastSequence} → ${sequence}，不计入丢包率` });
    }
  }
}

function parseEegPacket(packet, windowStartMs, windowEndMs) {
  if (packet.length !== 20) {
    appendDataRecord({ type: "note", text: `EEG 包长异常：收到 ${packet.length} 字节，期望 20 字节` });
    return;
  }
  const sequence = (packet[0] << 8) | packet[1];
  const values = Array.from({ length: 6 }, (_, index) => unsigned24(packet, 2 + (index * 3)));
  observeSequence(sequence);
  appendDataRecord({ type: "eeg", bytes: packet, sequence, values, windowStartMs, windowEndMs });
}

function printRawStream(name, raw) {
  if (!raw || raw.encoding !== "base64" || typeof raw.bytesBase64 !== "string") return;
  try {
    const bytes = bytesFromBase64(raw.bytesBase64);
    const packetBytes = Number(raw.packetBytes);
    const packetCount = Number(raw.packetCount);
    if (name === "EEG") {
      stats.eegPackets += packetCount;
      stats.eegBytes += bytes.length;
      if (packetBytes > 0) {
        for (let offset = 0; offset + packetBytes <= bytes.length; offset += packetBytes) {
          parseEegPacket(bytes.slice(offset, offset + packetBytes), raw.windowStartMs, raw.windowEndMs);
        }
        if (bytes.length % packetBytes) appendDataRecord({ type: "note", text: `EEG 窗口存在 ${bytes.length % packetBytes} 个无法组成完整包的尾部字节` });
      }
    } else {
      stats.hrPackets += packetCount;
      stats.hrBytes += bytes.length;
      for (const value of bytes) {
        appendDataRecord({ type: "hr", bytes: Uint8Array.of(value), value, windowStartMs: raw.windowStartMs, windowEndMs: raw.windowEndMs });
      }
    }
  } catch (error) {
    appendDataRecord({ type: "note", text: `${name} Base64 解码失败：${error.message}` });
  }
}

function rawRecordText(record) {
  if (record.type === "note") return `提示 | ${record.text}`;
  const window = `窗口[${record.windowStartMs}-${record.windowEndMs}]`;
  if (record.type === "hr") return `${window} | HR [${formatBytes(record.bytes)}]`;
  const points = [
    `SEQ [${formatBytes(record.bytes.slice(0, 2))}]`,
    ...Array.from({ length: 6 }, (_, index) => `EEG${index + 1} [${formatBytes(record.bytes.slice(2 + (index * 3), 5 + (index * 3)))}]`),
  ];
  return `${window} | ${points.join(" | ")}`;
}

function decodedRecordText(record) {
  if (record.type === "note") return record.text;
  if (record.type === "hr") {
    return displayFormat === "hex" ? `HR=0x${record.value.toString(16).padStart(2, "0").toUpperCase()}` : `HR=${record.value}`;
  }
  if (displayFormat === "hex") {
    const values = record.values.map((value, index) => `EEG${index + 1}=0x${value.toString(16).padStart(6, "0").toUpperCase()}`);
    return `SEQ=0x${record.sequence.toString(16).padStart(4, "0").toUpperCase()} | ${values.join(" | ")}`;
  }
  return `SEQ=${record.sequence} | ${record.values.map((value, index) => `EEG${index + 1}=${value}`).join(" | ")}`;
}

function renderData() {
  if (!dataRecords.length) {
    elements.rawData.textContent = "等待网关数据……";
    elements.decodedData.textContent = "等待网关数据……";
    return;
  }
  elements.rawData.textContent = dataRecords.map((record) => `${record.time}  ${rawRecordText(record)}`).join("\n");
  elements.decodedData.textContent = dataRecords.map((record) => `${record.time}  ${decodedRecordText(record)}`).join("\n");
  elements.rawData.scrollTop = elements.rawData.scrollHeight;
  elements.decodedData.scrollTop = elements.decodedData.scrollHeight;
}

function setDisplayFormat(format) {
  displayFormat = format;
  const hexadecimal = format === "hex";
  elements.hexFormat.classList.toggle("active", hexadecimal);
  elements.decimalFormat.classList.toggle("active", !hexadecimal);
  elements.hexFormat.setAttribute("aria-pressed", String(hexadecimal));
  elements.decimalFormat.setAttribute("aria-pressed", String(!hexadecimal));
  renderData();
}

function updateMetrics() {
  elements.eventCount.textContent = String(stats.events);
  elements.eegCount.textContent = `${stats.eegPackets} / ${stats.eegBytes}`;
  elements.hrCount.textContent = `${stats.hrPackets} / ${stats.hrBytes}`;
  elements.sequence.textContent = stats.lastSequence === null ? "—" : String(stats.lastSequence);
  elements.lostPackets.textContent = String(stats.lost);
  const expected = stats.receivedUnique + stats.lost;
  elements.lossRate.textContent = `${(expected ? (stats.lost / expected) * 100 : 0).toFixed(6)}%`;
}

function updateStatus(data) {
  const result = data?.result && typeof data.result === "object" ? data.result : data;
  if (!result || typeof result !== "object") return;
  if (result.mode !== undefined) elements.mode.textContent = result.mode;
  const status = result.status || result.payload?.status || result;
  if (status.connectionState !== undefined) {
    const nextState = String(status.connectionState);
    const changed = nextState !== deviceConnectionState;
    deviceConnectionState = nextState;
    elements.deviceState.textContent = `${DEVICE_STATE_LABELS[nextState] || nextState} · ${nextState}`;
    if (changed) {
      appendDataRecord({ type: "note", text: `设备状态：${DEVICE_STATE_LABELS[nextState] || nextState}（${nextState}）` });
      if (DEVICE_STATE_MESSAGES[nextState]) elements.gatewayMessage.textContent = DEVICE_STATE_MESSAGES[nextState];
    }
  }
  if (status.algorithmState !== undefined) elements.algorithmState.textContent = String(status.algorithmState);
}

function handleMessage(message) {
  appendProtocol("←", message);
  if (message.code !== 200) {
    setState("error", "请求失败", `${message.message || "网关返回错误"}（code=${message.code ?? "未知"}）`);
    return;
  }
  const data = message.data || {};
  updateStatus(data);
  if (data.action === "subscribe" && data.result?.subscriptionId) {
    subscriptionId = data.result.subscriptionId;
    setState("ok", "接收中", "订阅成功，正在等待网关转发耳机数据。即使数据暂未到达，网关服务仍保持运行。");
    refreshControls();
  } else if (data.action === "unsubscribe") {
    subscriptionId = null;
    setState("ok", "已连接", "已停止向当前网页推送数据；网关后台和耳机连接未停止。再次点击“开始接收”即可恢复。");
    refreshControls();
  }
  if (data.event === "data" || data.event === "status") {
    stats.events += 1;
    const payload = data.payload || {};
    printRawStream("EEG", payload.eegRaw);
    printRawStream("HR", payload.hrRaw);
    renderData();
    updateMetrics();
  }
}

function sendRequest(action, params) {
  if (!isConnected()) {
    setState("error", "未连接", "请先连接网关。网页不会直接访问 USB 串口。只要网关服务未运行，网页就无法接收数据。");
    return false;
  }
  const payload = { protocolVersion: PROTOCOL_VERSION, messageType: "request", requestId: requestId(), action, params };
  socket.send(JSON.stringify(payload));
  appendProtocol("→", payload);
  return true;
}

function connect() {
  const endpoint = elements.endpoint.value.trim();
  if (!endpoint.startsWith("ws://") && !endpoint.startsWith("wss://")) {
    setState("error", "地址错误", "WebSocket 地址必须以 ws:// 或 wss:// 开头。");
    return;
  }
  try {
    setState("warn", "正在连接", `正在连接当前网关：${endpoint}`);
    socket = new WebSocket(endpoint, SUBPROTOCOL);
    refreshControls();
  } catch (error) {
    socket = null;
    setState("error", "连接失败", error.message);
    refreshControls();
    return;
  }
  socket.addEventListener("open", () => {
    setState("ok", "已连接", "网关已连接。正在自动查询状态；点击“开始接收”后打印耳机数据。");
    refreshControls();
    sendRequest("getStatus", {});
  });
  socket.addEventListener("message", (event) => {
    try {
      handleMessage(JSON.parse(event.data));
    } catch (error) {
      appendProtocol("ERROR", `无法解析网关消息：${error.message}；接收字符数=${String(event.data).length}；原文未记录`);
    }
  });
  socket.addEventListener("error", () => {
    setState("error", "连接异常", "无法连接网关。请确认 NeuroBridge 正在运行，并通过网关提供的 http://127.0.0.1:8080/capture/ 打开本页。");
  });
  socket.addEventListener("close", (event) => {
    appendProtocol("SYSTEM", `WebSocket 已断开 code=${event.code}${event.reason ? ` reason=${event.reason}` : ""}`);
    socket = null;
    subscriptionId = null;
    setState("idle", "已断开", "网页与网关的连接已断开；网关进程和 USB 串口不会因此停止。");
    refreshControls();
  });
}

function clearDisplay() {
  dataRecords = [];
  protocolLines = [];
  stats = createStats();
  elements.rawData.textContent = "等待网关数据……";
  elements.decodedData.textContent = "等待网关数据……";
  elements.protocolLog.textContent = isConnected() ? "显示已清空，网关仍保持连接。" : "等待连接网关……";
  updateMetrics();
}

function exportDiagnosticLog() {
  const generatedAt = new Date();
  const notes = dataRecords.filter((record) => record.type === "note");
  const lines = [
    "# NeuroBridge Capture Diagnostic Log",
    `generatedAtUtc=${generatedAt.toISOString()}`,
    `endpoint=${elements.endpoint.value.trim()}`,
    `gatewayState=${elements.gatewayState.textContent}`,
    `gatewayMessage=${elements.gatewayMessage.textContent}`,
    `mode=${elements.mode.textContent}`,
    `deviceConnectionState=${deviceConnectionState ?? "unknown"}`,
    `deviceConnectionStateLabel=${elements.deviceState.textContent}`,
    `algorithmState=${elements.algorithmState.textContent}`,
    `subscriptionState=${elements.subscriptionState.textContent}`,
    `displayFormat=${displayFormat}`,
    `dataEvents=${stats.events}`,
    `eegPackets=${stats.eegPackets}`,
    `eegBytes=${stats.eegBytes}`,
    `hrPackets=${stats.hrPackets}`,
    `hrBytes=${stats.hrBytes}`,
    `latestSequence=${stats.lastSequence ?? "none"}`,
    `estimatedLostPackets=${stats.lost}`,
    `receivedUniquePackets=${stats.receivedUnique}`,
    "rawPayloadExported=false",
    "",
    "[SEQUENCE_AND_PARSE_NOTES]",
    ...(notes.length ? notes.map((record) => `${record.time}  ${record.text}`) : ["none"]),
    "",
    "[REDACTED_WEBSOCKET_PROTOCOL_LOG]",
    ...(protocolLines.length ? protocolLines : ["none"]),
    "",
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `neurobridge-capture-diagnostics-${generatedAt.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z")}.log`;
  const filename = link.download;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
  elements.gatewayMessage.textContent = `诊断日志已导出：${filename}；完整 EEG/心率原始数据未写入文件。`;
}

elements.connect.addEventListener("click", connect);
elements.disconnect.addEventListener("click", () => socket?.close(1000, "capture page disconnect"));
elements.status.addEventListener("click", () => sendRequest("getStatus", {}));
elements.start.addEventListener("click", () => sendRequest("subscribe", { streams: ["eeg.raw", "hr.raw", "status"], includeInvalid: true }));
elements.stop.addEventListener("click", () => subscriptionId && sendRequest("unsubscribe", { subscriptionId }));
elements.clear.addEventListener("click", clearDisplay);
elements.export.addEventListener("click", exportDiagnosticLog);
elements.hexFormat.addEventListener("click", () => setDisplayFormat("hex"));
elements.decimalFormat.addEventListener("click", () => setDisplayFormat("decimal"));

refreshControls();
updateMetrics();
