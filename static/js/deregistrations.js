(() => {
  'use strict';
  const el = id => document.getElementById(id);
  let next = null, busy = false, count = 0;
  const results = {requested: 'Ответ неизвестен', http_ok: 'HTTP 2xx', http_error: 'Ошибка HTTP', transport_error: 'Ошибка соединения'};
  function element(tag, text, cls) {
    const node = document.createElement(tag);
    node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }
  function renderEvent(event) {
    const row = document.createElement('tr');
    const time = element('td', (event.started_at || event.recorded_at || '').replace('T', ' ').replace(/(\.\d+)?(Z|\+00:00)$/, ''));
    row.append(time);
    const source = element('td', event.source === 'portal' ? 'Этот портал' : event.source === 'http_proxy' ? 'HTTP-прокси Consul' : 'Аудит Consul');
    source.append(element('div', event.server || 'Сервер неизвестен', 'details'));
    row.append(source);
    const target = element('td', '', 'wrap');
    target.append(element('code', `${event.method || 'PUT'} ${event.endpoint || ''}`));
    target.append(element('div', event.node_name ? `Узел: ${event.node_name}` : 'Узел: нет данных в запросе', 'details'));
    if (event.service_id) target.append(element('div', `Сервис: ${event.service_id}`, 'details'));
    if (event.datacenter) target.append(element('div', `DC: ${event.datacenter}`, 'details'));
    const detail = document.createElement('details');
    detail.append(element('summary', 'Идентификаторы запроса'));
    detail.append(element('div', `Request ID: ${event.request_id || '—'}`));
    detail.append(element('div', `ACL accessor: ${event.token_accessor || 'нет данных'}`));
    target.append(detail); row.append(target);
    const peer = element('td', event.peer_ip || 'Неизвестен', 'wrap');
    if (event.forwarded_for) peer.append(element('div', `X-Forwarded-For: ${event.forwarded_for}`, 'details'));
    row.append(peer);
    const status = element('td', '');
    status.append(element('span', event.http_status ? `HTTP ${event.http_status}` : (results[event.outcome] || 'Неизвестен'), `badge ${event.outcome || ''}`));
    row.append(status); return row;
  }
  async function load(append = false) {
    if (busy) return;
    busy = true; el('refresh').disabled = true; el('more').disabled = true;
    el('error').textContent = '';
    try {
      const params = new URLSearchParams({q: el('query').value, source: el('source').value, outcome: el('outcome').value, limit: '100'});
      if (append && next) params.set('before', next);
      const response = await fetch(`/api/deregistrations?${params}`, {cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      if (!append) { el('rows').replaceChildren(); count = 0; }
      data.events.forEach(event => el('rows').append(renderEvent(event)));
      count += data.events.length;
      if (!count) {
        const row = document.createElement('tr');
        const cell = element('td', 'Записей по выбранным фильтрам нет. Это не доказывает отсутствие дерегистраций вне подключённых источников.', 'empty');
        cell.colSpan = 5; row.append(cell); el('rows').append(row);
      }
      next = data.next_before;
      el('more').hidden = !next;
      el('count').textContent = `Показано записей: ${count}`;
      const coverage = data.coverage;
      const connected = coverage.audit_configured && coverage.audit_files_found > 0;
      const errors = coverage.files.some(file => file.error);
      el('coverage').classList.toggle('connected', connected && !errors);
      el('coverage-title').textContent = !coverage.audit_configured ? 'Серверный аудит не подключён' : errors ? 'Есть ошибки чтения серверного аудита' : connected ? 'Подключены файлы серверного аудита' : 'Файлы серверного аудита не найдены';
      el('coverage-text').textContent = !coverage.audit_configured ? 'Сейчас фиксируются только команды дерегистрации, отправленные этим порталом. Запросы от других клиентов не видны.' : `Найдено файлов: ${coverage.audit_files_found}. Охват зависит от подключённых Consul HTTP audit-логов. Внутренние RPC не записываются.`;
      el('sources').textContent = coverage.files.map(file => `${file.path}: прочитано ${file.offset} байт, некорректных строк ${file.invalid_lines}; ${file.error || 'ошибок чтения нет'}; последнее чтение ${file.last_read}`).join('\n');
      el('updated').textContent = `Обновлено ${new Date().toLocaleTimeString('ru-RU')}. Время событий указано в UTC.`;
    } catch (error) {
      el('error').textContent = error.message || 'Не удалось загрузить журнал';
      el('coverage').classList.remove('connected');
      el('coverage-title').textContent = 'Не удалось проверить источники журнала';
    } finally { busy = false; el('refresh').disabled = false; el('more').disabled = false; }
  }
  el('filters').addEventListener('submit', event => { event.preventDefault(); load(); });
  el('refresh').addEventListener('click', () => load());
  el('more').addEventListener('click', () => { el('auto').checked = false; load(true); });
  setInterval(() => { if (el('auto').checked && !document.hidden) load(); }, 10000);
  load();
})();
