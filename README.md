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
