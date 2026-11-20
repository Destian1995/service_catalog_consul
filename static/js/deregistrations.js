(() => {
  'use strict';
  const el = id => document.getElementById(id);
  let next = null, busy = false, count = 0;
  let activeFilters = {node: '', q: '', source: '', outcome: ''}, pendingApply = null;
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
    if (event.actor) source.append(element('div', `Пользователь: ${event.actor}`, 'details'));
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
  function renderTracking(coverage) {
    const container = el('tracking');
    container.replaceChildren();
    const portal = element('div', '');
    portal.append(element('strong', 'Команды этого портала'));
    portal.append(element('p', coverage.portal_mode === 'live' ? 'Боевой режим · фиксируются отправки портала' : 'Тестовый режим · команды в Consul не отправляются'));
    const targets = coverage.portal_targets || [];
    if (!targets.length) portal.append(element('p', 'Адреса Consul не настроены'));
    for (const target of targets) {
      portal.append(element('div', `${target.cluster} / ${target.datacenter || 'DC не указан'}`, 'details'));
      portal.append(element('code', target.endpoint));
    }
    portal.append(element('p', 'Это настроенные адреса, а не проверка доступности. Чужие запросы к ним автоматически не видны.'));
    container.append(portal);
    const files = element('div', '');
    files.append(element('strong', 'Внешние HTTP-запросы · файлы логов'));
    if (!coverage.audit_configured) files.append(element('p', 'Не подключены · видны только команды портала'));
    for (const pattern of coverage.audit_patterns || []) files.append(element('div', pattern, 'details'));
    for (const file of coverage.files || []) {
      files.append(element('div', `${!file.selected ? 'Не выбран текущими настройками' : file.present ? file.error ? 'Ошибка чтения' : 'Файл найден' : 'Файл отсутствует'}: ${file.path}`, 'details'));
    }
    files.append(element('p', 'Файл найден — это не подтверждение, что все запросы к серверам проходят через этот источник.'));
    container.append(files);
    const proxy = element('div', '');
    proxy.append(element('strong', 'Адреса из записей HTTP-прокси'));
    if (!(coverage.proxy_destinations || []).length) proxy.append(element('p', 'Пока нет записей · адрес назначения не установлен'));
    for (const target of coverage.proxy_destinations || []) {
      proxy.append(element('code', target.server));
      proxy.append(element('div', `Последняя запись: ${target.last_seen}`, 'details'));
    }
    proxy.append(element('p', 'Исторические адреса из журнала. Текущая конфигурация прокси и внутренний RPC здесь не проверяются.'));
    container.append(proxy);
  }
  function renderApplied(filters) {
    const container = el('applied-filters');
    container.replaceChildren();
    const sourceNames = {'': 'Все источники', portal: 'Этот портал', consul_audit: 'Аудит Consul', http_proxy: 'HTTP-прокси Consul'};
    for (const text of [filters.node ? `Узел: ${filters.node} · часть имени` : 'Узел: все',
      filters.q ? `Поиск: ${filters.q}` : 'Поиск: без ограничений',
      `Источник: ${sourceNames[filters.source] || filters.source}`,
      `Результат: ${results[filters.outcome] || 'Все результаты'}`]) {
      container.append(element('span', text, 'badge'));
    }
  }
  async function load(append = false, filters = activeFilters) {
    if (busy) return;
    busy = true; el('refresh').disabled = true; el('more').disabled = true;
    el('error').textContent = '';
    try {
      const params = new URLSearchParams({...filters, limit: '100'});
      if (append && next) params.set('before', next);
      const response = await fetch(`/api/deregistrations?${params}`, {cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      activeFilters = {...filters};
      renderApplied(activeFilters);
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
      renderTracking(coverage);
      const connected = coverage.audit_configured && coverage.audit_files_found > 0;
      const errors = coverage.files.some(file => file.selected && file.present && file.error);
      el('coverage').classList.toggle('connected', connected && !errors);
      el('coverage-title').textContent = !coverage.audit_configured ? 'Серверный аудит не подключён' : errors ? 'Есть ошибки чтения серверного аудита' : connected ? 'Подключены файлы серверного аудита' : 'Файлы серверного аудита не найдены';
      el('coverage-text').textContent = !coverage.audit_configured ? 'Сейчас фиксируются только команды дерегистрации, отправленные этим порталом. Запросы от других клиентов не видны.' : `Найдено файлов: ${coverage.audit_files_found}. Охват зависит от подключённых Consul HTTP audit-логов. Внутренние RPC не записываются.`;
      el('sources').textContent = coverage.files.map(file => `${file.path}: прочитано ${file.offset} байт, некорректных строк ${file.invalid_lines}; ${file.error || 'ошибок чтения нет'}; последнее чтение ${file.last_read}`).join('\n');
      el('updated').textContent = `Обновлено ${new Date().toLocaleTimeString('ru-RU')}. Время событий указано в UTC.`;
    } catch (error) {
      el('error').textContent = error.message || 'Не удалось загрузить журнал';
      el('coverage').classList.remove('connected');
      el('coverage-title').textContent = 'Не удалось проверить источники журнала';
      el('tracking').replaceChildren(element('p', 'Текущая привязка неизвестна: не удалось загрузить данные.'));
    } finally {
      busy = false; el('refresh').disabled = false; el('more').disabled = false;
      if (pendingApply) { const filters = pendingApply; pendingApply = null; load(false, filters); }
    }
  }
  el('filters').addEventListener('submit', event => {
    event.preventDefault();
    const filters = {node: el('node').value.trim(), q: el('query').value.trim(), source: el('source').value, outcome: el('outcome').value};
    if (busy) pendingApply = filters;
    else load(false, filters);
  });
  el('refresh').addEventListener('click', () => load());
  el('more').addEventListener('click', () => { el('auto').checked = false; load(true); });
  setInterval(() => { if (el('auto').checked && !document.hidden) load(); }, 10000);
  load();
})();
