"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "web/b-client-test/index.html"), "utf8");
const defaultEndpoint = html.match(/id="endpoint"[^>]*value="([^"]+)"/)[1];

class Element {
  constructor() { this.value = ""; this.children = []; this.listeners = {}; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  replaceChildren() { this.children = []; }
  add(option) { this.append(option); }
  remove() { this.parent.children.splice(this.parent.children.indexOf(this), 1); }
  get firstElementChild() { return this.children[0]; }
}

function page(injected) {
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
    window: { NEUROBRIDGE_VERSION: { protocolVersion: "1.0" }, location: { protocol: "file:" }, crypto: { randomUUID: () => "request-id" } },
    document: { querySelector: node, querySelectorAll: () => [], createElement: () => new Element() },
    Option: function(text, value) { this.text = text; this.value = value; },
    navigator: { clipboard: { writeText: async () => {} } },
    WebSocket: Socket, console,
  };
  if (injected) context.window.NEUROBRIDGE_B_CLIENT_ENDPOINT = injected;
  vm.runInNewContext(fs.readFileSync(path.join(root, "web/b-client-test/app.js"), "utf8"), context);
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
first.failSend = true;
client.node("#getStatusButton").listeners.click();
assert.ok(logText().includes("connection reset"));
first.close();
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
console.log("B-side page: static default, autoconnect/status, reconnect, safe bounded logs passed");
