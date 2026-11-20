/* Вход через Keycloak: текущий пользователь, роль и реакция на 401/403.
   Права проверяет сервер. Скрипт только скрывает недоступные читателю действия
   (элементы с классом admin-only) и отправляет на вход, когда сессия закончилась. */
(() => {
  'use strict';
  const nativeFetch = window.fetch.bind(window);
  let redirecting = false;
  let lastNotice = 0;

  function toLogin() {
    if (redirecting) return;
    redirecting = true;
    const next = location.pathname + location.search;
    location.href = '/auth/login?next=' + encodeURIComponent(next);
  }

  function notify(text) {
    const now = Date.now();
    if (now - lastNotice < 1500 || !document.body) return;
    lastNotice = now;
    const box = document.createElement('div');
    box.setAttribute('role', 'alert');
    box.textContent = text;
    Object.assign(box.style, {
      position: 'fixed', right: '24px', bottom: '24px', zIndex: '1000', maxWidth: '420px',
      padding: '12px 18px', borderRadius: '8px', font: '500 13px/1.45 system-ui, sans-serif',
      background: '#462434', color: '#fda4af', border: '1px solid #7f3b4d',
    });
    document.body.appendChild(box);
    setTimeout(() => box.remove(), 4000);
  }

  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    if (response.status === 401) toLogin();
    else if (response.status === 403) {
      await auth.refresh();
      notify('Недостаточно прав для этого действия.');
    }
    return response;
  };

  function renderBadge(user) {
    const slot = document.getElementById('userBadge');
    if (!slot || !user) return;
    slot.replaceChildren();
    const name = document.createElement('span');
    name.className = 'user-badge-name';
    name.textContent = user.name || user.login || 'Пользователь';
    name.title = user.login || '';
    const role = document.createElement('span');
    role.className = 'user-badge-role';
    role.textContent = user.is_admin ? 'администратор' : 'только чтение';
    slot.append(name, role);
    if (user.logout_url) {
      const logout = document.createElement('a');
      logout.className = 'user-badge-logout';
      logout.href = user.logout_url;
      logout.textContent = 'Выйти';
      slot.append(logout);
    }
  }

  const auth = {
    user: null,
    isAdmin: () => !!(auth.user && auth.user.is_admin),
    ready: null,
  };
  function applyUser(user) {
    const previous = auth.user;
    auth.user = user;
    document.documentElement.classList.toggle('role-admin', auth.isAdmin());
    document.documentElement.classList.toggle('can-manage-users', !!(user && user.can_manage_users));
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => renderBadge(user), {once: true});
    } else {
      renderBadge(user);
    }
    if (previous && user && (previous.is_admin !== user.is_admin || previous.can_manage_users !== user.can_manage_users)) {
      window.dispatchEvent(new CustomEvent('catalog-permissions-changed'));
    }
    return user;
  }
  auth.refresh = () => nativeFetch('/api/me', {cache: 'no-store', credentials: 'same-origin'})
    .then(response => {
      if (response.status === 401) { toLogin(); return null; }
      return response.ok ? response.json() : null;
    })
    .then(user => user ? applyUser(user) : null)
    .catch(() => null);
  auth.ready = auth.refresh();
  window.addEventListener('focus', () => auth.refresh());
  window.catalogAuth = auth;
})();
