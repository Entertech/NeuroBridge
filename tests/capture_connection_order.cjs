const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
const sockets = [];
class Socket {
  static OPEN=1; static CONNECTING=0;
  constructor() {this.readyState=0;this.handlers={};this.sent=[];sockets.push(this);}
  addEventListener(event,handler) {this.handlers[event]=handler;}
  send(value) {this.sent.push(JSON.parse(value));}
  open() {this.readyState=1;this.handlers.open();}
  respond(action,requestId,result={}) {this.handlers.message({data:JSON.stringify({code:200,data:{action,requestId,result}})});}
}
const context = vm.createContext({
  window: {NEUROBRIDGE_VERSION:{protocolVersion:'1.0'},NEUROBRIDGE_B_CLIENT_ENDPOINT:'ws://127.0.0.1/test'},
  document: {querySelector(id) {
    if (!nodes.has(id)) nodes.set(id,{value:'',dataset:{},classList:{toggle(){}},setAttribute(){},addEventListener(){}});
    return nodes.get(id);
  }}, WebSocket:Socket, Uint8Array, Date, Math, Number, String, Set, Map,
});
vm.runInContext(fs.readFileSync('web/capture/app.js','utf8'),context);
context.document.querySelector('#startButton').click = () => context.document.querySelector('#startButton').onclick?.();
for(let cycle=0;cycle<2;cycle++) {
  const socket=sockets.at(-1);socket.open();
  assert.deepEqual(socket.sent.map(r=>r.action),['getStatus']);
  assert.equal(nodes.get('#startButton').disabled,true);
  socket.respond('getStatus','unrelated');
  assert.equal(socket.sent.length,1);
  socket.respond('getStatus',socket.sent[0].requestId);
  assert.equal(socket.sent[1].action,'subscribe');
  assert.deepEqual(socket.sent[1].params.streams,['status']);
  socket.respond('subscribe',socket.sent[1].requestId,{subscriptionId:'status-'+cycle,streams:['status']});
  assert.equal(nodes.get('#startButton').disabled,false);
  assert.equal(socket.sent[2].action,'getStatus'); // close the snapshot/subscription race
  socket.readyState=3;socket.handlers.close({code:1000});
  if(cycle===0) vm.runInContext('connect()',context);
}
const dualNodes = new Map();
const dualSockets = [];
class DualSocket extends Socket {
  constructor() {super();dualSockets.push(this);}
}
const dual = vm.createContext({
  window: {NEUROBRIDGE_VERSION:{protocolVersion:'1.0'},NEUROBRIDGE_B_CLIENT_ENDPOINT:'ws://127.0.0.1/test',NEUROBRIDGE_B_CLIENT_SOURCE:'dual'},
  document: {querySelector(id) {
    if (!dualNodes.has(id)) dualNodes.set(id,{value:'',textContent:'',scrollTop:0,scrollHeight:0,dataset:{},classList:{toggle(){}},setAttribute(){},addEventListener(event,handler){if(event==='click') this.onclick=handler;}});
    return dualNodes.get(id);
  }, body:{append(){}}, head:{append(){}}, createElement(){return {click(){},remove(){}};}},
  WebSocket:DualSocket, Uint8Array, Date, Math, Number, String, Array, Set, Map, JSON, atob: (value) => Buffer.from(value, 'base64').toString('binary'),
});
vm.runInContext(fs.readFileSync('web/capture/app.js','utf8'),dual);
const dualSocket = dualSockets.at(-1);
dualSocket.open();
assert.deepEqual(dualSocket.sent.map(request => request.action), ['getStatus']);
dualSocket.respond('getStatus', dualSocket.sent[0].requestId);
assert.deepEqual(dualSocket.sent.slice(1).map(request => request.params.source), ['headset', 'headband']);
dualSocket.respond('subscribe', dualSocket.sent[1].requestId, {subscriptionId:'headset-status', streams:['status']});
dual.vm = undefined;
dualNodes.get('#startButton').onclick();
assert.deepEqual(dualSocket.sent.at(-2).params, {streams:['eeg.raw','hr.raw'], includeInvalid:true, source:'headset'});
assert.deepEqual(dualSocket.sent.at(-1).params, {streams:['eeg.raw','hr.raw'], includeInvalid:true, source:'headband'});
const packet = Buffer.from([0,1, ...Array(18).fill(7)]).toString('base64');
const heart = Buffer.from([80]).toString('base64');
dualSocket.handlers.message({data:JSON.stringify({code:200,data:{source:'headband',event:'data',payload:{
  eegRaw:{encoding:'base64',packetBytes:20,packetCount:1,windowStartMs:1,windowEndMs:2,bytesBase64:packet},
  hrRaw:{encoding:'base64',packetBytes:1,packetCount:1,windowStartMs:1,windowEndMs:2,bytesBase64:heart},
}}})});
assert.match(dualNodes.get('#headbandRawData').textContent, /07 07 07/);
assert.match(dualNodes.get('#headbandRawData').textContent, /50/);
assert.equal(dualNodes.get('#rawData').textContent, '等待网关数据……');
assert.doesNotMatch(dualNodes.get('#headbandRawData').textContent, /AA AA AA/);
console.log('capture connection/reconnection order passed');
