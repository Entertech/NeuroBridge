"use strict";

const fields = Object.fromEntries([
  'apiBase', 'connectButton', 'connectionState', 'algorithmState', 'recordingId',
  'connectionError', 'websocketUrl', 'indicator', 'message', 'startButton',
  'stopButton', 'saveButton', 'bClientLink', 'dataLogs', 'algorithmLogs', 'systemLogs',
].map(id => [id, document.querySelector(`#${id}`)]));
const defaultApi = /^https?:$/.test(window.location.protocol)
  ? window.location.origin : 'http://192.168.88.10:8766';
fields.apiBase.value = defaultApi;

let apiBase = defaultApi;
let status = null;
let busy = false;
let refreshing = false;
let generation = 0;
let operationError = '';
let networkError = '';

function setMessage() {
  fields.message.textContent = networkError || operationError || status?.connectionError || '';
}

function updateButtons() {
  fields.connectButton.disabled = busy;
  fields.apiBase.disabled = busy;
  fields.startButton.disabled = busy || !status || Boolean(status.captureRunning);
  fields.stopButton.disabled = busy || !status || (!status.captureRunning && !status.connectionError);
  const canSave = Boolean(status?.recordingId || status?.exportUrl);
  fields.saveButton.setAttribute('aria-disabled', String(busy || !canSave));
  fields.saveButton.textContent = status?.recordingId ? '停止并保存 ZIP' : '保存 ZIP';
}

function downloadUrl(relative) {
  const url = new URL(relative, `${apiBase}/`);
  if (url.origin !== apiBase || !/^https?:$/.test(url.protocol)) throw new Error('网关返回了不属于当前服务的下载地址。');
  return url.href;
}

function render(next) {
  status = next;
  const state = next.connectionState || 'disconnected';
  fields.connectionState.textContent = state === 'connected' ? '已连接，正在采集' : state === 'connecting' ? '正在扫描或连接' : '头环未连接';
  fields.algorithmState.textContent = next.algorithmState || 'unavailable';
  fields.recordingId.textContent = next.recordingId || '无正在录制的会话';
  fields.connectionError.textContent = next.connectionError || (state === 'connected' ? '已完成' : next.captureRunning ? '等待扫描或重连' : '采集任务已停止');
  fields.websocketUrl.textContent = next.websocketUrl || '—';
  fields.indicator.dataset.state = state;
  fields.saveButton.href = next.exportUrl ? downloadUrl(next.exportUrl) : '#';
  const client = new URL('../b-client-test/index.html', window.location.href);
  if (next.websocketUrl) client.searchParams.set('endpoint', next.websocketUrl);
  fields.bClientLink.href = client.href;
  updateButtons();
  setMessage();
}

async function request(path, method = 'GET') {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), method === 'GET' ? 5000 : 15000);
  try {
    const response = await fetch(new URL(path, `${apiBase}/`).href, {
      method, cache: 'no-store', mode: 'cors', credentials: 'omit', signal: controller.signal,
    });
    let result;
    try { result = await response.json(); }
    catch { throw new Error(`服务没有返回 JSON（HTTP ${response.status}），请确认网关已更新且采集控制已启用。`); }
    if (!response.ok || result.error || (result.code !== undefined && result.code !== 200)) {
      throw new Error(result.message || result.error || `HTTP ${response.status}`);
    }
    return result.code === 200 && result.data ? result.data : result;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('访问网关超时，请检查有线网络和服务状态。');
    if (error instanceof TypeError) throw new Error('无法访问网关。请检查 HTTP 地址、有线网络、CORS 和浏览器本地网络权限。也可打开网关托管的 /capture/index.html。');
    throw error;
  } finally { clearTimeout(timeout); }
}

function renderLogs(entries) {
  const format = entry => {
    const message = String(entry.message || '').replace(/preview=[^;]+/g, 'preview=[已省略]');
    return `${new Date(entry.timestampMs).toISOString()} ${String(entry.level || 'INFO')} ${message}`;
  };
  const show = (element, selected, empty) => {
    const keepAtBottom = element.scrollTop + element.clientHeight >= element.scrollHeight - 8;
    element.textContent = selected.map(format).join('\n') || empty;
    if (keepAtBottom) element.scrollTop = element.scrollHeight;
  };
  const isData = entry => String(entry.message).startsWith('Received headband data');
  const isAlgorithm = entry => String(entry.message).startsWith('Algorithm output');
  show(fields.dataLogs, entries.filter(isData), '等待头环数据…');
  show(fields.algorithmLogs, entries.filter(isAlgorithm), '等待算法结果…');
  show(fields.systemLogs, entries.filter(entry => !isData(entry) && !isAlgorithm(entry)), '等待网关运行日志…');
}

async function refresh() {
  if (busy || refreshing) return;
  refreshing = true;
  const current = generation;
  try {
    const next = await request('/api/status');
    if (current !== generation) return;
    networkError = '';
    render(next);
    const logs = await request('/api/logs');
    if (current === generation) renderLogs(Array.isArray(logs.entries) ? logs.entries : []);
  } catch (error) {
    if (current === generation) {
      networkError = error.message;
      setMessage();
    }
  } finally { refreshing = false; }
}

async function operate(action, save = false) {
  if (busy || !status) return;
  busy = true;
  generation += 1;
  operationError = '';
  networkError = '';
  updateButtons();
  try {
    let next = status;
    if (!save || next.captureRunning || next.recordingId) {
      next = await request(`/api/capture/${action}`, 'POST');
      render(next);
    }
    if (save) {
      if (next.recordingId || !next.exportUrl) throw new Error('尚无已完成的录制会话可下载。');
      window.location.assign(downloadUrl(next.exportUrl));
    }
  } catch (error) { operationError = error.message; }
  finally {
    busy = false;
    updateButtons();
    setMessage();
  }
}

function connect() {
  try {
    const url = new URL(fields.apiBase.value.trim());
    if (!/^https?:$/.test(url.protocol) || url.username || url.password || !['', '/'].includes(url.pathname) || url.search || url.hash) {
      throw new Error('请只填写网关 HTTP 地址和端口，不包含账号密码、路径或查询参数。');
    }
    apiBase = url.origin;
    generation += 1;
    status = null;
    operationError = '';
    networkError = '';
    updateButtons();
    fields.connectionState.textContent = '正在连接网关…';
    setMessage();
    return refresh();
  } catch (error) { operationError = error.message; setMessage(); }
}

fields.connectButton.addEventListener('click', connect);
fields.startButton.addEventListener('click', () => operate('start'));
fields.stopButton.addEventListener('click', () => operate('stop'));
fields.saveButton.addEventListener('click', event => {
  event.preventDefault();
  if (fields.saveButton.getAttribute('aria-disabled') !== 'true') return operate('stop', true);
});
connect();
setInterval(refresh, 1500);
