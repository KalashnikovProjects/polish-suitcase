# AI-планировщик путешествий

## Запуск всех сервисов

Скопируйте `.env.example` в `.env`, при необходимости замените пароли и токены, затем запустите:

```sh
docker compose up --build -d
docker compose ps
```

Frontend доступен по адресу `http://localhost:3000`, Backend — `http://localhost:8000`, Data Layer — `http://localhost:8080/health`. Панель RabbitMQ: `http://localhost:15672`.

Compose запускает MongoDB и RabbitMQ с постоянными томами, затем Backend, Data Layer, agent-worker и frontend. Worker подключается к Backend и RabbitMQ по внутренним именам сервисов; frontend проксирует `/api/v1/*` на Backend. По умолчанию `AGENT_MODE=demo` и `DATA_MODE=synthetic`, поэтому для первого запуска внешние API-ключи не нужны. В demo-режиме worker использует встроенные демонстрационные данные; при `AGENT_MODE=openai` он обращается к Data Layer.

Для реального агента задайте в `.env` `AGENT_MODE=openai`, `OPENAI_API_KEY` и `PARSER_SERVICE_TOKEN`. Режим данных выбирается через `DATA_MODE`; ключи внешних источников указываются в том же файле. Если меняете `FRONTEND_PORT`, обновите `CORS_ORIGINS` для нового адреса.

Остановить стек без удаления данных:

```sh
docker compose down
```
