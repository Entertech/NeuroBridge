const fields = {
  connectionState: document.querySelector('#connectionState'),
  algorithmState: document.querySelector('#algorithmState'),
  recordingId: document.querySelector('#recordingId'),
  websocketUrl: document.querySelector('#websocketUrl'),
  indicator: document.querySelector('#indicator'),
  message: document.querySelector('#message'),
  start: document.querySelector('#startButton'),
  stop: document.querySelector('#stopButton'),
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
  fields.websocketUrl.textContent = status.websocketUrl || '—';
  fields.indicator.dataset.state = state;
  fields.stop.disabled = !status.captureRunning;
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
}

fields.start.addEventListener('click', () => request('/api/capture/start'));
fields.stop.addEventListener('click', () => request('/api/capture/stop'));

request('/api/capture/start').catch(() => {
  fields.message.textContent = '无法启动本机扫描。请检查终端蓝牙授权。';
});
setInterval(refresh, 1500);
