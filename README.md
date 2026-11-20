# Service Catalog Consul

Каталог сервисов для мониторинга инфраструктуры на базе Consul API.

## Возможности

- **Обзор** — сводка серверов, сервисов, health checks
- **Серверы** — таблица с фильтрами (ИС, ДЦ, Среда, Команда) и раскрывающейся детализацией (система, сервисы, проверки, сеть)
- **Сервисы** — каталог с фильтрами, раскрывающиеся экземпляры с вложенной детализацией хостов
- **Аналитика** — графики: покрытие мониторингом, разбивка по ИС, ДЦ, средам, ОС, командам
- **Админка** — переключение тестовый/боевой режим, управление кластерами Consul, тест подключения
- **Вход через Keycloak** — читать может любой вошедший в realm, менять данные — только роль `catalog-admin`

## Быстрый старт (Docker)

```bash
git clone https://github.com/Destian1995/service_catalog_consul.git
cd service_catalog_consul
cp .env.example .env   # заполнить настройки Keycloak, см. «Вход и роли»
docker compose up -d --build
```

Портал: `http://localhost:8080`
Админка: `http://localhost:8080/admin`

## Конфигурация

Файл `consul_manager_config.json`:

```json
{
    "mode": "test",          // "test" или "live"
    "clusters": {
        "my-cluster": {
            "name": "my-cluster",
            "datacenters": [
                {
                    "name": "dc1",
                    "host": "consul.example.com",
                    "token": "your-consul-token",
                    "scheme": "https",
                    "verify_ssl": true
                }
            ]
        }
    },
    "app": {
        "host": "0.0.0.0",
        "port": 5000
    }
}
```

Пароль админки и `secret_key` в этом файле больше не используются: права даёт роль в Keycloak,
ключ сессий задаётся в `FLASK_SECRET_KEY`.

## Вход и роли (Keycloak)

Все страницы и API требуют входа через Keycloak (OpenID Connect, Authorization Code + PKCE).

| Кто | Что может |
|---|---|
| Любой пользователь, вошедший в realm | Смотреть все разделы, выгружать CSV и inventory, проверять экспортеры, открывать журнал дерегистраций |
| Клиентская роль `catalog-admin` | Дополнительно: админка, режим test/live, кластеры, дерегистрация узлов, владельцы, исключения и отметки ИС, правила SLA, карта архитектуры |

Права проверяет сервер. Любой изменяющий запрос (POST, PUT, PATCH, DELETE) по умолчанию доступен только
администратору, поэтому новый маршрут без доработки `auth.py` тоже будет закрыт для читателей.
Исключения — `READ_ONLY_POST_ENDPOINTS` в `auth.py`: проверка доступности экспортеров.
Изменяющий запрос с чужого сайта (заголовок `Origin` не совпадает с `PUBLIC_URL`) отклоняется.
Читателю интерфейс не показывает кнопки изменения. В истории изменений и журнале дерегистраций
сохраняется логин пользователя.

### Клиент в Keycloak

1. Clients → Create client: тип OpenID Connect, Client ID `service-catalog`.
2. Capability config: Client authentication — On, Authentication flow — только Standard flow.
3. Login settings: Valid redirect URIs — `https://<адрес портала>/auth/callback`,
   Valid post logout redirect URIs — `https://<адрес портала>/`, Web origins — `https://<адрес портала>`.
4. Advanced → Proof Key for Code Exchange Code Challenge Method — `S256`.
5. Roles → Create role `catalog-admin`. Выдать её администраторам: Users или Groups → Role mapping →
   Assign role → фильтр по клиентам → `service-catalog` `catalog-admin`. Удобнее через группу.
6. Credentials → Client secret скопировать в `KEYCLOAK_CLIENT_SECRET`.

Клиентская роль попадает в access token через стандартный client scope `roles`; убирать его из клиента нельзя.
Роли читаются при входе. Выданная или отозванная роль начинает действовать при следующем входе,
не позже чем через `SESSION_LIFETIME_HOURS`.

### Переменные окружения

Шаблон — `.env.example`. Файл `.env` в git и образ не попадает.

| Переменная | Назначение |
|---|---|
| `AUTH_MODE` | `keycloak` (по умолчанию) или `disabled` — без входа, для локальной разработки |
| `KEYCLOAK_ISSUER` | Адрес realm, например `https://keycloak-sbx.k8s.aton.global/realms/appsoft` |
| `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_CLIENT_SECRET` | Клиент из шагов выше |
| `KEYCLOAK_ADMIN_ROLE` | Клиентская роль администратора, по умолчанию `catalog-admin` |
| `KEYCLOAK_CA_BUNDLE` | PEM корневого сертификата, которым подписан Keycloak. Цепочка `keycloak-sbx` заканчивается корпоративным корнем Security Administrator; он лежит в `certs/` и попадает в образ, путь — `/app/certs/aton-security-administrator-root-ca.crt` |
| `PUBLIC_URL` | Внешний адрес портала без слэша в конце; из него строятся `/auth/callback` и адрес возврата после выхода |
| `FLASK_SECRET_KEY` | Случайная строка от 32 символов, одинаковая для всех воркеров Gunicorn |
| `SESSION_LIFETIME_HOURS` | Срок сессии портала, по умолчанию 8 |
| `SESSION_COOKIE_SECURE` | `true` — cookie только по HTTPS. Если портал открыт по HTTP, браузер не сохранит такую cookie и вход зациклится; `false` допустим только для локальной проверки |

Без полного набора переменных в режиме `keycloak` приложение не стартует и пишет, чего не хватает.

## Локальная разработка

```bash
pip install -r requirements.txt
AUTH_MODE=disabled python app.py
```

`AUTH_MODE=disabled` отключает вход и даёт всем права администратора. В рабочем окружении его не включать.

## Стек

- Python / Flask / Gunicorn
- Vanilla JS (SPA, Canvas-графики)
- Nginx (reverse proxy)
- Docker Compose

## Журнал дерегистраций

Блок «Что сейчас отслеживается» показывает режим портала, настроенные адреса Consul
по кластерам/DC и подключённые файлы логов. Адреса из записей HTTP-прокси показаны
отдельно как исторические наблюдения; это не проверка текущей конфигурации или доступности сервера.

Поле «Узел» фильтрует журнал по части имени. После «Применить» выбранное имя и остальные
условия показаны в «Применённых фильтрах». Автообновление и пагинация используют эти условия,
пока пользователь не нажмёт «Применить» снова. Это фильтр записей, а не запуск сбора с хоста;
записи без имени узла под такой фильтр не попадут.

Раздел `/deregistrations` открывается из меню каталога. История хранится в SQLite
(`CONSUL_JOURNAL_DB`, по умолчанию `data/deregistrations.sqlite3`); Docker Compose
сохраняет её в отдельном volume `catalog-journal`. Журнал не очищается автоматически.
Для резервного копирования работающей SQLite используйте SQLite backup API, а не копирование одного файла без WAL.

Фиксируются реальные попытки `PUT /v1/catalog/deregister`, отправленные порталом,
их HTTP-результат или ошибка соединения. Тестовый режим не отправляет команды и не создаёт фиктивные записи.
Если запись попытки невозможна, отправка блокируется. При сбое после отправки результат может остаться неизвестным.
IP соединения и X-Forwarded-For хранятся отдельно; forwarded-заголовок не подтверждает личность.
Для команд портала сохраняется логин пользователя Keycloak, отправившего команду.

### Внешние команды: подключение HTTP-логов

Портал не перехватывает чужие соединения к Consul. Для внешних команд нужен один из источников:

1. Consul Enterprise HTTP audit logs в формате JSONL, по одному JSON-объекту на строку.
   Подключите файлы всех нужных агентов/серверов read-only и укажите glob-паттерны:

   ```yaml
   services:
     catalog-app:
       environment:
         CONSUL_AUDIT_FILES: '["/audit/server-0/*.json", "/audit/server-1/*.json", "/audit/server-2/*.json"]'
       volumes:
         - ./consul-audit:/audit:ro
   ```

   Встроенный аудит требует Enterprise и не охватывает внутренние RPC.
   Документация: https://developer.hashicorp.com/consul/docs/monitor/log/audit
   Включение аудита на действующих серверах эта доработка не выполняет.

2. Для Consul без Enterprise подготовлен опциональный HTTP-прокси Nginx:
   `docker-compose.audit.yml` и `nginx-consul-audit.conf.template`.
   Задайте `CONSUL_UPSTREAM` равным настоящему HTTP(S) endpoint Consul, затем запустите:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.audit.yml up -d --build
   ```

   Прокси доступен локально на `127.0.0.1:18500`, в сети Compose — `consul-http-audit:8500`.
   Для удалённых клиентов отдельно настройте защищённый listener/маршрут.
   Клиенты, которые должны попадать в журнал, должны обращаться через этот прокси;
   прямые запросы к серверам и внутренний RPC останутся невидимыми.
   Токены передаются Consul, но не записываются в access log. Тела запросов и query string не логируются.
   Для HTTPS upstream проверяется сертификат; для внутреннего CA подключите его trust bundle
   и настройте `proxy_ssl_trusted_certificate`. Локальный HTTP listener не заменяет защищённый API вход.
   Прокси автоматически не подменяет существующие адреса клиентов и не развёрнут этой правкой.

Импорт выполняется каждые 10 секунд и при открытии журнала. Курсоры чтения хранятся в SQLite,
повторное чтение и ротация с переименованием не создают повторные записи с тем же request ID.
Один проход ограничен 5000 строками и 4 MiB на файл, одна строка — 1 MiB.
Сохраняйте ротированные файлы до их импорта; сжатые и многострочные JSON-файлы не поддерживаются.
На странице видны файлы, смещения чтения, ошибки и число некорректных строк.

В журнал попадают только PUT catalog/deregister и agent/service|check/deregister.
HTTP 2xx — ответ на запрос, а не гарантия постоянного удаления: anti-entropy может вернуть сервис.
Если тело запроса отсутствует в аудите, узел для catalog/deregister неизвестен; поиск по имени узла
такую запись не найдёт. Для расследования используйте также время, адрес, endpoint и ACL accessor ID.
Секрет токена, тела запросов и исходные audit-документы не сохраняются.
Записи портала и внешнего источника не объединяются эвристически и могут описывать один запрос.
Страница и API журнала доступны любому вошедшему пользователю; дерегистрировать узлы может только администратор.

Проверки: `python -m unittest test_auth test_deregistration_journal -v` и `node test_deregistration_filters.js`.
