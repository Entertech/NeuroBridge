"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "web/ubuntu/b-client-test/index.html"), "utf8");
const defaultEndpoint = html.match(/id="endpoint"[^>]*value="([^"]+)"/)[1];

class Element {
  constructor() { this.value = ""; this.children = []; this.listeners = {}; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  replaceChildren() { this.children = []; }
  add(option) { this.append(option); }
  remove() { this.parent.children.splice(this.parent.children.indexOf(this), 1); }
  getContext() { return new Proxy({}, { get: (target, key) => target[key] || (() => {}), set: (target, key, value) => { target[key] = value; return true; } }); }
  get firstElementChild() { return this.children[0]; }
}

function page(injected, search = '') {
  const nodes = new Map();
  function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, new Element());
    return nodes.get(selector);
  }
  node("#endpoint").value = defaultEndpoint;
  const sockets = [];
  class Socket {
    static CONNECTING = 0;
    static OPEN = 1;
    constructor(url, protocol) {
      this.url = url; this.protocol = protocol; this.readyState = Socket.CONNECTING;
      this.listeners = {}; this.sent = []; sockets.push(this);
    }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    emit(name, event = {}) { this.listeners[name](event); }
    send(value) { if (this.failSend) throw new Error("connection reset"); this.sent.push(JSON.parse(value)); }
    close() { this.readyState = 3; this.emit("close", { code: 1000, wasClean: true, reason: "" }); }
    open() { this.readyState = Socket.OPEN; this.emit("open"); }
  }
  const context = {
    window: { NEUROBRIDGE_VERSION: { protocolVersion: "1.0" }, location: { protocol: "file:", search }, addEventListener: () => {}, crypto: { randomUUID: () => "request-id" } },
    document: { querySelector: node, querySelectorAll: () => [], createElement: () => new Element() },
    Option: function(text, value) { this.text = text; this.value = value; },
    navigator: { clipboard: { writeText: async () => {} } },
    WebSocket: Socket, URLSearchParams, console,
  };
  if (injected) context.window.NEUROBRIDGE_B_CLIENT_ENDPOINT = injected;
  vm.runInNewContext(fs.readFileSync(path.join(root, "web/ubuntu/b-client-test/app.js"), "utf8"), context);
  return { nodes, sockets, node };
}

const client = page();
assert.equal(client.sockets.length, 1);
assert.equal(client.sockets[0].url, "ws://192.168.88.10:8765/neurobridge/v1/ws");
assert.equal(client.sockets[0].protocol, "neurobridge.v1");
const first = client.sockets[0];
first.open();
assert.equal(first.sent.length, 1);
assert.equal(first.sent[0].action, "getStatus");
assert.equal(first.sent[0].protocolVersion, "1.0");
assert.equal(first.sent[0].messageType, "request");
first.emit("message", { data: JSON.stringify({ code: 200, data: { payload: { eegRaw: { byteLength: 20, bytesBase64: "PRIVATE_RAW" }, algorithm: { eeg: { wave: "PRIVATE_WAVE" } } } } }) });
function logText() { return client.node("#log").children.map(row => row.children[2].textContent).join("\n"); }
assert.ok(!logText().includes("PRIVATE_RAW"));
assert.ok(!logText().includes("PRIVATE_WAVE"));
function incoming(data, code = 200) { first.emit("message", { data: JSON.stringify({ code, data }) }); }
function text(id) { return client.node(`#${id}`).textContent; }
incoming({ action: "getStatus", result: { gatewayBootId: "boot-a", mode: "live", connectionState: "connected", wearState: "worn", batteryPercent: 0, subjectId: "synthetic", serverTimeMs: 1000 } });
assert.equal(text("batteryValue"), "0%");
assert.equal(text("wearValue"), "已佩戴，接触良好");
assert.equal(text("deviceConnectionValue"), "已连接");
const wave = { left: Array.from({length: 1300}, (_, i) => i), right: [1, 2, null, 4], single: null };
incoming({ event: "data", mode: "live", timestampMs: 2000, valid: true, payload: { algorithm: { relaxation: 0, attention: 35, flow: { meditation: 12 }, eeg: { wave, bandPower: { delta: 0, theta: 2, alpha: 3, beta: 4, gamma: 5, lowBeta: null } } } } });
assert.equal(text("relaxationValue"), "0");
assert.equal(text("attentionValue"), "35");
assert.equal(text("flowValue"), "12");
assert.ok(text("waveformMeta").includes("左通道 1200 点 / 右通道 4 点"));
assert.equal(text("deltaBandValue"), "0");
assert.equal(text("lowBetaBandValue"), "未提供");
assert.equal(text("highBetaBandValue"), "未提供");
const eegTime = text("attentionMeta");
incoming({ event: "data", mode: "live", timestampMs: 1900, valid: true, payload: { algorithm: { pressure: 3, hr: { value: 80, hrv: 22.5 } } } });
assert.equal(text("pressureValue"), "3");
assert.equal(text("heartRateValue"), "80 bpm");
assert.equal(text("hrvValue"), "22.5");
assert.equal(text("attentionMeta"), eegTime);
incoming({ event: "status", mode: "live", timestampMs: 3000, payload: { status: { wearState: "notWorn", batteryPercent: 82 } } });
assert.equal(text("wearValue"), "未佩戴或接触不良");
assert.equal(text("batteryValue"), "82%");
assert.equal(text("attentionMeta"), eegTime);
incoming({ event: "data", timestampMs: 1000, payload: { algorithm: { attention: 1 } } });
assert.equal(text("attentionValue"), "35"); // Late windows do not replace newer readings.
incoming({ payload: { status: { batteryPercent: 99 }, algorithm: { attention: 99 } } }, 500);
assert.equal(text("batteryValue"), "82%");
assert.equal(text("attentionValue"), "35");
incoming({ timestampMs: 3100, valid: false, payload: { algorithm: { pressure: 9, hr: {value: 90, hrv: 9} } } });
assert.equal(text("pressureValue"), "—");
assert.equal(text("heartRateValue"), "—");
assert.equal(text("hrvValue"), "—");
incoming({ mode: "replay", timestampMs: 500, payload: { algorithm: { attention: 8 } } });
assert.equal(text("attentionValue"), "8");
assert.equal(text("flowValue"), "—");
incoming({ event: "replayEnded", mode: "replay" });
incoming({ mode: "replay", timestampMs: 100, payload: { algorithm: { attention: 4 } } });
assert.equal(text("attentionValue"), "4");
incoming({ gatewayBootId: "boot-b", mode: "replay" });
assert.equal(text("attentionValue"), "—");
incoming({ mode: "live", payload: { algorithm: { attention: 3 } } });
incoming({ mode: "live", payload: { status: { connectionState: "disconnected", wearState: "unknown", batteryPercent: null } } });
assert.equal(text("attentionValue"), "—");
assert.equal(text("batteryValue"), "未知");
assert.equal(text("wearValue"), "未知");
first.failSend = true;
client.node("#getStatusButton").listeners.click();
assert.ok(logText().includes("connection reset"));
first.close();
assert.equal(text("batteryValue"), "—");
assert.equal(text("heartRateValue"), "—");
client.node("#connectButton").listeners.click();
const second = client.sockets[1];
first.emit("close", { code: 1006, wasClean: false, reason: "late event" });
second.open();
assert.equal(second.sent[0].action, "getStatus");
assert.equal(client.node("#connectionLabel").textContent, "已连接");
for (let i = 0; i < 550; i++) second.emit("message", { data: '{"code":200,"data":{}}' });
assert.equal(client.node("#log").children.length, 500);
assert.equal(second.sent.length, 1); // No automatic stream subscription or heartbeat.
const hosted = page("ws://127.0.0.1:9999/neurobridge/v1/ws");
assert.equal(hosted.sockets[0].url, "ws://127.0.0.1:9999/neurobridge/v1/ws");
const linked = page(null, '?endpoint=ws%3A%2F%2F127.0.0.1%3A9999%2Fneurobridge%2Fv1%2Fws');
assert.equal(linked.sockets[0].url, "ws://127.0.0.1:9999/neurobridge/v1/ws");
console.log("B-side page: static default, autoconnect/status, reconnect, safe bounded logs passed");
