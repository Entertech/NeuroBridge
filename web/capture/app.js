const elements = {
  file: document.querySelector('#logFile'),
  fileName: document.querySelector('#fileName'),
  input: document.querySelector('#logInput'),
  analyze: document.querySelector('#analyzeButton'),
  clear: document.querySelector('#clearButton'),
  message: document.querySelector('#message'),
  serial: document.querySelector('#serialResult'),
  handshake: document.querySelector('#handshakeResult'),
  control: document.querySelector('#controlResult'),
  frames: document.querySelector('#framesResult'),
  overall: document.querySelector('#overallState'),
  diagnosis: document.querySelector('#diagnosisText'),
  metrics: document.querySelector('#metrics'),
  responseClassification: document.querySelector('#responseClassification'),
  responseBytes: document.querySelector('#responseBytes'),
  readBytes: document.querySelector('#readBytes'),
  frameCount: document.querySelector('#frameCount'),
  discardedBytes: document.querySelector('#discardedBytes'),
  streamHandshakeFrames: document.querySelector('#streamHandshakeFrames'),
  lossRate: document.querySelector('#lossRate'),
  keyEvents: document.querySelector('#keyEvents'),
};

const relevantPatterns = [
  /Serial discovery/i,
  /Serial candidate/i,
  /Serial permission/i,
  /handshake/i,
  /ACK sent/i,
  /Serial target selected/i,
  /Serial control response/i,
  /Serial valid-frame timeout/i,
  /Serial invalid frame/i,
  /Serial capture summary/i,
  /Serial connection failed/i,
  /Serial reconnect scheduled/i,
];
const handshakeResponseClassifications = new Set([
  'fixed_handshake',
  'contains_fixed_handshake',
  'partial_handshake',
]);

function lastMatch(text, pattern) {
  const matches = [...text.matchAll(pattern)];
  return matches.length ? matches[matches.length - 1] : null;
}

function valueFrom(text, field) {
  const match = lastMatch(text, new RegExp(`(?:^|\\s)${field}=([^\\s]+)`, 'gim'));
  return match ? match[1] : null;
}

function lastLine(text, pattern) {
  const lines = text.split(/\r?\n/).filter((line) => pattern.test(line));
  return lines.length ? lines[lines.length - 1] : '';
}

function setCard(name, text, state) {
  elements[name].textContent = text;
  document.querySelector(`[data-result="${name}"]`).dataset.state = state;
}

function setOverall(state, label, diagnosis) {
  elements.overall.dataset.state = state;
  elements.overall.textContent = label;
  elements.diagnosis.textContent = diagnosis;
}

function analyze(text) {
  const content = text.trim();
  if (!content) {
    elements.message.textContent = '没有可分析的日志内容。请先选择文件或粘贴日志。';
    return;
  }

  const opened = /Serial candidate opened/i.test(content);
  const target = /Serial target selected/i.test(content);
  const noCandidates = /no usable candidates|Unable to open any usable serial candidate/i.test(content);
  const permissionDenied = /Permission denied|permission preflight.*(?:canRead|canWrite)=false/i.test(content);
  const handshakeAccepted = /Serial handshake accepted and ACK sent/i.test(content);
  const badHandshake = /produced bytes but no valid handshake/i.test(content);
  const startResponseLine = lastLine(content, /Serial control response received: command=start/i);
  const responseClassification = valueFrom(startResponseLine, 'responseClassification');
  const responseBytes = valueFrom(startResponseLine, 'responseBytes');
  const expectedAck = valueFrom(startResponseLine, 'expectedAck01');
  const controlTimeout = /Serial control response timed out/i.test(content);
  const frameCount = Number(valueFrom(content, 'frames') || 0);
  const readBytes = valueFrom(content, 'readBytes');
  const discardedBytes = valueFrom(content, 'discardedBytes');
  const streamHandshakeFrames = Number(valueFrom(content, 'streamHandshakeFrames') || 0);
  const lossRate = valueFrom(content, 'lossRatePercent');
  const frameTimeout = /Serial valid-frame timeout/i.test(content);

  if (target) setCard('serial', '已打开并选中目标串口', 'ok');
  else if (permissionDenied) setCard('serial', '串口存在，但当前进程权限不足', 'error');
  else if (noCandidates) setCard('serial', '没有可打开的串口候选', 'error');
  else if (opened) setCard('serial', '已打开候选，尚未确认目标', 'warn');
  else setCard('serial', '日志中没有串口打开记录', 'idle');

  if (handshakeAccepted) setCard('handshake', '固定握手正确，ACK 已发送', 'ok');
  else if (badHandshake) setCard('handshake', '收到字节，但固定握手不匹配', 'error');
  else setCard('handshake', '日志中没有有效握手记录', 'idle');

  if (responseClassification === 'single_byte_0x01' && expectedAck === 'true') {
    setCard('control', '已确认收到单字节 0x01', 'ok');
  } else if (handshakeResponseClassifications.has(responseClassification)) {
    setCard('control', '收到的是握手内容，不是明确的 0x01', 'error');
  } else if (responseClassification) {
    setCard('control', `已收到响应：${responseClassification}`, 'warn');
  } else if (controlTimeout) {
    setCard('control', '控制响应超时', 'error');
  } else {
    setCard('control', '旧日志缺少响应分类，请更新程序后重试', 'idle');
  }

  if (frameCount > 0) setCard('frames', `已解析 ${frameCount} 个有效帧`, 'ok');
  else if (frameTimeout) setCard('frames', '持续收到或等待数据，但有效帧为 0', 'error');
  else setCard('frames', '日志中尚无有效帧统计', 'idle');

  if (frameCount > 0) {
    setOverall('ok', '链路有数据', 'USB 串口、控制阶段和数据分帧已经运行；继续结合丢包率和算法日志验收。');
  } else if ((handshakeResponseClassifications.has(responseClassification) || streamHandshakeFrames > 0) && frameTimeout) {
    setOverall('error', '疑似仍在握手', '0xE1 后收到的内容被识别为固定握手，且有效帧持续为 0。优先让设备方确认收到 ACK 后是否停止握手，以及 0xE1 后是否应返回单字节 0x01。');
  } else if (responseClassification === 'single_byte_0x01' && frameTimeout) {
    setOverall('error', '响应成功但帧不匹配', '已经确认收到 0x01，但没有解析出 28 字节帧。应核对设备实际数据是否符合 AA AA AA 1C … BB BB BB。');
  } else if (permissionDenied || noCandidates) {
    setOverall('error', '先解决串口访问', '程序尚未进入设备协议阶段。先处理串口节点、驱动、权限或占用问题。');
  } else if (badHandshake) {
    setOverall('error', '握手不匹配', '串口基本读写已经工作，但收到的字节不是约定的固定 7 字节握手。');
  } else {
    setOverall('warn', '信息不足', '日志不足以完成判断。请使用更新后的程序运行至少一个连接和 5 秒数据超时周期，再导出日志。');
  }

  elements.metrics.hidden = false;
  elements.responseClassification.textContent = responseClassification || '未记录';
  elements.responseBytes.textContent = responseBytes || '未记录';
  elements.readBytes.textContent = readBytes || '未记录';
  elements.frameCount.textContent = String(frameCount);
  elements.discardedBytes.textContent = discardedBytes || '未记录';
  elements.streamHandshakeFrames.textContent = String(streamHandshakeFrames);
  elements.lossRate.textContent = lossRate ? `${lossRate}%` : '未记录';

  const relevant = content.split(/\r?\n/).filter((line) => relevantPatterns.some((pattern) => pattern.test(line)));
  elements.keyEvents.textContent = relevant.slice(-160).join('\n') || '没有识别到 USB 串口关键日志。';
  elements.message.textContent = `已在本机分析 ${content.split(/\r?\n/).length} 行日志；未发送任何网络请求。`;
}

elements.file.addEventListener('change', () => {
  const [file] = elements.file.files;
  if (!file) return;
  elements.fileName.textContent = file.name;
  const reader = new FileReader();
  reader.addEventListener('load', () => {
    elements.input.value = String(reader.result || '');
    analyze(elements.input.value);
  });
  reader.addEventListener('error', () => {
    elements.message.textContent = '无法读取该文件，请确认它是本地文本日志。';
  });
  reader.readAsText(file, 'utf-8');
});

elements.analyze.addEventListener('click', () => analyze(elements.input.value));
elements.clear.addEventListener('click', () => {
  elements.file.value = '';
  elements.fileName.textContent = '尚未选择文件';
  elements.input.value = '';
  elements.metrics.hidden = true;
  elements.keyEvents.textContent = '等待日志……';
  elements.message.textContent = '等待日志。页面不会自动访问网关或任何网络地址。';
  ['serial', 'handshake', 'control', 'frames'].forEach((name) => setCard(name, '等待日志', 'idle'));
  setOverall('idle', '待分析', '导入日志后，这里会给出最直接的现场判断。');
});
