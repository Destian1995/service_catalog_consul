# Service Catalog Consul

Каталог сервисов для мониторинга инфраструктуры на базе Consul API.

## Возможности

- **Обзор** — сводка серверов, сервисов, health checks
- **Серверы** — таблица с фильтрами (ИС, ДЦ, Среда, Команда) и раскрывающейся детализацией (система, сервисы, проверки, сеть)
- **Сервисы** — каталог с фильтрами, раскрывающиеся экземпляры с вложенной детализацией хостов
- **Аналитика** — графики: покрытие мониторингом, разбивка по ИС, ДЦ, средам, ОС, командам
- **Админка** — переключение тестовый/боевой режим, управление кластерами Consul, тест подключения

## Быстрый старт (Docker)

```bash
git clone https://github.com/Destian1995/service_catalog_consul.git
cd service_catalog_consul
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
        "port": 5000,
        "secret_key": "change-me",
        "admin_password": "admin"
    }
}
```

## Локальная разработка

```bash
pip install -r requirements.txt
python app.py
```

## Стек

- Python / Flask / Gunicorn
- Vanilla JS (SPA, Canvas-графики)
- Nginx (reverse proxy)
- Docker Compose

## Журнал дерегистраций

Раздел `/deregistrations` открывается из меню каталога. История хранится в SQLite
(`CONSUL_JOURNAL_DB`, по умолчанию `data/deregistrations.sqlite3`); Docker Compose
сохраняет её в отдельном volume `catalog-journal`. Журнал не очищается автоматически.
Для резервного копирования работающей SQLite используйте SQLite backup API, а не копирование одного файла без WAL.

Фиксируются реальные попытки `PUT /v1/catalog/deregister`, отправленные порталом,
их HTTP-результат или ошибка соединения. Тестовый режим не отправляет команды и не создаёт фиктивные записи.
Если запись попытки невозможна, отправка блокируется. При сбое после отправки результат может остаться неизвестным.
IP соединения и X-Forwarded-For хранятся отдельно; forwarded-заголовок не подтверждает личность.

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
Доступ к странице и API наследует существующие правила доступа портала; публикуйте его за вашим контролем доступа.

Проверки: `python -m unittest test_deregistration_journal -v`.
