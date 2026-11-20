const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

class Element {
  constructor() { this.children = []; this.listeners = {}; this.dataset = {}; this.style = {}; this.value = ''; this.hidden = true; }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.children.push(item); return item; }
  replaceChildren(...items) { this.children = [...items]; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute() {}
  remove() {}
  set innerHTML(value) { throw new Error('User-controlled HTML must never be parsed'); }
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const owner = {subject: 'owner-1', login: 'owner', name: 'Owner', role: 'admin', is_owner: true, last_seen: '2026-11-20T10:00:00Z'};
const reader = {subject: 'reader-1', login: 'reader', name: '<img src=x onerror=alert(1)>', role: 'user', is_owner: false, last_seen: owner.last_seen};

async function usersPage({canManage = true, failSave = false} = {}) {
  const elements = Object.fromEntries(['usersRows', 'usersStatus', 'usersSearch', 'usersRefresh', 'usersPage'].map(id => [id, new Element()]));
  const calls = [], redirects = [];
  const window = {catalogAuth: {ready: Promise.resolve({can_manage_users: canManage})}, addEventListener() {}};
  const context = {
    window, document: {getElementById: id => elements[id], createElement: () => new Element()},
    location: {replace: path => redirects.push(path)}, confirm: () => true,
    fetch: async (url, options = {}) => {
      calls.push({url, options});
      if (!options.method) return {ok: true, json: async () => ({users: [owner, reader]})};
      if (failSave) return {ok: false, json: async () => ({error: 'Denied'})};
      return {ok: true, json: async () => ({user: {...reader, role: JSON.parse(options.body).role}})};
    },
  };
  vm.runInNewContext(fs.readFileSync('static/js/users.js', 'utf8'), context);
  await flush();
  return {elements, calls, redirects};
}

test('regular users cannot open the role editor or request user data', async () => {
  const page = await usersPage({canManage: false});
  assert.deepEqual(page.redirects, ['/']);
  assert.equal(page.calls.length, 0);
  assert.equal(page.elements.usersPage.hidden, true);
});

test('owner row has no role button and untrusted names remain plain text', async () => {
  const {elements} = await usersPage();
  const rows = elements.usersRows.children;
  assert.equal(rows[0].children[4].children.length, 0);
  assert.equal(rows[1].children[0].textContent, reader.name);
  assert.equal(elements.usersPage.hidden, false);
});

test('grant and revoke send the selected immutable subject and role', async () => {
  const {elements, calls} = await usersPage();
  await elements.usersRows.children[1].children[4].children[0].listeners.click();
  assert.equal(calls[1].url, '/api/admin/users/reader-1/role');
  assert.equal(calls[1].options.method, 'PUT');
  assert.deepEqual(JSON.parse(calls[1].options.body), {role: 'admin'});
  assert.equal(elements.usersRows.children[1].children[2].textContent, 'Администратор');
  await elements.usersRows.children[1].children[4].children[0].listeners.click();
  assert.deepEqual(JSON.parse(calls[2].options.body), {role: 'user'});
  assert.equal(elements.usersRows.children[1].children[2].textContent, 'Обычный пользователь');
});

test('failed saves leave the displayed role unchanged and show an error', async () => {
  const {elements} = await usersPage({failSave: true});
  await elements.usersRows.children[1].children[4].children[0].listeners.click();
  assert.equal(elements.usersRows.children[1].children[2].textContent, 'Обычный пользователь');
  assert.equal(elements.usersStatus.dataset.error, 'true');
  assert.equal(elements.usersStatus.textContent, 'Denied');
  assert.equal(elements.usersRows.children[1].children[4].children[0].disabled, false);
});

test('search filters by login without parsing names as HTML', async () => {
  const {elements} = await usersPage();
  elements.usersSearch.value = 'READER';
  elements.usersSearch.listeners.input();
  assert.equal(elements.usersRows.children.length, 1);
  assert.equal(elements.usersRows.children[0].children[0].textContent, reader.name);
});

test('a denied mutation refreshes current permissions and removes admin controls', async () => {
  let user = {is_admin: true, can_manage_users: false};
  const classes = new Set();
  const events = [];
  const window = {
    fetch: async url => url === '/api/me' ? {ok: true, status: 200, json: async () => ({...user})} : {status: 403},
    addEventListener() {}, dispatchEvent: event => events.push(event.type),
  };
  const document = {
    readyState: 'complete', getElementById: () => null,
    documentElement: {classList: {toggle: (name, value) => value ? classes.add(name) : classes.delete(name)}},
    body: new Element(), createElement: () => new Element(),
  };
  vm.runInNewContext(fs.readFileSync('static/js/auth.js', 'utf8'), {
    window, document, location: {pathname: '/', search: ''},
    CustomEvent: class {constructor(type) {this.type = type;}}, setTimeout: () => 0,
  });
  await window.catalogAuth.ready;
  assert.equal(classes.has('role-admin'), true);
  user = {is_admin: false, can_manage_users: false};
  const result = await window.fetch('/api/change', {method: 'POST'});
  assert.equal(result.status, 403);
  assert.equal(window.catalogAuth.isAdmin(), false);
  assert.equal(classes.has('role-admin'), false);
  assert.deepEqual(events, ['catalog-permissions-changed']);
});
