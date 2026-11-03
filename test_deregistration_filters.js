// No browser or Consul connection: exercise applied-vs-draft filter behavior.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Element {
  constructor() { this.children = []; this.handlers = {}; this.value = ''; this.checked = true; this.textContent = ''; this.classList = {toggle() {}, remove() {}}; }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.handlers[name] = callback; }
}
async function test() {
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const calls = [];
  let tick, rejectNext = false;
  const context = {
    document: {getElementById: get, createElement: () => new Element(), hidden: false},
    URLSearchParams, Date,
    setInterval: callback => { tick = callback; },
    fetch: async url => {
      calls.push(new URL(url, 'http://localhost').searchParams);
      const fail = rejectNext; rejectNext = false;
      return {ok: !fail, json: async () => fail ? {error: 'fixture failure'} : {
        events: [], next_before: null,
        coverage: {files: [], portal_targets: [], portal_mode: 'test', audit_configured: false, audit_files_found: 0}
      }};
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'static/js/deregistrations.js'), 'utf8'), context);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle();
  get('node').value = 'uknrd101';
  tick(); await settle();
  assert.equal(calls.at(-1).get('node'), '', 'Draft typing must not change auto-refresh');
  get('filters').handlers.submit({preventDefault() {}}); await settle();
  assert.equal(calls.at(-1).get('node'), 'uknrd101');
  assert.match(get('applied-filters').children[0].textContent, /uknrd101/);
  get('node').value = 'different-node';
  tick(); await settle();
  assert.equal(calls.at(-1).get('node'), 'uknrd101', 'Auto-refresh must keep applied node');
  rejectNext = true;
  get('filters').handlers.submit({preventDefault() {}}); await settle();
  assert.match(get('applied-filters').children[0].textContent, /uknrd101/, 'Failed apply must not relabel old results');
  tick(); await settle();
  assert.equal(calls.at(-1).get('node'), 'uknrd101');
  console.log('PASS: draft edits, apply, auto-refresh and failed-apply consistency');
}
test().catch(error => { console.error(error); process.exitCode = 1; });
