# AI-планировщик путешествий

## Запуск всех сервисов

Скопируйте `.env.example` в `.env`, при необходимости замените пароли и токены, затем запустите:

```sh
docker compose up --build -d
docker compose ps
```

Приложение доступно через Caddy по адресу `http://localhost:3000`: страницы и `/api/v1/*` работают на одном адресе. Backend также доступен напрямую по адресу `http://localhost:8000`, Data Layer — `http://localhost:8080/health`. Панель RabbitMQ: `http://localhost:15672`.

Compose запускает MongoDB и RabbitMQ с постоянными томами, затем Backend, Data Layer, agent-worker, frontend и Caddy. Worker подключается к Backend и RabbitMQ по внутренним именам сервисов; Caddy направляет `/api/v1/*` в Backend, остальные запросы — во frontend. Порт `BACKEND_PORT` меняет только прямой доступ с хоста и не влияет на маршрутизацию через Caddy. По умолчанию `AGENT_MODE=demo` и `DATA_MODE=synthetic`, поэтому для первого запуска внешние API-ключи не нужны. В demo-режиме worker использует встроенные демонстрационные данные; при `AGENT_MODE=openai` он обращается к Data Layer.

Для реального агента задайте в `.env` `AGENT_MODE=openai`, `OPENAI_API_KEY` и `PARSER_SERVICE_TOKEN`. Режим данных выбирается через `DATA_MODE`; ключи внешних источников указываются в том же файле. Если меняете `FRONTEND_PORT`, обновите `CORS_ORIGINS` для нового адреса.
`DATA_MODE` режим источников: `sandbox` `live` `synthetic`

Остановить стек без удаления данных:

```sh
docker compose down
```
