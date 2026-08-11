const fields = {
  connectionState: document.querySelector('#connectionState'),
  algorithmState: document.querySelector('#algorithmState'),
  recordingId: document.querySelector('#recordingId'),
  connectionError: document.querySelector('#connectionError'),
  websocketUrl: document.querySelector('#websocketUrl'),
  indicator: document.querySelector('#indicator'),
  message: document.querySelector('#message'),
  start: document.querySelector('#startButton'),
  stop: document.querySelector('#stopButton'),
  save: document.querySelector('#saveButton'),
  dataLogs: document.querySelector('#dataLogs'),
  algorithmLogs: document.querySelector('#algorithmLogs'),
  systemLogs: document.querySelector('#systemLogs'),
  configPanel: document.querySelector('#configPanel'),
  configFields: document.querySelector('#configFields'),
  configState: document.querySelector('#configState'),
  configForm: document.querySelector('#configForm'),
  configMessage: document.querySelector('#configMessage'),
  saveConfig: document.querySelector('#saveConfigButton'),
};

const configInputs = {
  server: { host: '#serverHost', port: '#serverPort', path: '#serverPath' },
  network: { mode: '#networkMode', interface: '#networkInterface', subnet_cidr: '#networkSubnet', dhcp_range_start: '#dhcpRangeStart', dhcp_range_end: '#dhcpRangeEnd', dhcp_lease_time: '#dhcpLeaseTime' },
  ble: { enabled: '#bleEnabled', device_name: '#bleDeviceName', model_nbr_uuid: '#bleModelUuid', scan_timeout_seconds: '#bleScanTimeout', reconnect_delay_seconds: '#bleReconnectDelay' },
  recording: { directory: '#recordingDirectory', subject_id: '#recordingSubjectId', replay_recording_id: '#replayRecordingId', replay_speed: '#replaySpeed' },
  download: { enabled: '#downloadEnabled', host: '#downloadHost', port: '#downloadPort', path: '#downloadPath' },
  logging: { directory: '#loggingDirectory', filename: '#loggingFilename', level: '#loggingLevel' },
  algorithm: { enabled: '#algorithmEnabled' },
};

function errorFromResponse(response, body) {
  return new Error(body?.error || `${response.status} ${response.statusText}`);
}

async function fetchJson(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options });
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(`服务返回了无效响应（${response.status}）。`);
  }
  if (!response.ok) throw errorFromResponse(response, body);
  return body;
}

function render(status) {
  if (status.error) {
    fields.message.textContent = status.error;
    return;
  }
  const state = status.connectionState || 'disconnected';
  const gatewayPage = status.pageMode === 'gateway';
  fields.connectionState.textContent = gatewayPage
    ? (state === 'gateway-running' ? '网关服务正在运行' : '网关服务未运行')
    : (state === 'connected' ? '已连接，正在采集' : state === 'connecting' ? '正在扫描或连接' : '未连接');
  fields.algorithmState.textContent = gatewayPage
    ? '由网关服务管理'
    : (status.algorithmState || 'unavailable');
  fields.recordingId.textContent = status.recordingId || (gatewayPage ? '请从录播目录或下载页查看' : '等待连接');
  fields.connectionError.textContent = status.connectionError || (state === 'connected' ? '已完成' : '等待扫描');
  fields.websocketUrl.textContent = status.websocketUrl || (gatewayPage ? '配置页保存后按 server 设置提供' : '—');
  fields.indicator.dataset.state = state === 'gateway-running' ? 'connected' : state === 'gateway-stopped' ? 'disconnected' : state;
  fields.stop.disabled = !status.captureRunning;
  fields.start.textContent = gatewayPage ? '启动网关服务' : '重新开始扫描';
  fields.stop.textContent = gatewayPage ? '停止网关服务' : '停止采集';
  const canSave = Boolean(status.exportUrl);
  fields.save.href = status.exportUrl || '#';
  fields.save.setAttribute('aria-disabled', String(!canSave));
  fields.message.textContent = status.connectionError || '';
}

async function request(path) {
  const status = await fetchJson(path, { method: path.includes('/start') || path.includes('/stop') ? 'POST' : 'GET' });
  render(status);
  return status;
}

function formatLog({ timestampMs, level, message }) {
  const time = timestampMs ? new Date(timestampMs).toLocaleTimeString('zh-CN', { hour12: false }) : '--:--:--';
  return `${time} ${(level || 'INFO').padEnd(7)} ${message}`;
}

function renderLogs(element, filtered, empty) {
  const next = filtered.map(formatLog).join('\n') || empty;
  const keepAtBottom = element.scrollTop + element.clientHeight >= element.scrollHeight - 8;
  if (element.textContent !== next) element.textContent = next;
  if (keepAtBottom) element.scrollTop = element.scrollHeight;
}

async function refresh() {
  try {
    render(await fetchJson('/api/status'));
  } catch (error) {
    fields.message.textContent = `无法读取本机采集服务状态：${error.message}`;
  }
  try {
    const { entries } = await fetchJson('/api/logs');
    const isData = ({ message }) => message.startsWith('Received headband data');
    const isAlgorithm = ({ message }) => message.startsWith('Algorithm output');
    renderLogs(fields.dataLogs, entries.filter(isData), '等待头环数据…');
    renderLogs(fields.algorithmLogs, entries.filter(isAlgorithm), '等待算法 bridge 输出…');
    renderLogs(fields.systemLogs, entries.filter((entry) => !isData(entry) && !isAlgorithm(entry)), '等待本地服务日志…');
  } catch (error) {
    const message = `无法读取本地日志：${error.message}`;
    fields.dataLogs.textContent = message;
    fields.algorithmLogs.textContent = message;
    fields.systemLogs.textContent = message;
  }
}

function controlFor(selector) {
  return document.querySelector(selector);
}

function setConfig(config) {
  for (const [section, keys] of Object.entries(configInputs)) {
    for (const [key, selector] of Object.entries(keys)) {
      const control = controlFor(selector);
      const value = config[section]?.[key];
      if (control.type === 'checkbox') control.checked = Boolean(value);
      else control.value = value ?? '';
    }
  }
  updateDhcpFields();
}

function getConfig() {
  const config = {};
  for (const [section, keys] of Object.entries(configInputs)) {
    config[section] = {};
    for (const [key, selector] of Object.entries(keys)) {
      const control = controlFor(selector);
      if (control.type === 'checkbox') config[section][key] = control.checked;
      else if (control.type === 'number') config[section][key] = Number(control.value);
      else config[section][key] = control.value.trim();
    }
  }
  return config;
}

function updateDhcpFields() {
  const enabled = controlFor('#networkMode').value === 'dhcp';
  document.querySelectorAll('.dhcp-field input').forEach((input) => {
    input.disabled = !enabled;
    input.required = enabled;
  });
}

function setConfigurationEnabled(enabled) {
  fields.configForm.querySelectorAll('input, select').forEach((control) => {
    control.disabled = !enabled;
  });
  fields.saveConfig.disabled = !enabled;
  if (enabled) updateDhcpFields();
}

async function loadConfiguration() {
  setConfigurationEnabled(false);
  try {
    const response = await fetch('/api/config', { cache: 'no-store' });
    if (response.status === 404) {
      fields.configState.textContent = '此页面由采集 POC 承载';
      fields.configMessage.textContent = '当前服务没有开启网关配置接口；macOS POC 请继续通过启动脚本配置。';
      return;
    }
    const body = await response.json();
    if (!response.ok) throw errorFromResponse(response, body);
    setConfig(body.config);
    setConfigurationEnabled(true);
    fields.configState.textContent = '已读取本机配置';
    fields.configMessage.textContent = '修改后保存会校验配置并重启网关服务。';
  } catch (error) {
    fields.configState.textContent = '配置不可用';
    fields.configMessage.textContent = `无法读取网关配置：${error.message}`;
  }
}

fields.start.addEventListener('click', () => request('/api/capture/start').catch((error) => {
  fields.message.textContent = `无法启动本机采集服务：${error.message}`;
}));
fields.stop.addEventListener('click', () => request('/api/capture/stop').catch((error) => {
  fields.message.textContent = `无法停止本机采集服务：${error.message}`;
}));
fields.save.addEventListener('click', (event) => {
  if (fields.save.getAttribute('aria-disabled') === 'true') event.preventDefault();
});
controlFor('#networkMode').addEventListener('change', updateDhcpFields);
fields.configForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (fields.saveConfig.disabled) return;
  fields.saveConfig.disabled = true;
  fields.configMessage.textContent = '正在校验、保存并重启网关…';
  try {
    const result = await fetchJson('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: getConfig(), applyNetwork: controlFor('#applyNetwork').checked }),
    });
    setConfig(result.config);
    fields.configMessage.textContent = result.message;
    fields.configState.textContent = '配置已保存';
  } catch (error) {
    fields.configMessage.textContent = `保存失败：${error.message}`;
    fields.configState.textContent = '请修正错误';
  } finally {
    fields.saveConfig.disabled = false;
  }
});

request('/api/capture/start').catch((error) => {
  fields.message.textContent = `无法启动本机采集服务：${error.message}`;
});
loadConfiguration();
refresh();
setInterval(refresh, 1500);
