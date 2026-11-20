/* Only the catalog owner may manage permissions; the API enforces this too. */
(() => {
  'use strict';
  let users = [];
  let saving = false;
  const rows = document.getElementById('usersRows');
  const status = document.getElementById('usersStatus');
  const search = document.getElementById('usersSearch');
  const refresh = document.getElementById('usersRefresh');

  function message(text, error = false) {
    status.textContent = text;
    status.dataset.error = String(error);
  }

  function cell(row, text) {
    const td = document.createElement('td');
    td.textContent = text;
    row.append(td);
    return td;
  }

  function render() {
    rows.replaceChildren();
    const query = search.value.trim().toLocaleLowerCase();
    const filtered = users.filter(user => [user.name, user.login].some(value => (value || '').toLocaleLowerCase().includes(query)));
    for (const user of filtered) {
      const row = document.createElement('tr');
      cell(row, user.name || user.login);
      const identity = cell(row, user.login);
      const subject = document.createElement('code');
      subject.className = 'users-id';
      subject.textContent = user.subject;
      identity.append(subject);
      cell(row, user.role === 'admin' ? 'Администратор' : 'Обычный пользователь');
      cell(row, new Date(user.last_seen).toLocaleString('ru-RU'));
      const action = cell(row, '');
      if (user.is_owner) {
        action.textContent = 'Владелец каталога';
      } else {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn-export';
        button.textContent = user.role === 'admin' ? 'Снять права администратора' : 'Назначить администратором';
        button.disabled = saving;
        button.addEventListener('click', () => changeRole(user));
        action.append(button);
      }
      rows.append(row);
    }
    if (!filtered.length) {
      const row = document.createElement('tr');
      const empty = cell(row, 'Пользователи не найдены');
      empty.colSpan = 5;
      rows.append(row);
    }
  }

  async function load() {
    refresh.disabled = true;
    message('Загрузка пользователей…');
    try {
      const response = await fetch('/api/admin/users', {cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Не удалось загрузить пользователей');
      users = data.users;
      render();
      message('Пользователей: ' + users.length);
    } catch (error) {
      message(error.message || 'Ошибка соединения', true);
    } finally {
      refresh.disabled = false;
    }
  }

  async function changeRole(user) {
    if (saving) return;
    const role = user.role === 'admin' ? 'user' : 'admin';
    const action = role === 'admin' ? 'Разрешить изменение данных каталога' : 'Оставить только просмотр';
    if (!confirm(action + ' для пользователя «' + (user.name || user.login) + '»?')) return;
    saving = true;
    refresh.disabled = true;
    render();
    try {
      const response = await fetch('/api/admin/users/' + encodeURIComponent(user.subject) + '/role', {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({role}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Не удалось сохранить права');
      users = users.map(item => item.subject === data.user.subject ? data.user : item);
      message('Права пользователя «' + (user.name || user.login) + '» сохранены.');
    } catch (error) {
      message(error.message || 'Ошибка соединения', true);
    } finally {
      saving = false;
      refresh.disabled = false;
      render();
    }
  }

  search.addEventListener('input', render);
  refresh.addEventListener('click', load);
  window.addEventListener('catalog-permissions-changed', () => {
    if (!window.catalogAuth.user?.can_manage_users) location.replace('/');
  });
  window.catalogAuth.ready.then(user => {
    if (!user || !user.can_manage_users) { location.replace('/'); return; }
    document.getElementById('usersPage').hidden = false;
    load();
  });
})();
