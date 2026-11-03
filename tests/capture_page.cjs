"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/ubuntu/capture/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../web/ubuntu/capture/index.html'), 'utf8');
assert.ok(html.includes('href="./styles.css"'));
assert.ok(html.includes('src="./app.js"'));

class Element {
  constructor() { this.attributes = {}; this.listeners = {}; this.dataset = {}; this.value = ''; this.clientHeight = this.scrollTop = this.scrollHeight = 0; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
}

function page(hosted = false, legacy = false) {
  const nodes = new Map();
  const node = selector => { if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); };
  const requests = [], downloads = [], intervals = [];
  const server = { captureRunning: false, recordingId: null, exportUrl: null, connectionState: 'disconnected', algorithmState: 'unavailable', websocketUrl: 'ws://192.168.88.10:8765/neurobridge/v1/ws' };
  let rejectStart = false;
  let deferStatus = false;
  let releaseStatus;
  const response = (data, code = 200) => ({ ok: code === 200, status: code, json: async () => JSON.parse(JSON.stringify(legacy ? data : { protocolVersion: '1.0', code, data: code === 200 ? data : {}, message: code === 200 ? 'OK' : 'Device capture is disabled in deployment configuration.' })) });
  const context = {
    window: { location: { protocol: hosted ? 'http:' : 'file:', origin: hosted ? 'http://127.0.0.1:8090' : 'null', href: hosted ? 'http://127.0.0.1:8090/capture/index.html' : 'file:///test/capture/index.html', assign: url => downloads.push(url) } },
    document: { querySelector: node }, URL, URLSearchParams, AbortController, setTimeout, clearTimeout,
    setInterval: callback => intervals.push(callback),
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      assert.ok(/^https?:\/\//.test(url), 'file:// pages must use absolute gateway URLs');
      const target = new URL(url).pathname;
      if (target === '/api/status') {
        if (deferStatus) {
          deferStatus = false;
          const stale = JSON.parse(JSON.stringify(server));
          return new Promise(resolve => { releaseStatus = () => resolve(response(stale)); });
        }
        return response(server);
      }
      if (target === '/api/logs') return response({ entries: [{ timestampMs: 1000, level: 'INFO', message: 'Received headband data: eegPackets=1' }, { timestampMs: 1000, level: 'INFO', message: 'Algorithm output: attention=7' }] });
      assert.equal(options.method, 'POST');
      if (target === '/api/capture/start') {
        if (rejectStart) return response({}, 503);
        Object.assign(server, { captureRunning: true, recordingId: 'rec-test', exportUrl: null, connectionState: 'connected' });
      } else if (target === '/api/capture/stop') {
        Object.assign(server, { captureRunning: false, recordingId: null, exportUrl: '/downloads/recordings/rec-test.zip', connectionState: 'disconnected' });
      } else throw new Error(`Unexpected request: ${url}`);
      return response(server);
    },
  };
  vm.runInNewContext(source, context);
  return { node, requests, downloads, intervals, server, rejectStart: value => { rejectStart = value; }, deferStatus: () => { deferStatus = true; }, releaseStatus: () => releaseStatus() };
}

const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const client = page();
  await flush();
  assert.equal(client.requests[0].url, 'http://192.168.88.10:8766/api/status');
  assert.ok(client.requests.every(item => item.method === 'GET'));
  assert.equal(client.node('#startButton').disabled, false);
  assert.ok(client.node('#dataLogs').textContent.includes('eegPackets=1'));
  assert.equal(new URL(client.node('#bClientLink').href).searchParams.get('endpoint'), client.server.websocketUrl);
  client.deferStatus();
  const refresh = client.intervals[0]();
  await flush();
  await client.node('#startButton').listeners.click();
  client.releaseStatus();
  await refresh;
  assert.equal(client.node('#connectionState').textContent, '已连接，正在采集');
  assert.equal(client.node('#saveButton').textContent, '停止并保存 ZIP');
  await client.node('#saveButton').listeners.click({ preventDefault() {} });
  assert.equal(client.requests.at(-1).url, 'http://192.168.88.10:8766/api/capture/stop');
  assert.equal(client.downloads[0], 'http://192.168.88.10:8766/downloads/recordings/rec-test.zip');
  client.rejectStart(true);
  await client.node('#startButton').listeners.click();
  assert.ok(client.node('#message').textContent.includes('disabled'));
  const count = client.requests.length;
  client.node('#apiBase').value = 'http://user:password@example.test';
  await client.node('#connectButton').listeners.click();
  assert.equal(client.requests.length, count);
  const hosted = page(true, true);
  await flush();
  assert.equal(hosted.requests[0].url, 'http://127.0.0.1:8090/api/status');
  assert.equal(hosted.node('#startButton').disabled, false);
  console.log('Capture page: direct-file gateway URLs, hosted legacy response, control, completed download, stale-response protection and errors passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
