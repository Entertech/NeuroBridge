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
};

function render(status) {
  if (status.error) {
    fields.message.textContent = status.error;
    return;
  }
  const state = status.connectionState || 'disconnected';
  fields.connectionState.textContent = state === 'connected' ? '已连接，正在采集' : state === 'connecting' ? '正在扫描或连接' : '未连接';
  fields.algorithmState.textContent = status.algorithmState || 'unavailable';
  fields.recordingId.textContent = status.recordingId || '等待连接';
  fields.connectionError.textContent = status.connectionError || (state === 'connected' ? '已完成' : '等待扫描');
  fields.websocketUrl.textContent = status.websocketUrl || '—';
  fields.indicator.dataset.state = state;
  fields.stop.disabled = !status.captureRunning;
  const canSave = Boolean(status.exportUrl);
  fields.save.href = status.exportUrl || '#';
  fields.save.setAttribute('aria-disabled', String(!canSave));
  fields.message.textContent = status.connectionError || '';
}

async function request(path) {
  const response = await fetch(path, { method: path.includes('/start') || path.includes('/stop') ? 'POST' : 'GET', cache: 'no-store' });
  const status = await response.json();
  render(status);
  return status;
}

async function refresh() {
  try {
    await request('/api/status');
  } catch {
    fields.message.textContent = '无法读取本机采集服务状态。请查看启动此页面的终端。';
  }
  try {
    const response = await fetch('/api/logs', { cache: 'no-store' });
    const { entries } = await response.json();
    const format = ({ timestampMs, level, message }) => {
      const time = new Date(timestampMs).toLocaleTimeString('zh-CN', { hour12: false });
      return `${time} ${level.padEnd(7)} ${message}`;
    };
    const renderLogs = (element, filtered, empty) => {
      const next = filtered.map(format).join('\n') || empty;
      const keepAtBottom = element.scrollTop + element.clientHeight >= element.scrollHeight - 8;
      if (element.textContent !== next) element.textContent = next;
      if (keepAtBottom) element.scrollTop = element.scrollHeight;
    };
    const isData = ({ message }) => message.startsWith('Received headband data');
    const isAlgorithm = ({ message }) => message.startsWith('Algorithm output');
    renderLogs(fields.dataLogs, entries.filter(isData), '等待头环数据…');
    renderLogs(fields.algorithmLogs, entries.filter(isAlgorithm), '等待算法 bridge 输出…');
    renderLogs(fields.systemLogs, entries.filter((entry) => !isData(entry) && !isAlgorithm(entry)), '等待本地服务日志…');
  } catch {
    fields.dataLogs.textContent = '无法读取本地日志。';
    fields.algorithmLogs.textContent = '无法读取本地日志。';
    fields.systemLogs.textContent = '无法读取本地日志。';
  }
}

fields.start.addEventListener('click', () => request('/api/capture/start'));
fields.stop.addEventListener('click', () => request('/api/capture/stop'));
fields.save.addEventListener('click', (event) => {
  if (fields.save.getAttribute('aria-disabled') === 'true') event.preventDefault();
});

request('/api/capture/start').catch(() => {
  fields.message.textContent = '无法启动本机扫描。请检查终端蓝牙授权。';
});
setInterval(refresh, 1500);
