"use strict";

const PROTOCOL_VERSION = window.NEUROBRIDGE_VERSION.protocolVersion;
const SUBPROTOCOL = "neurobridge.v1";
const MAX_DATA_LINES = 800;
const MAX_PROTOCOL_LINES = 300;
const SEQUENCE_MODULUS = 0x10000;
const SEQUENCE_HALF_RANGE = 0x8000;
const RECENT_SEQUENCE_WINDOW = 4096;

const elements = {
  endpoint: document.querySelector("#endpoint"),
  connect: document.querySelector("#connectButton"),
  disconnect: document.querySelector("#disconnectButton"),
  status: document.querySelector("#statusButton"),
  start: document.querySelector("#startButton"),
  stop: document.querySelector("#stopButton"),
  clear: document.querySelector("#clearButton"),
  export: document.querySelector("#exportButton"),
  exportRaw: document.querySelector("#exportRawButton"),
  gatewayState: document.querySelector("#gatewayState"),
  gatewayMessage: document.querySelector("#gatewayMessage"),
  mode: document.querySelector("#modeValue"),
  deviceState: document.querySelector("#deviceState"),
  sourceNotice: document.querySelector("#sourceNotice"),
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
let rawRecords = [];
let protocolLines = [];
let stats = createStats();
let displayFormat = "hex";
let deviceConnectionState = null;
let dataMode = null;

const DEVICE_STATE_LABELS = {
  connecting: "连接中",
  connected: "已连接",
  disconnected: "已断开",
};

const DEVICE_STATE_MESSAGES = {
  connecting: "网关正在发现、打开并验证设备候选。",
  connected: "已有合法数据流或 ACK 返回独立 01，设备已验证。算法初始化和 E1 发生在设备验证之后；已有数据流时不会发送 E1。算法未就绪或 E1 写入失败会在网关日志中报告，但不属于设备验证失败。",
  disconnected: "当前没有可用的实时设备路径；可能尚未发现设备、设备验证未通过或连接已断开。数据模式为 replay 时，页面仍会显示历史录播数据。",
};

const DATA_MODE_LABELS = {
  live: "实时 · live",
  replay: "历史录播 · replay",
};

function renderSourceState() {
  elements.mode.textContent = dataMode === null ? "—" : (DATA_MODE_LABELS[dataMode] || dataMode);
  if (deviceConnectionState === null) {
    elements.deviceState.textContent = "—";
  } else if (deviceConnectionState === "disconnected" && dataMode === "replay") {
    elements.deviceState.textContent = "实时耳机未连接 · 当前数据为录播";
  } else {
    elements.deviceState.textContent = `${DEVICE_STATE_LABELS[deviceConnectionState] || deviceConnectionState} · ${deviceConnectionState}`;
  }

  elements.sourceNotice.dataset.mode = dataMode || "unknown";
  if (dataMode === "live") {
    elements.sourceNotice.textContent = "当前数据来源：实时耳机。耳机连接状态与正在显示的数据来自同一条 USB 串口实时链路。";
  } else if (dataMode === "replay") {
    elements.sourceNotice.textContent = "当前数据来源：历史录播，不是当前耳机实时流。实时耳机未连接时，网关会按配置自动读取已保存数据并继续发送。";
  } else {
    elements.sourceNotice.textContent = "正在等待网关报告数据来源。耳机连接状态只表示当前实时串口路径，录播数据可能在耳机未连接时继续发送。";
  }
}

if (typeof window.NEUROBRIDGE_B_CLIENT_ENDPOINT === "string") {
  elements.endpoint.value = window.NEUROBRIDGE_B_CLIENT_ENDPOINT;
}

function createStats() {
  return {
    events: 0,
    eegPackets: 0,
    eegBytes: 0,
    hrPackets: 0,
    hrBytes: 0,
    lost: 0,
    receivedUnique: 0,
    lastSequence: null,
    baseExtendedSequence: null,
    highestExtendedSequence: null,
    recentSequenceOrder: [],
    recentSequences: new Set(),
    duplicates: 0,
    outOfOrder: 0,
    late: 0,
  };
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

function timestampLabel(value) {
  const timestamp = Number(value);
  let date = Number.isFinite(timestamp) ? new Date(timestamp) : new Date();
  if (Number.isNaN(date.getTime())) date = new Date();
  const calendar = [date.getFullYear(), date.getMonth() + 1, date.getDate()]
    .map((part, index) => String(part).padStart(index === 0 ? 4 : 2, "0"))
    .join("-");
  const clock = [date.getHours(), date.getMinutes(), date.getSeconds()]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
  return `${calendar} ${clock}.${String(date.getMilliseconds()).padStart(3, "0")}`;
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
  elements.exportRaw.disabled = rawRecords.length === 0;
  elements.subscriptionState.textContent = subscriptionId ? `接收中 · ${subscriptionId}` : "未订阅";
}

function appendLine(lines, target, line, maximum) {
  lines.push(`${timeLabel()}  ${line}`);
  if (lines.length > maximum) lines.splice(0, lines.length - maximum);
  target.textContent = lines.join("\n");
  target.scrollTop = target.scrollHeight;
}

function appendDataRecord(record) {
  const timestampMs = Number.isFinite(Number(record.timestampMs)) ? Number(record.timestampMs) : Date.now();
  dataRecords.push({ ...record, timestampMs });
  if (dataRecords.length > MAX_DATA_LINES) dataRecords.splice(0, dataRecords.length - MAX_DATA_LINES);
}

function appendRawRecord(bytes, timestampMs) {
  rawRecords.push({ bytes: Uint8Array.from(bytes), timestampMs: Number.isFinite(Number(timestampMs)) ? Number(timestampMs) : Date.now() });
  if (rawRecords.length > MAX_DATA_LINES) rawRecords.splice(0, rawRecords.length - MAX_DATA_LINES);
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

function rememberExtendedSequence(extended) {
  stats.recentSequences.add(extended);
  stats.recentSequenceOrder.push(extended);
  while (stats.recentSequenceOrder.length > RECENT_SEQUENCE_WINDOW) {
    stats.recentSequences.delete(stats.recentSequenceOrder.shift());
  }
}

function updateEstimatedLoss() {
  if (stats.baseExtendedSequence === null || stats.highestExtendedSequence === null) {
    stats.lost = 0;
    return;
  }
  const expected = stats.highestExtendedSequence - stats.baseExtendedSequence + 1;
  stats.lost = Math.max(0, expected - stats.receivedUnique);
}

function observeSequence(sequence) {
  if (stats.highestExtendedSequence === null) {
    stats.baseExtendedSequence = sequence;
    stats.highestExtendedSequence = sequence;
    stats.receivedUnique = 1;
    stats.lastSequence = sequence;
    rememberExtendedSequence(sequence);
    updateEstimatedLoss();
    return;
  }

  const cycle = stats.highestExtendedSequence - (stats.highestExtendedSequence % SEQUENCE_MODULUS);
  let extended = cycle + sequence;
  const delta = extended - stats.highestExtendedSequence;
  if (delta < -SEQUENCE_HALF_RANGE) extended += SEQUENCE_MODULUS;
  else if (delta > SEQUENCE_HALF_RANGE) extended -= SEQUENCE_MODULUS;

  const previousHighest = stats.highestExtendedSequence;
  if (extended > previousHighest) {
    const gap = extended - previousHighest - 1;
    stats.highestExtendedSequence = extended;
    stats.lastSequence = sequence;
    stats.receivedUnique += 1;
    rememberExtendedSequence(extended);
    if (gap > 0) {
      appendDataRecord({ type: "note", text: `EEG 序列跳变 ${(previousHighest % SEQUENCE_MODULUS)} → ${sequence}，暂缺 ${gap} 包` });
    }
  } else if (stats.recentSequences.has(extended)) {
    stats.duplicates += 1;
    appendDataRecord({ type: "note", text: `EEG 重复序列号=${sequence}，不增加已收包数` });
  } else if (extended >= Math.max(stats.baseExtendedSequence, previousHighest - RECENT_SEQUENCE_WINDOW + 1)) {
    stats.outOfOrder += 1;
    stats.receivedUnique += 1;
    rememberExtendedSequence(extended);
    appendDataRecord({ type: "note", text: `EEG 乱序补到序列号=${sequence}，将重新计算累计丢包` });
  } else {
    stats.late += 1;
    appendDataRecord({ type: "note", text: `EEG 过晚序列号=${sequence}，超出 ${RECENT_SEQUENCE_WINDOW} 包统计窗口` });
  }
  updateEstimatedLoss();
}

function parseEegPacket(packet, windowStartMs, windowEndMs, packetIndex) {
  if (packet.length !== 20) {
    appendDataRecord({ type: "note", text: `EEG 包长异常：收到 ${packet.length} 字节，期望 20 字节` });
    return null;
  }
  const sequence = (packet[0] << 8) | packet[1];
  const values = Array.from({ length: 6 }, (_, index) => unsigned24(packet, 2 + (index * 3)));
  observeSequence(sequence);
  return { type: "eeg", bytes: packet, sequence, values, windowStartMs, windowEndMs, packetIndex, timestampMs: windowEndMs };
}

function decodeRawPackets(name, raw, expectedPacketBytes) {
  if (!raw) return null;
  if (raw.encoding !== "base64" || typeof raw.bytesBase64 !== "string") {
    appendDataRecord({ type: "note", text: `${name} 原始数据格式异常：期望 base64` });
    return null;
  }
  try {
    const bytes = bytesFromBase64(raw.bytesBase64);
    const packetBytes = Number(raw.packetBytes);
    const packetCount = Number(raw.packetCount);
    if (!Number.isInteger(packetBytes) || packetBytes <= 0) {
      appendDataRecord({ type: "note", text: `${name} packetBytes 异常：${raw.packetBytes}` });
      return null;
    }
    if (packetBytes !== expectedPacketBytes) {
      appendDataRecord({ type: "note", text: `${name} 单包长度异常：收到 ${packetBytes} 字节，期望 ${expectedPacketBytes} 字节` });
    }
    const packets = [];
    for (let offset = 0; offset + packetBytes <= bytes.length; offset += packetBytes) {
      packets.push(bytes.slice(offset, offset + packetBytes));
    }
    const trailingBytes = bytes.length % packetBytes;
    const trailing = trailingBytes ? bytes.slice(bytes.length - trailingBytes) : null;
    if (trailingBytes) appendDataRecord({ type: "note", text: `${name} 窗口存在 ${trailingBytes} 个无法组成完整包的尾部字节` });
    if (Number.isInteger(packetCount) && packetCount !== packets.length) {
      appendDataRecord({ type: "note", text: `${name} 包数不一致：声明 ${packetCount} 包，实际解析 ${packets.length} 包` });
    }
    return { packets, trailing, bytes, packetCount, windowStartMs: raw.windowStartMs, windowEndMs: raw.windowEndMs };
  } catch (error) {
    appendDataRecord({ type: "note", text: `${name} Base64 解码失败：${error.message}` });
    return null;
  }
}

function printRawPayload(payload) {
  const eeg = decodeRawPackets("EEG", payload.eegRaw, 20);
  const hr = decodeRawPackets("HR", payload.hrRaw, 1);
  if (eeg) {
    stats.eegPackets += Number.isInteger(eeg.packetCount) ? eeg.packetCount : eeg.packets.length;
    stats.eegBytes += eeg.bytes.length;
  }
  if (hr) {
    stats.hrPackets += Number.isInteger(hr.packetCount) ? hr.packetCount : hr.packets.length;
    stats.hrBytes += hr.bytes.length;
  }
  if (!eeg && !hr) return;

  const eegCount = eeg?.packets.length ?? 0;
  const hrCount = hr?.packets.length ?? 0;
  if (eeg && hr && eegCount !== hrCount) {
    appendDataRecord({ type: "note", text: `同一窗口 EEG/HR 包数不一致：EEG=${eegCount}，HR=${hrCount}；未配对数据仍会单独显示` });
  }

  const recordCount = Math.max(eegCount, hrCount);
  for (let index = 0; index < recordCount; index += 1) {
    const packetIndex = index + 1;
    const eegPacket = eeg?.packets[index];
    const hrPacket = hr?.packets[index];
    if (eegPacket) appendRawRecord(eegPacket, eeg.windowEndMs);
    if (hrPacket) appendRawRecord(hrPacket, hr.windowEndMs);
    const eegRecord = eegPacket ? parseEegPacket(eegPacket, eeg.windowStartMs, eeg.windowEndMs, packetIndex) : null;
    if (eegRecord && hrPacket) {
      appendDataRecord({ ...eegRecord, type: "frame", hrBytes: hrPacket, hrValue: hrPacket[0] });
    } else if (eegRecord) {
      appendDataRecord(eegRecord);
    } else if (hrPacket) {
      appendDataRecord({
        type: "hr",
        bytes: hrPacket,
        value: hrPacket[0],
        windowStartMs: hr.windowStartMs,
        windowEndMs: hr.windowEndMs,
        timestampMs: hr.windowEndMs,
        packetIndex,
      });
    }
  }
  if (eeg?.trailing) appendRawRecord(eeg.trailing, eeg.windowEndMs);
  if (hr?.trailing) appendRawRecord(hr.trailing, hr.windowEndMs);
}

function rawRecordText(record) {
  return `${timestampLabel(record.timestampMs)}  ${formatBytes(record.bytes)}`;
}

function decodedRecordText(record) {
  const timestamp = timestampLabel(record.timestampMs);
  if (record.type === "note") return `${timestamp}  ${record.text}`;
  if (record.type === "hr") {
    return displayFormat === "hex"
      ? `${timestamp}  数据帧#${record.packetIndex ?? "?"} | EEG=缺失 | HR=0x${record.value.toString(16).padStart(2, "0").toUpperCase()}`
      : `${timestamp}  数据帧#${record.packetIndex ?? "?"} | EEG=缺失 | HR=${record.value}`;
  }
  if (displayFormat === "hex") {
    const values = record.values.map((value, index) => `EEG${index + 1}=0x${value.toString(16).padStart(6, "0").toUpperCase()}`);
    const hr = record.type === "frame" ? `0x${record.hrValue.toString(16).padStart(2, "0").toUpperCase()}` : "缺失";
    return `${timestamp}  数据帧#${record.packetIndex ?? "?"} | SEQ=0x${record.sequence.toString(16).padStart(4, "0").toUpperCase()} | ${values.join(" | ")} | HR=${hr}`;
  }
  const hr = record.type === "frame" ? record.hrValue : "缺失";
  return `${timestamp}  数据帧#${record.packetIndex ?? "?"} | SEQ=${record.sequence} | ${record.values.map((value, index) => `EEG${index + 1}=${value}`).join(" | ")} | HR=${hr}`;
}

function renderData() {
  elements.rawData.textContent = rawRecords.length ? rawRecords.map(rawRecordText).join("\n") : "等待网关数据……";
  elements.decodedData.textContent = dataRecords.length ? dataRecords.map(decodedRecordText).join("\n") : "等待网关数据……";
  elements.exportRaw.disabled = rawRecords.length === 0;
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
  if (result.mode !== undefined) dataMode = String(result.mode);
  const status = result.status || result.payload?.status || result;
  if (status.connectionState !== undefined) {
    const nextState = String(status.connectionState);
    const changed = nextState !== deviceConnectionState;
    deviceConnectionState = nextState;
    if (changed) {
      appendDataRecord({ type: "note", text: `设备状态：${DEVICE_STATE_LABELS[nextState] || nextState}（${nextState}）` });
      if (DEVICE_STATE_MESSAGES[nextState]) elements.gatewayMessage.textContent = DEVICE_STATE_MESSAGES[nextState];
    }
  }
  renderSourceState();
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
    printRawPayload(payload);
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
  rawRecords = [];
  protocolLines = [];
  stats = createStats();
  elements.rawData.textContent = "等待网关数据……";
  elements.decodedData.textContent = "等待网关数据……";
  elements.protocolLog.textContent = isConnected() ? "显示已清空，网关仍保持连接。" : "等待连接网关……";
  updateMetrics();
  refreshControls();
}

function exportRawData() {
  if (!rawRecords.length) {
    elements.gatewayMessage.textContent = "暂无可导出的原始数据。请先开始接收并等待耳机数据到达。";
    return;
  }
  const generatedAt = new Date();
  const blob = new Blob([`${rawRecords.map(rawRecordText).join("\n")}\n`], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `neurobridge-raw-data-${generatedAt.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z")}.txt`;
  const filename = link.download;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
  elements.gatewayMessage.textContent = `原始数据已导出：${filename}；共 ${rawRecords.length} 行，格式为毫秒时间戳加原始字节。`;
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
    `dataMode=${dataMode ?? "unknown"}`,
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
    `duplicatePackets=${stats.duplicates}`,
    `outOfOrderPackets=${stats.outOfOrder}`,
    `latePackets=${stats.late}`,
    "rawPayloadExported=false",
    "",
    "[SEQUENCE_AND_PARSE_NOTES]",
    ...(notes.length ? notes.map((record) => `${timestampLabel(record.timestampMs)}  ${record.text}`) : ["none"]),
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
elements.exportRaw.addEventListener("click", exportRawData);
elements.hexFormat.addEventListener("click", () => setDisplayFormat("hex"));
elements.decimalFormat.addEventListener("click", () => setDisplayFormat("decimal"));

refreshControls();
updateMetrics();
renderSourceState();
if (typeof window.NEUROBRIDGE_B_CLIENT_ENDPOINT === "string") {
  connect();
}
