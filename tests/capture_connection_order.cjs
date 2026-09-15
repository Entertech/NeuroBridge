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
console.log('capture connection/reconnection order passed');
