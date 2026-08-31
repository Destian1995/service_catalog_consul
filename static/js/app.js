/* ═══════════════════════════════════════════════
   Каталог сервисов — SPA
   Таблицы с раскрывающимися строками
   ═══════════════════════════════════════════════ */

const API = '';
let currentView = 'dashboard';
const _drillCache = {};
const LOADER = `<div class="loading-spinner"><div class="loader-ring"><svg class="loader-svg" viewBox="0 0 64 64"><circle class="loader-track" cx="32" cy="32" r="28"/><circle class="loader-arc" cx="32" cy="32" r="28"/><path class="loader-pulse-bg" d="M16 32h6l3-8 7 16 3-8h13"/><path class="loader-pulse" d="M16 32h6l3-8 7 16 3-8h13"/></svg></div></div>`;

// ─── Helpers ───

async function api(path) {
    const res = await fetch(`${API}${path}`);
    return res.json();
}

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function getTagClass(tag) {
    const map = { web: 'tag-web', proxy: 'tag-web', api: 'tag-api', gateway: 'tag-api',
        auth: 'tag-api', security: 'tag-api', database: 'tag-database', sql: 'tag-database',
        nosql: 'tag-database', cache: 'tag-cache', 'in-memory': 'tag-cache', mq: 'tag-mq',
        amqp: 'tag-mq', streaming: 'tag-mq', monitoring: 'tag-monitoring', metrics: 'tag-monitoring',
        dashboards: 'tag-monitoring', logs: 'tag-monitoring', alerts: 'tag-monitoring',
        infra: 'tag-infra', 'service-discovery': 'tag-infra', exporter: 'tag-monitoring' };
    return map[tag] || '';
}

function statusIcon(status) {
    if (status === 'passing')
        return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>`;
    if (status === 'warning')
        return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 9v4M12 17h.01"/></svg>`;
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 6L6 18M6 6l12 12"/></svg>`;
}

function chevronIcon() {
    return `<svg class="expand-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>`;
}

function plural(n, one, few, many) {
    const abs = Math.abs(n) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (last > 1 && last < 5) return few;
    if (last === 1) return one;
    return many;
}

// ─── Навигация ───

function navigate(view) {
    $$('.nav-item').forEach(n => n.classList.remove('active'));
    const navItem = $(`.nav-item[data-view="${view}"]`);
    if (navItem) navItem.classList.add('active');

    $$('.view').forEach(v => v.classList.remove('active'));
    currentView = view;

    if (view === 'dashboard') renderDashboard();
    else if (view === 'servers') renderServers();
    else if (view === 'services') renderServices();
    else if (view === 'analytics') renderAnalytics();

    const targetView = $(`#view-${view}`);
    if (targetView) targetView.classList.add('active');

    if (window.innerWidth <= 768) {
        $('#sidebar').classList.remove('open');
    }
}

function toggleSidebar() {
    const sb = $('#sidebar');
    if (window.innerWidth <= 768) {
        sb.classList.toggle('open');
    } else {
        sb.classList.toggle('collapsed');
        document.body.classList.toggle('sidebar-collapsed');
    }
}

function onGlobalSearch(val) {
    if (currentView === 'servers') applyServerFilters();
    else if (currentView === 'services') applyServiceFilters();
}

async function refreshData() {
    // Clear drill-down cache on manual refresh
    Object.keys(_drillCache).forEach(k => delete _drillCache[k]);
    navigate(currentView);
}

// ═══════════════════════════════════════
// Обзор (Dashboard)
// ═══════════════════════════════════════

async function renderDashboard() {
    const el = $('#view-dashboard');
    el.innerHTML = LOADER;

    const [summary, nodes, services, analytics] = await Promise.all([
        api('/api/health/summary'),
        api('/api/nodes'),
        api('/api/services'),
        api('/api/analytics'),
    ]);
    const isMon = analytics.is_monitoring || {};

    const dcs = {};
    nodes.forEach(n => {
        const dc = n.Datacenter;
        if (!dcs[dc]) dcs[dc] = { total: 0, envs: {} };
        dcs[dc].total++;
        const env = n.Meta.environment || 'unknown';
        dcs[dc].envs[env] = (dcs[dc].envs[env] || 0) + 1;
    });

    el.innerHTML = `
        <h2 class="page-title">Обзор</h2>
        <div class="stats-grid">
            <div class="stat-card accent clickable" onclick="drillDown({})">
                <div class="stat-label">Всего серверов</div>
                <div class="stat-value">${summary.total_nodes}</div>
            </div>
            <div class="stat-card accent clickable" onclick="navigate('analytics')">
                <div class="stat-label">ИС на мониторинге</div>
                <div class="stat-value">${isMon.monitored_is || 0}<span style="font-size:16px;color:var(--text-muted);font-weight:400"> / ${isMon.total_is || 0}</span></div>
            </div>
            <div class="stat-card passing clickable" onclick="drillDown({status:'passing'})">
                <div class="stat-label">Проверки ОК</div>
                <div class="stat-value">${summary.passing}</div>
            </div>
            <div class="stat-card warning clickable" onclick="drillDown({status:'warning'})">
                <div class="stat-label">Предупреждения</div>
                <div class="stat-value">${summary.warning}</div>
            </div>
            <div class="stat-card critical clickable" onclick="drillDown({status:'critical'})">
                <div class="stat-label">Ошибки</div>
                <div class="stat-value">${summary.critical}</div>
            </div>
        </div>

        <div class="section-title">Серверы по дата-центрам</div>
        <div class="stats-grid" style="margin-bottom:32px">
            ${Object.entries(dcs).map(([dc, data]) => {
                const dcSafe = btoa(unescape(encodeURIComponent(JSON.stringify({dc}))));
                return `
                <div class="stat-card accent clickable" onclick="drillDownB64('${dcSafe}')">
                    <div class="stat-label">${dc}</div>
                    <div class="stat-value">${data.total}</div>
                    <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
                        ${Object.entries(data.envs).map(([env, cnt]) =>
                            '<span class="tag">' + env + ': ' + cnt + '</span>').join('')}
                    </div>
                </div>`;
            }).join('')}
        </div>

        <!-- Drill-down panel -->
        <div id="drillDownPanel" style="display:none"></div>

        <div class="section-title">Обзор сервисов</div>
        <div class="table-wrapper">
            <table class="data-table">
                <thead>
                    <tr>
                        <th>Сервис</th>
                        <th>Экземпляры</th>
                        <th>Порты</th>
                        <th>Теги</th>
                    </tr>
                </thead>
                <tbody>
                    ${services.map(s => `
                        <tr onclick="navigate('services')" style="cursor:pointer">
                            <td class="cell-name">${s.name}</td>
                            <td class="cell-mono">${s.instances}</td>
                            <td class="cell-mono">${s.ports.join(', ')}</td>
                            <td>${s.tags.slice(0, 4).map(t => `<span class="tag ${getTagClass(t)}">${t}</span>`).join(' ')}${s.tags.length > 4 ? ` <span class="tag">+${s.tags.length - 4}</span>` : ''}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
    `;
}

// ═══════════════════════════════════════
// Dashboard Drill-Down (with client cache)
// ═══════════════════════════════════════

function drillDownB64(b64) {
    const filters = JSON.parse(decodeURIComponent(escape(atob(b64))));
    drillDown(filters);
}

async function drillDown(filters) {
    const panel = document.getElementById('drillDownPanel');
    if (!panel) return;

    const key = JSON.stringify(filters);

    // Toggle off if same filter clicked again
    if (panel.style.display !== 'none' && panel.dataset.filter === key) {
        panel.style.display = 'none';
        return;
    }
    panel.dataset.filter = key;
    panel.style.display = 'block';

    let data;
    if (_drillCache[key]) {
        data = _drillCache[key];
    } else {
        panel.innerHTML = LOADER;
        panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        let url = '/api/health/details?';
        if (filters.dc) url += `dc=${encodeURIComponent(filters.dc)}&`;
        if (filters.status) url += `status=${encodeURIComponent(filters.status)}&`;
        if (filters.system_name) url += `system_name=${encodeURIComponent(filters.system_name)}&`;
        data = await api(url);
        _drillCache[key] = data;
    }

    // Build breadcrumbs
    const statusNames = { passing: 'Норма', warning: 'Предупреждения', critical: 'Ошибки' };
    const crumbs = [];
    // Parent crumb (without system_name) — allows going back
    const parentFilters = {};
    if (filters.dc) parentFilters.dc = filters.dc;
    if (filters.status) parentFilters.status = filters.status;

    if (filters.status) {
        const parentB64 = btoa(unescape(encodeURIComponent(JSON.stringify({status: filters.status}))));
        const label = statusNames[filters.status] || filters.status;
        if (filters.system_name || filters.dc) {
            crumbs.push(`<span class="breadcrumb-link" onclick="drillDownB64('${parentB64}')">${label}</span>`);
        } else {
            crumbs.push(`<span class="breadcrumb-current">${label}</span>`);
        }
    }
    if (filters.dc) {
        const dcOnly = Object.assign({}, parentFilters);
        delete dcOnly.system_name;
        const dcB64 = btoa(unescape(encodeURIComponent(JSON.stringify(dcOnly))));
        if (filters.system_name) {
            crumbs.push(`<span class="breadcrumb-link" onclick="drillDownB64('${dcB64}')">ДЦ: ${filters.dc}</span>`);
        } else {
            crumbs.push(`<span class="breadcrumb-current">ДЦ: ${filters.dc}</span>`);
        }
    }
    if (filters.system_name) {
        crumbs.push(`<span class="breadcrumb-current">ИС: ${filters.system_name}</span>`);
    }
    if (crumbs.length === 0) crumbs.push(`<span class="breadcrumb-current">Все серверы</span>`);
    const breadcrumbHtml = crumbs.join('<span class="breadcrumb-sep">/</span>');

    // Collect IS stats
    const isStat = {};
    let totalChecks = 0;
    data.forEach(d => {
        const is = d.system_name || '-';
        if (!isStat[is]) isStat[is] = { servers: 0, checks: 0, passing: 0, warning: 0, critical: 0 };
        isStat[is].servers++;
        d.checks.forEach(c => {
            isStat[is].checks++;
            isStat[is][c.Status]++;
            totalChecks++;
        });
    });

    // Filters for drill-down sub-filter
    const allIS = [...new Set(data.map(d => d.system_name).filter(s => s && s !== '-'))].sort();

    panel.innerHTML = `
        <div class="drilldown-container">
            <div class="drilldown-header">
                <div class="drilldown-title">${breadcrumbHtml}</div>
                <div class="drilldown-summary">
                    ${data.length} ${plural(data.length, 'сервер', 'сервера', 'серверов')}, ${totalChecks} ${plural(totalChecks, 'проверка', 'проверки', 'проверок')}
                </div>
                <button class="btn-icon" onclick="document.getElementById('drillDownPanel').style.display='none'" title="Закрыть" style="margin-left:auto">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
                </button>
            </div>

            ${allIS.length > 1 ? `
            <div class="drilldown-is-filter">
                <span class="filter-label">ИС</span>
                ${allIS.map(is => {
                    const merged = Object.assign({}, filters, {system_name: is});
                    const safe = btoa(unescape(encodeURIComponent(JSON.stringify(merged))));
                    return `<button class="filter-chip" onclick="drillDownB64('${safe}')">${is}</button>`;
                }).join('')}
            </div>` : ''}

            ${Object.keys(isStat).length > 1 ? `
            <div class="drilldown-is-cards">
                ${Object.entries(isStat).sort((a,b) => b[1].servers - a[1].servers).map(([is, st]) => {
                    const merged = Object.assign({}, filters, {system_name: is});
                    const safe = btoa(unescape(encodeURIComponent(JSON.stringify(merged))));
                    return `
                    <div class="drilldown-is-card clickable" onclick="drillDownB64('${safe}')">
                        <div class="drilldown-is-name">${is}</div>
                        <div class="drilldown-is-stats">
                            <span>${st.servers} ${plural(st.servers, 'сервер', 'сервера', 'серверов')}</span>
                            ${st.passing > 0 ? '<span class="inst-badge inst-passing">' + st.passing + '</span>' : ''}
                            ${st.warning > 0 ? '<span class="inst-badge inst-warning">' + st.warning + '</span>' : ''}
                            ${st.critical > 0 ? '<span class="inst-badge inst-critical">' + st.critical + '</span>' : ''}
                        </div>
                    </div>`;
                }).join('')}
            </div>` : ''}

            <div class="table-wrapper" style="margin-top:12px">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th style="width:32px"></th>
                            <th>Статус</th>
                            <th>Сервер</th>
                            <th>ИС</th>
                            <th>IP</th>
                            <th>ДЦ</th>
                            <th>Среда</th>
                            <th>Проверки</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.map((d, i) => {
                            const worst = d.checks.some(c => c.Status === 'critical') ? 'critical'
                                        : d.checks.some(c => c.Status === 'warning') ? 'warning' : 'passing';
                            const ddRowId = `dd-row-${i}`;
                            return `
                            <tr class="expandable-row" onclick="toggleRow('${ddRowId}', this)">
                                <td>${chevronIcon()}</td>
                                <td><span class="status-dot status-${worst}"></span></td>
                                <td class="cell-name">${d.node}</td>
                                <td><span class="badge badge-system">${d.system_name}</span></td>
                                <td class="cell-mono">${d.address}</td>
                                <td><span class="badge badge-dc">${d.datacenter}</span></td>
                                <td><span class="badge badge-env">${d.environment}</span></td>
                                <td class="cell-mono">${d.checks.length}</td>
                            </tr>
                            <tr class="expand-content" id="${ddRowId}">
                                <td colspan="8">
                                    <div class="expand-body">
                                        ${d.checks.map(c => `
                                            <div class="check-item">
                                                <div class="check-status ${c.Status}">${statusIcon(c.Status)}</div>
                                                <div class="check-body">
                                                    <div class="check-name">${c.Name}${c.ServiceName ? ` <span style="color:var(--text-muted);font-weight:400;font-size:12px">(${c.ServiceName})</span>` : ''}</div>
                                                    <div class="check-output">${c.Output || '-'}</div>
                                                </div>
                                            </div>
                                        `).join('')}
                                    </div>
                                </td>
                            </tr>`;
                        }).join('')}
                    </tbody>
                </table>
            </div>
        </div>
    `;
}

// ═══════════════════════════════════════
// Серверы — Таблица с раскрывающимися строками
// ═══════════════════════════════════════

async function renderServers() {
    const el = $('#view-servers');
    el.innerHTML = LOADER;

    const [dcs, envs, teams, systems] = await Promise.all([
        api('/api/datacenters'),
        api('/api/environments'),
        api('/api/teams'),
        api('/api/systems'),
    ]);

    const globalSearch = $('#globalSearch').value || '';

    el.innerHTML = `
        <h2 class="page-title">Серверы</h2>
        <div class="filter-bar">
            <div class="filter-group">
                <span class="filter-label">ИС</span>
                <select class="filter-select" id="filterSystem" onchange="applyServerFilters()">
                    <option value="">Все</option>
                    ${systems.map(s => `<option value="${s}">${s}</option>`).join('')}
                </select>
            </div>
            <div class="filter-group">
                <span class="filter-label">ДЦ</span>
                <select class="filter-select" id="filterDc" onchange="applyServerFilters()">
                    <option value="">Все</option>
                    ${dcs.map(d => `<option value="${d}">${d}</option>`).join('')}
                </select>
            </div>
            <div class="filter-group">
                <span class="filter-label">Среда</span>
                <select class="filter-select" id="filterEnv" onchange="applyServerFilters()">
                    <option value="">Все</option>
                    ${envs.map(e => `<option value="${e}">${e}</option>`).join('')}
                </select>
            </div>
            <div class="filter-group">
                <span class="filter-label">Команда</span>
                <select class="filter-select" id="filterTeam" onchange="applyServerFilters()">
                    <option value="">Все</option>
                    ${teams.map(t => `<option value="${t}">${t}</option>`).join('')}
                </select>
            </div>
            <input class="filter-search" id="filterServerSearch" placeholder="Поиск серверов..."
                   value="${globalSearch}" oninput="applyServerFilters()">
            <button class="btn-reset" onclick="resetServerFilters()">Сбросить</button>
        </div>
        <div class="table-wrapper" id="serversContainer"></div>
    `;
    applyServerFilters();
}

async function applyServerFilters() {
    const container = document.getElementById('serversContainer');
    if (!container) return;
    container.innerHTML = LOADER;

    const dc = document.getElementById('filterDc')?.value || '';
    const env = document.getElementById('filterEnv')?.value || '';
    const team = document.getElementById('filterTeam')?.value || '';
    const systemName = document.getElementById('filterSystem')?.value || '';
    const search = document.getElementById('filterServerSearch')?.value || '';

    let url = '/api/nodes?';
    if (dc) url += `dc=${encodeURIComponent(dc)}&`;
    if (env) url += `env=${encodeURIComponent(env)}&`;
    if (team) url += `team=${encodeURIComponent(team)}&`;
    if (systemName) url += `system_name=${encodeURIComponent(systemName)}&`;
    if (search) url += `search=${encodeURIComponent(search)}&`;

    const nodes = await api(url);

    if (nodes.length === 0) {
        container.innerHTML = `<div class="empty-state"><p>Серверы не найдены</p></div>`;
        return;
    }

    const allDetails = await Promise.all(nodes.map(n => api(`/api/nodes/${n.Node}`)));

    container.innerHTML = `
        <table class="data-table">
            <thead>
                <tr>
                    <th style="width:32px"></th>
                    <th>Статус</th>
                    <th>Сервер</th>
                    <th>ИС</th>
                    <th>IP-адрес</th>
                    <th>Дата-центр</th>
                    <th>Среда</th>
                    <th>ОС</th>
                    <th>Сервисы</th>
                </tr>
            </thead>
            <tbody>
                ${allDetails.map((detail, i) => {
                    const node = nodes[i];
                    const services = detail.services || [];
                    const checks = detail.checks || [];
                    const critCount = checks.filter(c => c.Status === 'critical').length;
                    const warnCount = checks.filter(c => c.Status === 'warning').length;
                    const passCount = checks.filter(c => c.Status === 'passing').length;
                    let overallStatus = 'passing';
                    if (critCount > 0) overallStatus = 'critical';
                    else if (warnCount > 0) overallStatus = 'warning';
                    const rowId = `server-row-${i}`;

                    return `
                        <tr class="expandable-row" onclick="toggleRow('${rowId}', this)">
                            <td>${chevronIcon()}</td>
                            <td><span class="status-dot status-${overallStatus} ${overallStatus === 'critical' ? 'status-critical-pulse' : ''}"></span></td>
                            <td class="cell-name">${node.Node}</td>
                            <td><span class="badge badge-system">${node.Meta.system_name || '-'}</span></td>
                            <td class="cell-mono">${node.Address}</td>
                            <td><span class="badge badge-dc">${node.Datacenter}</span></td>
                            <td><span class="badge badge-env">${node.Meta.environment}</span></td>
                            <td class="cell-muted">${node.Meta.os}</td>
                            <td class="cell-mono">${services.length}</td>
                        </tr>
                        <tr class="expand-content" id="${rowId}">
                            <td colspan="9">
                                <div class="expand-body">
                                    <div class="expand-tabs">
                                        <button class="tab active" onclick="event.stopPropagation(); switchExpandTab(this, '${rowId}-overview')">Обзор</button>
                                        <button class="tab" onclick="event.stopPropagation(); switchExpandTab(this, '${rowId}-services')">Сервисы <span class="tab-count">${services.length}</span></button>
                                        <button class="tab" onclick="event.stopPropagation(); switchExpandTab(this, '${rowId}-health')">Проверки <span class="tab-count">${checks.length}</span></button>
                                        <button class="tab" onclick="event.stopPropagation(); switchExpandTab(this, '${rowId}-network')">Сеть</button>
                                    </div>

                                    <div class="expand-panel active" id="${rowId}-overview">
                                        <div class="info-grid">
                                            <div class="info-card">
                                                <div class="info-card-title">Система</div>
                                                <div class="info-row"><span class="info-key">ОС</span><span class="info-value">${node.Meta.os}</span></div>
                                                <div class="info-row"><span class="info-key">Ядро</span><span class="info-value">${node.Meta.kernel}</span></div>
                                                <div class="info-row"><span class="info-key">CPU</span><span class="info-value">${node.Meta.cpu}</span></div>
                                                <div class="info-row"><span class="info-key">RAM</span><span class="info-value">${node.Meta.ram}</span></div>
                                                <div class="info-row"><span class="info-key">Диск</span><span class="info-value">${node.Meta.disk}</span></div>
                                            </div>
                                            <div class="info-card">
                                                <div class="info-card-title">Организация</div>
                                                <div class="info-row"><span class="info-key">Среда</span><span class="info-value">${node.Meta.environment}</span></div>
                                                <div class="info-row"><span class="info-key">Команда</span><span class="info-value">${node.Meta.team}</span></div>
                                                <div class="info-row"><span class="info-key">Дата-центр</span><span class="info-value">${node.Datacenter}</span></div>
                                                <div class="info-row"><span class="info-key">ИС</span><span class="info-value">${node.Meta.system_name || '-'}</span></div>
                                                <div class="info-row"><span class="info-key">ID узла</span><span class="info-value">${node.ID}</span></div>
                                            </div>
                                            <div class="info-card">
                                                <div class="info-card-title">Состояние</div>
                                                <div class="info-row"><span class="info-key">Норма</span><span class="info-value" style="color:var(--passing)">${passCount}</span></div>
                                                <div class="info-row"><span class="info-key">Предупр.</span><span class="info-value" style="color:var(--warning)">${warnCount}</span></div>
                                                <div class="info-row"><span class="info-key">Критич.</span><span class="info-value" style="color:var(--critical)">${critCount}</span></div>
                                            </div>
                                        </div>
                                    </div>

                                    <div class="expand-panel" id="${rowId}-services">
                                        ${services.length === 0 ? '<div class="empty-state"><p>Нет сервисов</p></div>' :
                                        `<table class="data-table nested-table">
                                            <thead><tr><th>Статус</th><th>Сервис</th><th>Порт</th><th>Версия</th><th>Теги</th></tr></thead>
                                            <tbody>
                                                ${services.map(svc => {
                                                    const svcChecks = checks.filter(c => c.ServiceName === svc.Service);
                                                    let svcStatus = 'passing';
                                                    if (svcChecks.some(c => c.Status === 'critical')) svcStatus = 'critical';
                                                    else if (svcChecks.some(c => c.Status === 'warning')) svcStatus = 'warning';
                                                    return `<tr>
                                                            <td><span class="status-dot status-${svcStatus}"></span></td>
                                                            <td class="cell-name">${svc.Service}</td>
                                                            <td><span class="port-badge">:${svc.Port}</span></td>
                                                            <td class="cell-mono">${svc.Meta?.version || '-'}</td>
                                                            <td>${svc.Tags.slice(0, 5).map(t => `<span class="tag ${getTagClass(t)}">${t}</span>`).join(' ')}</td>
                                                        </tr>`;
                                                }).join('')}
                                            </tbody>
                                        </table>`}
                                    </div>

                                    <div class="expand-panel" id="${rowId}-health">
                                        ${checks.map(c => `
                                            <div class="check-item">
                                                <div class="check-status ${c.Status}">${statusIcon(c.Status)}</div>
                                                <div class="check-body">
                                                    <div class="check-name">${c.Name}</div>
                                                    <div class="check-output">${c.Output}</div>
                                                </div>
                                            </div>
                                        `).join('')}
                                    </div>

                                    <div class="expand-panel" id="${rowId}-network">
                                        <div class="info-grid">
                                            <div class="info-card">
                                                <div class="info-card-title">Адреса</div>
                                                <div class="info-row"><span class="info-key">LAN</span><span class="info-value">${node.TaggedAddresses?.lan || node.Address}</span></div>
                                                <div class="info-row"><span class="info-key">WAN</span><span class="info-value">${node.TaggedAddresses?.wan || '-'}</span></div>
                                                <div class="info-row"><span class="info-key">Основной</span><span class="info-value">${node.Address}</span></div>
                                            </div>
                                            <div class="info-card">
                                                <div class="info-card-title">Открытые порты</div>
                                                ${services.map(svc => `
                                                    <div class="info-row"><span class="info-key">${svc.Service}</span><span class="info-value">:${svc.Port}</span></div>
                                                `).join('')}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </td>
                        </tr>`;
                }).join('')}
            </tbody>
        </table>
    `;
}

function resetServerFilters() {
    ['filterSystem', 'filterDc', 'filterEnv', 'filterTeam', 'filterServerSearch'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    applyServerFilters();
}

// ═══════════════════════════════════════
// Сервисы — Таблица с раскрывающимися строками
// ═══════════════════════════════════════

async function renderServices() {
    const el = $('#view-services');
    el.innerHTML = LOADER;

    const [tags, dcs] = await Promise.all([
        api('/api/tags'),
        api('/api/datacenters'),
    ]);

    const globalSearch = $('#globalSearch').value || '';

    el.innerHTML = `
        <h2 class="page-title">Сервисы</h2>
        <div class="filter-bar">
            <div class="filter-group">
                <span class="filter-label">ДЦ</span>
                <select class="filter-select" id="filterSvcDc" onchange="applyServiceFilters()">
                    <option value="">Все</option>
                    ${dcs.map(d => `<option value="${d}">${d}</option>`).join('')}
                </select>
            </div>
            <div class="filter-group">
                <span class="filter-label">Тег</span>
                <select class="filter-select" id="filterSvcTag" onchange="applyServiceFilters()">
                    <option value="">Все</option>
                    ${tags.map(t => `<option value="${t}">${t}</option>`).join('')}
                </select>
            </div>
            <input class="filter-search" id="filterSvcSearch" placeholder="Поиск сервисов..."
                   value="${globalSearch}" oninput="applyServiceFilters()">
            <button class="btn-reset" onclick="resetServiceFilters()">Сбросить</button>
        </div>
        <div class="table-wrapper" id="servicesContainer"></div>
    `;
    applyServiceFilters();
}

async function applyServiceFilters() {
    const container = document.getElementById('servicesContainer');
    if (!container) return;
    container.innerHTML = LOADER;

    const dc = document.getElementById('filterSvcDc')?.value || '';
    const tag = document.getElementById('filterSvcTag')?.value || '';
    const search = document.getElementById('filterSvcSearch')?.value || '';

    let url = '/api/services?';
    if (dc) url += `dc=${encodeURIComponent(dc)}&`;
    if (tag) url += `tag=${encodeURIComponent(tag)}&`;
    if (search) url += `search=${encodeURIComponent(search)}&`;

    const services = await api(url);

    if (services.length === 0) {
        container.innerHTML = `<div class="empty-state"><p>Сервисы не найдены</p></div>`;
        return;
    }

    const allInstances = await Promise.all(services.map(s => api(`/api/services/${s.name}`)));

    container.innerHTML = `
        <table class="data-table">
            <thead>
                <tr>
                    <th style="width:32px"></th>
                    <th>Сервис</th>
                    <th>Экземпляры</th>
                    <th>Порты</th>
                    <th>Теги</th>
                </tr>
            </thead>
            <tbody>
                ${services.map((svc, i) => {
                    const instances = allInstances[i] || [];
                    const passing = instances.filter(inst => inst.status === 'passing').length;
                    const warning = instances.filter(inst => inst.status === 'warning').length;
                    const critical = instances.filter(inst => inst.status === 'critical').length;
                    const allTags = [...new Set(instances.flatMap(inst => inst.service.Tags))];
                    const meta = instances[0]?.service.Meta || {};
                    const rowId = `svc-row-${i}`;

                    return `
                        <tr class="expandable-row" onclick="toggleRow('${rowId}', this)">
                            <td>${chevronIcon()}</td>
                            <td class="cell-name">${svc.name}</td>
                            <td>
                                <div class="instance-counts">
                                    <span class="inst-total">${svc.instances}</span>
                                    ${passing > 0 ? `<span class="inst-badge inst-passing">${passing}</span>` : ''}
                                    ${warning > 0 ? `<span class="inst-badge inst-warning">${warning}</span>` : ''}
                                    ${critical > 0 ? `<span class="inst-badge inst-critical">${critical}</span>` : ''}
                                </div>
                            </td>
                            <td class="cell-mono">${svc.ports.join(', ')}</td>
                            <td>${svc.tags.slice(0, 4).map(t => `<span class="tag ${getTagClass(t)}">${t}</span>`).join(' ')}${svc.tags.length > 4 ? ` <span class="tag">+${svc.tags.length - 4}</span>` : ''}</td>
                        </tr>
                        <tr class="expand-content" id="${rowId}">
                            <td colspan="5">
                                <div class="expand-body">
                                    <div class="expand-tabs">
                                        <button class="tab active" onclick="event.stopPropagation(); switchExpandTab(this, '${rowId}-instances')">Экземпляры <span class="tab-count">${instances.length}</span></button>
                                        <button class="tab" onclick="event.stopPropagation(); switchExpandTab(this, '${rowId}-health')">Проверки</button>
                                        <button class="tab" onclick="event.stopPropagation(); switchExpandTab(this, '${rowId}-meta')">Метаданные</button>
                                    </div>

                                    <div class="expand-panel active" id="${rowId}-instances">
                                        <table class="data-table nested-table">
                                            <thead><tr>
                                                <th style="width:28px"></th><th>Статус</th><th>Сервер</th><th>IP</th><th>Дата-центр</th><th>Среда</th><th>Порт</th>
                                            </tr></thead>
                                            <tbody>
                                                ${instances.map((inst, j) => {
                                                    const hostId = `${rowId}-host-${j}`;
                                                    return `
                                                    <tr class="expandable-row nested-expandable" onclick="event.stopPropagation(); toggleHostRow('${hostId}', this, '${inst.node.Node}')">
                                                        <td>${chevronIcon()}</td>
                                                        <td><span class="status-dot status-${inst.status}"></span></td>
                                                        <td class="cell-name">${inst.node.Node}</td>
                                                        <td class="cell-mono">${inst.node.Address}</td>
                                                        <td><span class="badge badge-dc">${inst.node.Datacenter}</span></td>
                                                        <td><span class="badge badge-env">${inst.node.Meta.environment}</span></td>
                                                        <td><span class="port-badge">:${inst.service.Port}</span></td>
                                                    </tr>
                                                    <tr class="expand-content nested-expand" id="${hostId}">
                                                        <td colspan="7">
                                                            <div class="expand-body nested-body" id="${hostId}-body">
                                                                ${LOADER}
                                                            </div>
                                                        </td>
                                                    </tr>`;
                                                }).join('')}
                                            </tbody>
                                        </table>
                                    </div>

                                    <div class="expand-panel" id="${rowId}-health">
                                        <div class="health-summary-row">
                                            <span class="health-pill passing">${passing} норма</span>
                                            <span class="health-pill warning">${warning} предупр.</span>
                                            <span class="health-pill critical">${critical} критич.</span>
                                        </div>
                                        ${instances.flatMap(inst => inst.checks.map(c => `
                                            <div class="check-item">
                                                <div class="check-status ${c.Status}">${statusIcon(c.Status)}</div>
                                                <div class="check-body">
                                                    <div class="check-name">${c.Name} <span style="color:var(--text-muted);font-weight:400;font-size:12px">на ${inst.node.Node}</span></div>
                                                    <div class="check-output">${c.Output}</div>
                                                </div>
                                            </div>
                                        `)).join('')}
                                    </div>

                                    <div class="expand-panel" id="${rowId}-meta">
                                        <div class="info-grid">
                                            <div class="info-card">
                                                <div class="info-card-title">Метаданные сервиса</div>
                                                ${Object.entries(meta).map(([k, v]) =>
                                                    `<div class="info-row"><span class="info-key">${k}</span><span class="info-value">${v}</span></div>`
                                                ).join('')}
                                            </div>
                                            <div class="info-card">
                                                <div class="info-card-title">Все теги</div>
                                                <div style="display:flex;flex-wrap:wrap;gap:6px;padding:6px 0">
                                                    ${allTags.map(t => `<span class="tag ${getTagClass(t)}">${t}</span>`).join('')}
                                                </div>
                                            </div>
                                            <div class="info-card">
                                                <div class="info-card-title">Развёрнут на</div>
                                                ${instances.map(inst =>
                                                    `<div class="info-row"><span class="info-key">${inst.node.Node}</span><span class="info-value">${inst.node.Datacenter}</span></div>`
                                                ).join('')}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </td>
                        </tr>`;
                }).join('')}
            </tbody>
        </table>
    `;
}

function resetServiceFilters() {
    ['filterSvcDc', 'filterSvcTag', 'filterSvcSearch'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    applyServiceFilters();
}

// ═══════════════════════════════════════
// Аналитика — Графики
// ═══════════════════════════════════════

const CHART_COLORS = [
    '#6366f1', '#8b5cf6', '#a78bfa', '#c084fc',
    '#60a5fa', '#38bdf8', '#22d3ee', '#2dd4bf',
    '#34d399', '#4ade80', '#a3e635', '#facc15',
    '#fb923c', '#f87171', '#fb7185', '#e879f9',
];

async function renderAnalytics() {
    const el = $('#view-analytics');
    el.innerHTML = LOADER;

    const data = await api('/api/analytics');
    const mon = data.monitoring;

    el.innerHTML = `
        <h2 class="page-title">Аналитика</h2>

        <div class="section-title">Покрытие мониторингом</div>
        <div class="monitoring-panel">
            <div class="mon-hero">
                <div class="mon-hero-ring">
                    <canvas id="chart-mon-ring" width="180" height="180"></canvas>
                    <div class="mon-hero-center">
                        <span class="mon-hero-pct">${mon.coverage_pct}%</span>
                        <span class="mon-hero-label">покрытие</span>
                    </div>
                </div>
                <div class="mon-hero-stats">
                    <div class="mon-stat-big">
                        <span class="mon-stat-num">${mon.total}</span>
                        <span class="mon-stat-text">Всего серверов</span>
                    </div>
                    <div class="mon-stat-big">
                        <span class="mon-stat-num" style="color:var(--passing)">${mon.monitored}</span>
                        <span class="mon-stat-text">Под мониторингом</span>
                    </div>
                </div>
            </div>
            <div class="mon-levels">
                <div class="mon-level-card mon-full">
                    <div class="mon-level-header">
                        <div class="mon-level-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
                        </div>
                        <div>
                            <div class="mon-level-title">Полный мониторинг</div>
                            <div class="mon-level-desc">Метрики + Логи + Алерты + Дашборды</div>
                        </div>
                        <span class="mon-level-count">${mon.full}</span>
                    </div>
                    <div class="mon-level-bar"><div class="mon-level-fill mon-full-fill" style="width:${mon.total ? Math.round(mon.full / mon.total * 100) : 0}%"></div></div>
                    <div class="mon-level-servers">${mon.servers_full.map(s => `<span class="tag">${s}</span>`).join('')}</div>
                </div>
                <div class="mon-level-card mon-basic">
                    <div class="mon-level-header">
                        <div class="mon-level-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 20V10M12 20V4M6 20v-6"/></svg>
                        </div>
                        <div>
                            <div class="mon-level-title">Базовый мониторинг</div>
                            <div class="mon-level-desc">Только Node Exporter + Consul Agent</div>
                        </div>
                        <span class="mon-level-count">${mon.basic}</span>
                    </div>
                    <div class="mon-level-bar"><div class="mon-level-fill mon-basic-fill" style="width:${mon.total ? Math.round(mon.basic / mon.total * 100) : 0}%"></div></div>
                    <div class="mon-level-servers">${mon.servers_basic.map(s => `<span class="tag">${s}</span>`).join('')}</div>
                </div>
                ${mon.none > 0 ? `
                <div class="mon-level-card mon-none">
                    <div class="mon-level-header">
                        <div class="mon-level-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/></svg>
                        </div>
                        <div>
                            <div class="mon-level-title">Без мониторинга</div>
                            <div class="mon-level-desc">Агенты мониторинга не обнаружены</div>
                        </div>
                        <span class="mon-level-count">${mon.none}</span>
                    </div>
                    <div class="mon-level-bar"><div class="mon-level-fill mon-none-fill" style="width:${mon.total ? Math.round(mon.none / mon.total * 100) : 0}%"></div></div>
                    <div class="mon-level-servers">${mon.servers_none.map(s => `<span class="tag">${s}</span>`).join('')}</div>
                </div>` : ''}
            </div>
        </div>

        <div class="charts-grid" style="margin-top:24px">
            <div class="chart-card">
                <div class="chart-card-title">Мониторинг по дата-центрам</div>
                <canvas id="chart-mon-dc"></canvas>
            </div>
            <div class="chart-card">
                <div class="chart-card-title">Мониторинг по средам</div>
                <canvas id="chart-mon-env"></canvas>
            </div>
        </div>

        <div class="section-title" style="margin-top:32px">Информационные системы на мониторинге</div>
        <div class="monitoring-panel" style="margin-bottom:20px">
            <div class="mon-hero" style="margin-bottom:16px">
                <div class="mon-hero-stats">
                    <div class="mon-stat-big">
                        <span class="mon-stat-num" style="color:var(--accent-light)">${data.is_monitoring.total_is}</span>
                        <span class="mon-stat-text">Всего ИС</span>
                    </div>
                    <div class="mon-stat-big">
                        <span class="mon-stat-num" style="color:var(--passing)">${data.is_monitoring.monitored_is}</span>
                        <span class="mon-stat-text">На мониторинге</span>
                    </div>
                    <div class="mon-stat-big">
                        <span class="mon-stat-num" style="color:var(--warning)">${data.is_monitoring.total_is - data.is_monitoring.monitored_is}</span>
                        <span class="mon-stat-text">Без мониторинга</span>
                    </div>
                    <div class="mon-stat-big">
                        <span class="mon-stat-num" style="color:#c4b5fd">${data.is_monitoring.coverage_pct}%</span>
                        <span class="mon-stat-text">Покрытие ИС</span>
                    </div>
                </div>
            </div>
            <p style="font-size:12px;color:var(--text-muted);margin-bottom:12px">Нажмите на переключатель, чтобы отметить ИС как поставленную на мониторинг</p>
            <div class="systems-panel" id="systemsMonPanel">
                ${Object.entries(data.hosts_by_system).map(([sysName, info]) => {
                    if (sysName === 'Unassigned') return '';
                    const pct = Math.round(info.count / data.monitoring.total * 100);
                    const svcs = data.services_by_system[sysName] || [];
                    const checked = info.is_monitored;
                    const covered = info.all_covered;
                    return `
                    <div class="system-card ${checked ? 'system-monitored' : ''}" id="sys-card-${sysName.replace(/[^a-zA-Z0-9]/g, '_')}">
                        <div class="system-card-header">
                            <label class="mon-toggle" onclick="event.stopPropagation()">
                                <input type="checkbox" ${checked ? 'checked' : ''} onchange="toggleSystemMonitoring('${sysName.replace(/'/g, "\\'")}', this.checked)">
                                <span class="mon-toggle-slider"></span>
                            </label>
                            <div class="system-card-info">
                                <div class="system-card-name">${sysName}</div>
                                <div class="system-card-meta">
                                    ${info.datacenters.map(dc => '<span class="badge badge-dc">' + dc + '</span>').join('')}
                                    ${info.environments.map(e => '<span class="badge badge-env">' + e + '</span>').join('')}
                                    ${covered ? '<span class="badge badge-passing" style="font-size:10px">все хосты покрыты</span>' : ''}
                                </div>
                            </div>
                            <div class="system-card-count">
                                <span class="system-count-num">${info.count}</span>
                                <span class="system-count-label">${plural(info.count, 'хост', 'хоста', 'хостов')}</span>
                            </div>
                        </div>
                        <div class="system-card-bar">
                            <div class="system-card-fill" style="width:${pct}%"></div>
                        </div>
                        <div class="system-card-mon-stats">
                            ${info.mon_full > 0 ? '<span class="inst-badge inst-passing">полный: ' + info.mon_full + '</span>' : ''}
                            ${info.mon_basic > 0 ? '<span class="inst-badge inst-warning">базовый: ' + info.mon_basic + '</span>' : ''}
                            ${info.mon_none > 0 ? '<span class="inst-badge" style="background:rgba(100,116,139,0.1);border:1px solid rgba(100,116,139,0.2);color:var(--text-muted)">нет: ' + info.mon_none + '</span>' : ''}
                        </div>
                        <div class="system-card-details">
                            <div class="system-detail-group">
                                <span class="system-detail-label">Серверы</span>
                                <div class="system-detail-tags">${info.servers.map(s => '<span class="tag">' + s + '</span>').join('')}</div>
                            </div>
                            <div class="system-detail-group">
                                <span class="system-detail-label">Сервисы</span>
                                <div class="system-detail-tags">${svcs.map(s => '<span class="tag ' + getTagClass(s) + '">' + s + '</span>').join('')}</div>
                            </div>
                        </div>
                    </div>`;
                }).join('')}
            </div>
        </div>

        <div class="charts-grid" style="margin-top:24px">
            <div class="chart-card chart-card-wide">
                <div class="chart-card-title">Хосты по информационным системам</div>
                <canvas id="chart-hosts-system"></canvas>
            </div>
        </div>

        <div class="section-title" style="margin-top:32px">Разбивка инфраструктуры</div>
        <div class="charts-grid">
            <div class="chart-card chart-card-wide">
                <div class="chart-card-title">Экземпляры по сервисам</div>
                <canvas id="chart-instances-bar"></canvas>
            </div>

            <div class="chart-card">
                <div class="chart-card-title">Сервисы по категориям</div>
                <canvas id="chart-svc-cat"></canvas>
                <div class="chart-legend" id="legend-svc-cat"></div>
            </div>

            <div class="chart-card">
                <div class="chart-card-title">Статусы проверок</div>
                <canvas id="chart-health"></canvas>
                <div class="chart-legend" id="legend-health"></div>
            </div>

            <div class="chart-card">
                <div class="chart-card-title">Серверы по дата-центрам</div>
                <canvas id="chart-dc"></canvas>
                <div class="chart-legend" id="legend-dc"></div>
            </div>

            <div class="chart-card">
                <div class="chart-card-title">Серверы по средам</div>
                <canvas id="chart-env"></canvas>
                <div class="chart-legend" id="legend-env"></div>
            </div>

            <div class="chart-card">
                <div class="chart-card-title">Серверы по ОС</div>
                <canvas id="chart-os"></canvas>
                <div class="chart-legend" id="legend-os"></div>
            </div>

            <div class="chart-card">
                <div class="chart-card-title">Серверы по командам</div>
                <canvas id="chart-team"></canvas>
                <div class="chart-legend" id="legend-team"></div>
            </div>

            <div class="chart-card chart-card-wide">
                <div class="chart-card-title">Сервисы на сервере</div>
                <canvas id="chart-svcs-server"></canvas>
            </div>

            <div class="chart-card chart-card-wide">
                <div class="chart-card-title">Состояние по дата-центрам</div>
                <canvas id="chart-health-dc"></canvas>
            </div>
        </div>
    `;

    requestAnimationFrame(() => {
        drawMonitoringRing('chart-mon-ring', mon);
        drawMonitoringStackedBar('chart-mon-dc', data.monitoring_by_dc);
        drawMonitoringStackedBar('chart-mon-env', data.monitoring_by_env);
        const hostsBySystemCounts = {};
        Object.entries(data.hosts_by_system).forEach(([name, info]) => { hostsBySystemCounts[name] = info.count; });
        drawBarChart('chart-hosts-system', hostsBySystemCounts, ['#6366f1', '#8b5cf6', '#a78bfa', '#22d3ee', '#34d399']);
        drawDonut('chart-svc-cat', 'legend-svc-cat', data.services_by_category, CHART_COLORS);
        drawDonut('chart-health', 'legend-health', data.health_status, ['#10b981', '#f59e0b', '#ef4444']);
        drawDonut('chart-dc', 'legend-dc', data.servers_by_dc, CHART_COLORS);
        drawDonut('chart-env', 'legend-env', data.servers_by_env, ['#6366f1', '#22d3ee', '#facc15', '#fb7185']);
        drawDonut('chart-os', 'legend-os', data.servers_by_os, ['#60a5fa', '#34d399', '#fb923c', '#e879f9']);
        drawDonut('chart-team', 'legend-team', data.servers_by_team, CHART_COLORS.slice(2));
        drawBarChart('chart-instances-bar', data.instances_per_service, CHART_COLORS);
        drawBarChart('chart-svcs-server', data.services_per_server, CHART_COLORS.slice(4));
        drawStackedBar('chart-health-dc', data.health_by_dc);
    });
}

// ─── Кольцевая диаграмма мониторинга ───

function drawMonitoringRing(canvasId, mon) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const size = 180;
    canvas.width = size * dpr; canvas.height = size * dpr;
    canvas.style.width = size + 'px'; canvas.style.height = size + 'px';
    ctx.scale(dpr, dpr);
    const cx = size / 2, cy = size / 2, outerR = size / 2 - 6, innerR = outerR * 0.72;
    const total = mon.total || 1;
    const segments = [{ val: mon.full, color: '#10b981' }, { val: mon.basic, color: '#f59e0b' }, { val: mon.none, color: '#334155' }];
    ctx.beginPath(); ctx.arc(cx, cy, outerR, 0, Math.PI * 2); ctx.arc(cx, cy, innerR, Math.PI * 2, 0, true); ctx.closePath(); ctx.fillStyle = 'rgba(255,255,255,0.03)'; ctx.fill();
    let startAngle = -Math.PI / 2;
    segments.forEach(seg => { if (seg.val === 0) return; const a = (seg.val / total) * Math.PI * 2; ctx.beginPath(); ctx.arc(cx, cy, outerR, startAngle, startAngle + a); ctx.arc(cx, cy, innerR, startAngle + a, startAngle, true); ctx.closePath(); ctx.fillStyle = seg.color; ctx.fill(); startAngle += a; });
}

// ─── Stacked bar мониторинга ───

function drawMonitoringStackedBar(canvasId, dataObj) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const entries = Object.entries(dataObj);
    const barH = 36, gap = 14, labelW = 140, padTop = 8, padBottom = 30;
    const totalH = padTop + entries.length * (barH + gap) - gap + padBottom;
    const levelColors = { full: '#10b981', basic: '#f59e0b', none: '#334155' };
    const levelLabels = { full: 'Полный', basic: 'Базовый', none: 'Нет' };
    const parentW = canvas.parentElement.clientWidth - 40;
    canvas.width = parentW * dpr; canvas.height = totalH * dpr;
    canvas.style.width = parentW + 'px'; canvas.style.height = totalH + 'px';
    ctx.scale(dpr, dpr);
    const barArea = parentW - labelW - 20;
    entries.forEach(([label, levels], i) => {
        const y = padTop + i * (barH + gap);
        const total = Object.values(levels).reduce((s, v) => s + v, 0);
        if (total === 0) return;
        ctx.fillStyle = '#94a3b8'; ctx.font = "500 12px 'Inter', sans-serif"; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; ctx.fillText(label, labelW - 10, y + barH / 2);
        ctx.fillStyle = 'rgba(255,255,255,0.03)'; roundRect(ctx, labelW, y, barArea, barH, 6); ctx.fill();
        let x = labelW;
        ['full', 'basic', 'none'].forEach(level => { const val = levels[level] || 0; if (val === 0) return; const w = (val / total) * barArea; ctx.fillStyle = levelColors[level]; roundRect(ctx, x, y, w, barH, x === labelW ? 6 : 0); ctx.fill(); if (w > 28) { ctx.fillStyle = level === 'none' ? '#94a3b8' : '#fff'; ctx.font = "700 12px 'JetBrains Mono', monospace"; ctx.textAlign = 'center'; ctx.fillText(val, x + w / 2, y + barH / 2); } x += w; });
    });
    const ly = totalH - 18; let lx = labelW;
    ['full', 'basic', 'none'].forEach(level => { ctx.fillStyle = levelColors[level]; roundRect(ctx, lx, ly, 12, 12, 3); ctx.fill(); ctx.fillStyle = '#94a3b8'; ctx.font = "500 11px 'Inter', sans-serif"; ctx.textAlign = 'left'; ctx.fillText(levelLabels[level], lx + 16, ly + 6); lx += ctx.measureText(levelLabels[level]).width + 30; });
}

// ─── Кольцевая диаграмма (Donut) ───

function drawDonut(canvasId, legendId, dataObj, colors) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.parentElement;
    const size = Math.min(rect.clientWidth - 40, 220);
    canvas.width = size * dpr; canvas.height = size * dpr;
    canvas.style.width = size + 'px'; canvas.style.height = size + 'px';
    ctx.scale(dpr, dpr);
    const entries = Object.entries(dataObj);
    const total = entries.reduce((s, [, v]) => s + v, 0);
    if (total === 0) return;
    const cx = size / 2, cy = size / 2, outerR = size / 2 - 8, innerR = outerR * 0.6;
    let startAngle = -Math.PI / 2;
    entries.forEach(([, value], i) => { const a = (value / total) * Math.PI * 2; ctx.beginPath(); ctx.arc(cx, cy, outerR, startAngle, startAngle + a); ctx.arc(cx, cy, innerR, startAngle + a, startAngle, true); ctx.closePath(); ctx.fillStyle = colors[i % colors.length]; ctx.fill(); startAngle += a; });
    ctx.fillStyle = '#e2e8f0'; ctx.font = `700 ${Math.round(size * 0.14)}px 'JetBrains Mono', monospace`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(total, cx, cy - 6);
    ctx.fillStyle = '#64748b'; ctx.font = `500 ${Math.round(size * 0.06)}px 'Inter', sans-serif`; ctx.fillText('ВСЕГО', cx, cy + 14);
    const legendEl = document.getElementById(legendId);
    if (legendEl) {
        legendEl.innerHTML = entries.map(([label, value], i) => {
            const pct = Math.round((value / total) * 100);
            return `<div class="legend-item"><span class="legend-color" style="background:${colors[i % colors.length]}"></span><span class="legend-label">${label}</span><span class="legend-value">${value} <span class="legend-pct">(${pct}%)</span></span></div>`;
        }).join('');
    }
}

// ─── Горизонтальная столбчатая диаграмма ───

function drawBarChart(canvasId, dataObj, colors) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const entries = Object.entries(dataObj);
    const maxVal = Math.max(...entries.map(([, v]) => v), 1);
    const barH = 28, gap = 8, labelW = 160, valueW = 50, padTop = 8, padBottom = 8;
    const totalH = padTop + entries.length * (barH + gap) - gap + padBottom;
    const parentW = canvas.parentElement.clientWidth - 40;
    canvas.width = parentW * dpr; canvas.height = totalH * dpr;
    canvas.style.width = parentW + 'px'; canvas.style.height = totalH + 'px';
    ctx.scale(dpr, dpr);
    const barArea = parentW - labelW - valueW - 20;
    entries.forEach(([label, value], i) => {
        const y = padTop + i * (barH + gap); const barW = Math.max((value / maxVal) * barArea, 4);
        ctx.fillStyle = '#94a3b8'; ctx.font = "500 12px 'Inter', sans-serif"; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; ctx.fillText(label, labelW - 10, y + barH / 2);
        ctx.fillStyle = 'rgba(255,255,255,0.03)'; roundRect(ctx, labelW, y, barArea, barH, 4); ctx.fill();
        ctx.fillStyle = colors[i % colors.length]; roundRect(ctx, labelW, y, barW, barH, 4); ctx.fill();
        ctx.fillStyle = '#e2e8f0'; ctx.font = "600 13px 'JetBrains Mono', monospace"; ctx.textAlign = 'left'; ctx.fillText(value, labelW + barArea + 10, y + barH / 2);
    });
}

// ─── Stacked bar (состояние по ДЦ) ───

function drawStackedBar(canvasId, dataObj) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const entries = Object.entries(dataObj);
    const barH = 32, gap = 12, labelW = 140, padTop = 8, padBottom = 8;
    const totalH = padTop + entries.length * (barH + gap) - gap + padBottom;
    const statusColors = { passing: '#10b981', warning: '#f59e0b', critical: '#ef4444' };
    const parentW = canvas.parentElement.clientWidth - 40;
    canvas.width = parentW * dpr; canvas.height = totalH * dpr;
    canvas.style.width = parentW + 'px'; canvas.style.height = totalH + 'px';
    ctx.scale(dpr, dpr);
    const barArea = parentW - labelW - 20;
    entries.forEach(([dc, statuses], i) => {
        const y = padTop + i * (barH + gap); const total = Object.values(statuses).reduce((s, v) => s + v, 0); if (total === 0) return;
        ctx.fillStyle = '#94a3b8'; ctx.font = "500 12px 'Inter', sans-serif"; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; ctx.fillText(dc, labelW - 10, y + barH / 2);
        ctx.fillStyle = 'rgba(255,255,255,0.03)'; roundRect(ctx, labelW, y, barArea, barH, 4); ctx.fill();
        let x = labelW;
        ['passing', 'warning', 'critical'].forEach(status => { const val = statuses[status] || 0; if (val === 0) return; const w = (val / total) * barArea; ctx.fillStyle = statusColors[status]; roundRect(ctx, x, y, w, barH, x === labelW ? 4 : 0); ctx.fill(); if (w > 30) { ctx.fillStyle = '#fff'; ctx.font = "600 11px 'JetBrains Mono', monospace"; ctx.textAlign = 'center'; ctx.fillText(val, x + w / 2, y + barH / 2); } x += w; });
    });
}

// ─── Canvas: скруглённый прямоугольник ───

function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.quadraticCurveTo(x + w, y, x + w, y + r); ctx.lineTo(x + w, y + h - r); ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h); ctx.lineTo(x + r, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - r); ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y); ctx.closePath();
}

// ═══════════════════════════════════════
// Раскрытие / Сворачивание строк
// ═══════════════════════════════════════

function toggleRow(rowId, triggerRow) {
    const expandRow = document.getElementById(rowId);
    if (!expandRow) return;
    const isOpen = expandRow.classList.contains('open');
    const table = triggerRow.closest('table');
    table.querySelectorAll('.expand-content.open').forEach(r => { r.classList.remove('open'); r.previousElementSibling?.classList.remove('expanded'); });
    if (!isOpen) { expandRow.classList.add('open'); triggerRow.classList.add('expanded'); }
}

async function toggleHostRow(rowId, triggerRow, nodeName) {
    const expandRow = document.getElementById(rowId);
    if (!expandRow) return;
    const isOpen = expandRow.classList.contains('open');
    const table = triggerRow.closest('tbody');
    table.querySelectorAll('.nested-expand.open').forEach(r => { r.classList.remove('open'); r.previousElementSibling?.classList.remove('expanded'); });
    if (!isOpen) {
        expandRow.classList.add('open'); triggerRow.classList.add('expanded');
        const bodyEl = document.getElementById(`${rowId}-body`);
        if (bodyEl.dataset.loaded) return;
        bodyEl.dataset.loaded = '1';
        const data = await api(`/api/nodes/${nodeName}`);
        const node = data.node, services = data.services || [], checks = data.checks || [];
        const critCount = checks.filter(c => c.Status === 'critical').length;
        const warnCount = checks.filter(c => c.Status === 'warning').length;
        const passCount = checks.filter(c => c.Status === 'passing').length;

        bodyEl.innerHTML = `
            <div class="expand-tabs">
                <button class="tab active" onclick="event.stopPropagation(); switchExpandTab(this, '${rowId}-overview')">Обзор</button>
                <button class="tab" onclick="event.stopPropagation(); switchExpandTab(this, '${rowId}-services')">Сервисы <span class="tab-count">${services.length}</span></button>
                <button class="tab" onclick="event.stopPropagation(); switchExpandTab(this, '${rowId}-health')">Проверки <span class="tab-count">${checks.length}</span></button>
                <button class="tab" onclick="event.stopPropagation(); switchExpandTab(this, '${rowId}-network')">Сеть</button>
            </div>
            <div class="expand-panel active" id="${rowId}-overview">
                <div class="info-grid">
                    <div class="info-card">
                        <div class="info-card-title">Система</div>
                        <div class="info-row"><span class="info-key">ОС</span><span class="info-value">${node.Meta.os}</span></div>
                        <div class="info-row"><span class="info-key">Ядро</span><span class="info-value">${node.Meta.kernel}</span></div>
                        <div class="info-row"><span class="info-key">CPU</span><span class="info-value">${node.Meta.cpu}</span></div>
                        <div class="info-row"><span class="info-key">RAM</span><span class="info-value">${node.Meta.ram}</span></div>
                        <div class="info-row"><span class="info-key">Диск</span><span class="info-value">${node.Meta.disk}</span></div>
                    </div>
                    <div class="info-card">
                        <div class="info-card-title">Организация</div>
                        <div class="info-row"><span class="info-key">Среда</span><span class="info-value">${node.Meta.environment}</span></div>
                        <div class="info-row"><span class="info-key">Команда</span><span class="info-value">${node.Meta.team}</span></div>
                        <div class="info-row"><span class="info-key">Дата-центр</span><span class="info-value">${node.Datacenter}</span></div>
                        <div class="info-row"><span class="info-key">ИС</span><span class="info-value">${node.Meta.system_name || '-'}</span></div>
                        <div class="info-row"><span class="info-key">ID узла</span><span class="info-value">${node.ID}</span></div>
                    </div>
                    <div class="info-card">
                        <div class="info-card-title">Состояние</div>
                        <div class="info-row"><span class="info-key">Норма</span><span class="info-value" style="color:var(--passing)">${passCount}</span></div>
                        <div class="info-row"><span class="info-key">Предупр.</span><span class="info-value" style="color:var(--warning)">${warnCount}</span></div>
                        <div class="info-row"><span class="info-key">Критич.</span><span class="info-value" style="color:var(--critical)">${critCount}</span></div>
                    </div>
                </div>
            </div>
            <div class="expand-panel" id="${rowId}-services">
                ${services.length === 0 ? '<div class="empty-state"><p>Нет сервисов</p></div>' :
                `<table class="data-table nested-table">
                    <thead><tr><th>Статус</th><th>Сервис</th><th>Порт</th><th>Версия</th><th>Теги</th></tr></thead>
                    <tbody>${services.map(svc => {
                        const svcChecks = checks.filter(c => c.ServiceName === svc.Service);
                        let svcStatus = 'passing';
                        if (svcChecks.some(c => c.Status === 'critical')) svcStatus = 'critical';
                        else if (svcChecks.some(c => c.Status === 'warning')) svcStatus = 'warning';
                        return `<tr><td><span class="status-dot status-${svcStatus}"></span></td><td class="cell-name">${svc.Service}</td><td><span class="port-badge">:${svc.Port}</span></td><td class="cell-mono">${svc.Meta?.version || '-'}</td><td>${svc.Tags.slice(0, 5).map(t => `<span class="tag ${getTagClass(t)}">${t}</span>`).join(' ')}</td></tr>`;
                    }).join('')}</tbody>
                </table>`}
            </div>
            <div class="expand-panel" id="${rowId}-health">
                ${checks.map(c => `<div class="check-item"><div class="check-status ${c.Status}">${statusIcon(c.Status)}</div><div class="check-body"><div class="check-name">${c.Name}</div><div class="check-output">${c.Output}</div></div></div>`).join('')}
            </div>
            <div class="expand-panel" id="${rowId}-network">
                <div class="info-grid">
                    <div class="info-card">
                        <div class="info-card-title">Адреса</div>
                        <div class="info-row"><span class="info-key">LAN</span><span class="info-value">${node.TaggedAddresses?.lan || node.Address}</span></div>
                        <div class="info-row"><span class="info-key">WAN</span><span class="info-value">${node.TaggedAddresses?.wan || '-'}</span></div>
                        <div class="info-row"><span class="info-key">Основной</span><span class="info-value">${node.Address}</span></div>
                    </div>
                    <div class="info-card">
                        <div class="info-card-title">Открытые порты</div>
                        ${services.map(svc => `<div class="info-row"><span class="info-key">${svc.Service}</span><span class="info-value">:${svc.Port}</span></div>`).join('')}
                    </div>
                </div>
            </div>`;
    }
}

function switchExpandTab(tabEl, panelId) {
    const parent = tabEl.closest('.expand-body');
    parent.querySelectorAll('.expand-tabs .tab').forEach(t => t.classList.remove('active'));
    tabEl.classList.add('active');
    parent.querySelectorAll('.expand-panel').forEach(p => p.classList.remove('active'));
    document.getElementById(panelId)?.classList.add('active');
}

// ─── Загрузка индикатора режима ───
async function toggleSystemMonitoring(sysName, isChecked) {
    try {
        await fetch('/api/admin/toggle-monitored-system', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({system_name: sysName})
        });
        // Update card visually
        const cardId = 'sys-card-' + sysName.replace(/[^a-zA-Z0-9]/g, '_');
        const card = document.getElementById(cardId);
        if (card) {
            card.classList.toggle('system-monitored', isChecked);
        }
    } catch(e) {
        console.error('Toggle monitoring error:', e);
    }
}

async function loadModeIndicator() {
    try {
        const data = await api('/api/mode');
        const el = document.getElementById('connectionStatus');
        if (!el) return;
        if (data.mode === 'live') {
            el.innerHTML = '<span class="status-dot status-passing"></span><span>Consul API (боевой)</span>';
        } else {
            el.innerHTML = '<span class="status-dot status-warning"></span><span>Тестовый режим</span>';
        }
    } catch(e) {}
}

// ─── Инициализация ───
document.addEventListener('DOMContentLoaded', () => {
    navigate('dashboard');
    loadModeIndicator();
});
